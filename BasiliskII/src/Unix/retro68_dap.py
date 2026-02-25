#!/usr/bin/env python3
"""
Custom DAP adapter for Nova + BasiliskII debugging.

Speaks DAP protocol on stdin/stdout to Nova, and GDB remote protocol
over TCP to BasiliskII's GDB stub. Replaces both debug_wrapper.py and GDB.

Architecture:
    Nova IDE  <--DAP (stdin/stdout)-->  retro68_dap.py  <--GDB remote (TCP)-->  BasiliskII GDB stub

Orchestration flow:
1. Build, start BasiliskII, connect to stub early (CPU pauses on connect)
2. Enter DAP loop immediately — Nova sends attach/setBreakpoints/configurationDone
3. Breakpoints sent as VMAs to stub (stored, resolved at _LoadSeg time)
4. configurationDone resumes CPU — boot continues
5. User launches app manually in the emulator
6. Stub resolves VMA breakpoints at _LoadSeg time using fresh JT scan
   (local variables, not shared state — immune to multi-app clobbering)
7. Breakpoint fires → S05 → adapter discovers segments, reports to Nova
"""

import argparse
import json
import os
import re
import select
import socket
import struct
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------------------
# Logging  (everything goes to /tmp/dap_traffic.log, never stdout)
# ---------------------------------------------------------------------------

LOG_PATH = "/tmp/dap_traffic.log"
_log_file = None

def _open_log():
    global _log_file
    if _log_file is None:
        _log_file = open(LOG_PATH, "w")

def log(msg):
    _open_log()
    _log_file.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    _log_file.flush()

def status(msg):
    """Write status to stderr (shown in Nova's output) AND to the log."""
    print(msg, file=sys.stderr, flush=True)
    log(f"[status] {msg}")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUTOMATION_SOCKET = "basilisk_auto"
GDB_PORT_DEFAULT = 9999

# Classic Mac OS low-memory global: CurrentA5
CURRENT_A5_ADDR = 0x904

# m68k register layout: d0-d7 (0-7), a0-a7 (8-15), pc (16), sr (17)
REG_NAMES = (
    ["d0", "d1", "d2", "d3", "d4", "d5", "d6", "d7"] +
    ["a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7/sp"] +
    ["pc", "sr"]
)

# ---------------------------------------------------------------------------
# DAP Protocol Layer  (stdin / stdout)
# ---------------------------------------------------------------------------

class DAPIO:
    """Read/write DAP messages on stdin/stdout using Content-Length framing."""

    def __init__(self):
        self._stdin = sys.stdin.buffer
        self._stdout = sys.stdout.buffer
        self._seq = 0

    def read_message(self):
        """Read one DAP message. Returns parsed JSON dict or None on EOF."""
        headers = {}
        while True:
            line = self._stdin.readline()
            if not line:
                return None
            line = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == "":
                break
            if ":" in line:
                key, val = line.split(":", 1)
                headers[key.strip()] = val.strip()
        content_length = int(headers.get("Content-Length", 0))
        if content_length == 0:
            return None
        body = self._stdin.read(content_length)
        if not body:
            return None
        msg = json.loads(body.decode("utf-8"))
        log(f"<-- DAP recv: {json.dumps(msg)}")
        return msg

    def send_message(self, msg):
        """Send a DAP message (dict) to stdout."""
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
        self._stdout.write(header + body)
        self._stdout.flush()
        log(f"--> DAP send: {json.dumps(msg)}")

    def next_seq(self):
        self._seq += 1
        return self._seq

    def send_response(self, request, body=None, success=True, message=None):
        resp = {
            "seq": self.next_seq(),
            "type": "response",
            "request_seq": request["seq"],
            "command": request["command"],
            "success": success,
        }
        if body is not None:
            resp["body"] = body
        if message is not None:
            resp["message"] = message
        self.send_message(resp)

    def send_event(self, event, body=None):
        msg = {
            "seq": self.next_seq(),
            "type": "event",
            "event": event,
        }
        if body is not None:
            msg["body"] = body
        self.send_message(msg)

# ---------------------------------------------------------------------------
# GDB Remote Protocol Layer  (TCP socket)
# ---------------------------------------------------------------------------

class GDBRemote:
    """Speak GDB remote serial protocol over TCP."""

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self.no_ack = False

    def connect(self, timeout=30):
        """Connect to stub, retrying until timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(5)
                s.connect((self.host, self.port))
                s.settimeout(None)
                self.sock = s
                log(f"GDB connected to {self.host}:{self.port}")
                return True
            except (ConnectionRefusedError, OSError) as e:
                log(f"GDB connect attempt failed: {e}")
                time.sleep(0.5)
        return False

    def fileno(self):
        return self.sock.fileno() if self.sock else -1

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def send_packet(self, payload):
        """Send a GDB remote packet: $payload#checksum"""
        csum = sum(ord(c) if isinstance(c, str) else c for c in payload) & 0xFF
        pkt = f"${payload}#{csum:02x}"
        self.sock.sendall(pkt.encode("ascii"))
        log(f"--> GDB send: {pkt}")
        if not self.no_ack:
            ack = self.sock.recv(1)
            if ack != b"+":
                log(f"GDB: expected +, got {ack!r}")

    def recv_packet(self, timeout=None):
        """Receive one GDB remote packet. Returns payload string or None."""
        if self.sock is None:
            return None
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            return self._recv_packet_inner()
        except socket.timeout:
            return None
        finally:
            if timeout is not None:
                self.sock.settimeout(None)

    def _recv_packet_inner(self):
        """Inner packet receive logic."""
        # Wait for $
        while True:
            b = self.sock.recv(1)
            if not b:
                return None
            c = b[0]
            if c == ord("$"):
                break
            if c == ord("+") or c == ord("-"):
                continue

        # Read payload until #
        payload = bytearray()
        while True:
            b = self.sock.recv(1)
            if not b:
                return None
            if b[0] == ord("#"):
                break
            payload.append(b[0])

        # Read 2-digit checksum
        cs = self.sock.recv(2)
        if not cs or len(cs) < 2:
            return None

        result = payload.decode("ascii")
        log(f"<-- GDB recv: ${result}#{cs.decode('ascii')}")

        if not self.no_ack:
            self.sock.sendall(b"+")

        return result

    def send_break(self):
        """Send Ctrl-C (0x03) to interrupt the stub."""
        if self.sock:
            self.sock.sendall(b"\x03")
            log("--> GDB send: <break>")

    def negotiate(self):
        """Initial handshake: consume initial S05, qSupported, no-ack mode."""
        initial = self.recv_packet(timeout=10)
        log(f"Initial stop reply (consumed): {initial}")

        self.send_packet("qSupported:multiprocess+;swbreak+;hwbreak+")
        resp = self.recv_packet(timeout=5)
        log(f"qSupported response: {resp}")

        self.send_packet("QStartNoAckMode")
        resp = self.recv_packet(timeout=5)
        if resp == "OK":
            self.no_ack = True
            log("No-ack mode enabled")

    def read_registers(self):
        """Read all registers via 'g' command. Returns list of 18 uint32 values."""
        self.send_packet("g")
        resp = self.recv_packet(timeout=5)
        if not resp or len(resp) < 18 * 8:
            return None
        regs = []
        for i in range(18):
            hex_val = resp[i * 8:(i + 1) * 8]
            regs.append(int(hex_val, 16))
        return regs

    def read_memory(self, addr, length):
        """Read memory. Returns bytes or None."""
        self.send_packet(f"m{addr:x},{length:x}")
        resp = self.recv_packet(timeout=5)
        if not resp or resp.startswith("E"):
            return None
        try:
            return bytes.fromhex(resp)
        except ValueError:
            return None

    def read_u32(self, addr):
        """Read a big-endian uint32 from memory."""
        data = self.read_memory(addr, 4)
        if data and len(data) == 4:
            return struct.unpack(">I", data)[0]
        return None

# ---------------------------------------------------------------------------
# DWARF Line Table  (source:line -> VMA)
# ---------------------------------------------------------------------------

class DWARFLineTable:
    """Parse objdump --dwarf=decodedline output to map source:line -> VMA."""

    def __init__(self):
        self.line_to_vma = {}   # {(basename, line_num): vma}
        self.vma_to_line = {}   # {vma: (basename, line_num)}

    def load(self, objdump_path, symbol_file):
        """Run objdump and parse the decoded line table."""
        cmd = [objdump_path, "--dwarf=decodedline", symbol_file]
        log(f"Running: {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            log(f"objdump error: {e}")
            return

        if result.returncode != 0:
            log(f"objdump failed: {result.stderr}")
            return

        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            filename = parts[0]
            if parts[1] == "-":
                continue
            try:
                line_num = int(parts[1])
            except ValueError:
                continue
            addr_str = parts[2].lstrip("0x") or "0"
            try:
                vma = int(addr_str, 16)
            except ValueError:
                continue

            basename = os.path.basename(filename)
            key = (basename, line_num)
            if key not in self.line_to_vma:
                self.line_to_vma[key] = vma
            if vma not in self.vma_to_line:
                self.vma_to_line[vma] = (basename, line_num)

        log(f"DWARF: {len(self.line_to_vma)} line->vma, "
            f"{len(self.vma_to_line)} vma->line")

    def get_vma(self, source_path, line_num):
        basename = os.path.basename(source_path)
        key = (basename, line_num)
        result = self.line_to_vma.get(key)
        if result is None:
            # Debug: show what keys ARE in the table for this file
            matching_keys = [k for k in self.line_to_vma.keys() if k[0] == basename]
            if matching_keys:
                log(f"get_vma({basename}:{line_num}): not found. Available lines for {basename}: {sorted([k[1] for k in matching_keys[:10]])}")
            else:
                log(f"get_vma({basename}:{line_num}): no entries for file {basename}. Known files: {set(k[0] for k in self.line_to_vma.keys())}")
        return result

    def get_source_line(self, vma):
        if vma in self.vma_to_line:
            return self.vma_to_line[vma]
        # Closest VMA <= requested
        best_vma = None
        for candidate_vma in self.vma_to_line:
            if candidate_vma <= vma:
                if best_vma is None or candidate_vma > best_vma:
                    best_vma = candidate_vma
        if best_vma is not None:
            return self.vma_to_line[best_vma]
        return None


# ---------------------------------------------------------------------------
# Segment Translation  (VMA <-> runtime address)
# ---------------------------------------------------------------------------

class SegmentMap:
    """Parse .segments file and translate between VMA and runtime addresses."""

    def __init__(self):
        self.segments = {}       # {seg_num: {vma_start, vma_end, ref_func_vma}}
        self.runtime_bases = {}  # {seg_num: runtime_base_address}

    def load(self, segments_file):
        if not segments_file or not os.path.exists(segments_file):
            log(f"Segments file not found: {segments_file}")
            return
        with open(segments_file) as f:
            for line in f:
                line = line.strip()
                m = re.match(r"SEGMENT\s+(\d+)\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)", line)
                if m:
                    seg = int(m.group(1))
                    self.segments.setdefault(seg, {})
                    self.segments[seg]["vma_start"] = int(m.group(2), 16)
                    self.segments[seg]["vma_end"] = int(m.group(3), 16)
                    continue
                m = re.match(r"REF_FUNC\s+(\d+)\s+0x([0-9a-fA-F]+)", line)
                if m:
                    seg = int(m.group(1))
                    self.segments.setdefault(seg, {})
                    self.segments[seg]["ref_func_vma"] = int(m.group(2), 16)
        log(f"Segments: loaded {len(self.segments)} segments")
        for seg_num, info in sorted(self.segments.items()):
            ref = info.get("ref_func_vma")
            log(f"  seg {seg_num}: VMA 0x{info.get('vma_start',0):08x}-"
                f"0x{info.get('vma_end',0):08x}"
                + (f" ref=0x{ref:08x}" if ref is not None else ""))

    def _query_stub_segment_bases(self, gdb):
        """Query the GDB stub for segment bases using qSegmentBases packet.

        Response format: seg:1:0x...:2:0x...; or seg:none if no segments loaded.
        Returns True if successfully queried and parsed.
        """
        try:
            if gdb.sock is None:
                return False
            gdb.send_packet("qSegmentBases")
            response = gdb.recv_packet(timeout=0.5)
            if not response or not response.startswith("seg"):
                log(f"query_stub_segment_bases: unexpected response: {response!r}")
                return False

            if response == "seg:none":
                log("query_stub_segment_bases: no segments loaded on stub yet")
                return False

            # Parse response: seg:1:0x002b6000;2:0x002b8000;...
            self.runtime_bases = {}
            parts = response[4:].split(";")  # Skip "seg:" prefix
            for part in parts:
                if not part:
                    continue
                try:
                    seg_str, addr_str = part.split(":")
                    seg_num = int(seg_str)
                    base_addr = int(addr_str, 16)
                    self.runtime_bases[seg_num] = base_addr
                    log(f"  seg {seg_num}: base=0x{base_addr:08x} (from stub)")
                except (ValueError, IndexError):
                    log(f"Failed to parse segment entry: {part}")
                    return False

            return len(self.runtime_bases) > 0
        except Exception as e:
            log(f"query_stub_segment_bases failed: {e}")
            return False

    def find_segment_for_vma(self, vma):
        for seg_num, info in self.segments.items():
            if info.get("vma_start", 0) <= vma < info.get("vma_end", 0):
                return seg_num
        return 0

    def discover_segments(self, gdb, expected_seg_nums=None):
        """Discover segment runtime bases from the GDB stub.

        First tries to query the stub directly (most reliable).
        Falls back to reverse-engineering from jump table if query fails.

        If expected_seg_nums is provided, validates that segments are found.
        Returns True if segments were successfully discovered.
        """
        # Try to query stub for segment bases (most reliable) - safely
        try:
            if self._query_stub_segment_bases(gdb):
                log("discover_segments: got bases from stub query")
                return True
        except Exception as e:
            log(f"discover_segments: stub query failed ({e}), trying JT")

        # Fallback: reverse-engineer from jump table
        log("discover_segments: using JT scanning")
        app_a5 = gdb.read_u32(CURRENT_A5_ADDR)
        if not app_a5 or app_a5 == 0:
            log("discover_segments: CurrentA5 is 0")
            return False

        log(f"discover_segments: CurrentA5=0x{app_a5:08x}")

        # Bulk-read JT: A5+0x20 .. A5+0x800 (252 entries * 8 bytes)
        jt_size = 0x800 - 0x20
        jt_data = gdb.read_memory(app_a5 + 0x20, jt_size)
        if not jt_data:
            log("discover_segments: failed to read jump table")
            return False

        # Scan JT entries.  Each entry is 8 bytes.
        # LOADED:   seg_num(u16) + 0x4EF9(u16) + runtime_addr(u32)
        # UNLOADED: offset(u16)  + 0x3F3C(u16) + seg_num(u16) + 0xA9F0(u16)
        all_funcs_per_seg = {}  # {seg_num: [runtime_addrs]}
        jt_seg_nums = set()
        for i in range(0, len(jt_data) - 7, 8):
            opcode = struct.unpack_from(">H", jt_data, i + 2)[0]

            if opcode == 0x4EF9:  # JMP -- loaded segment
                seg_num = struct.unpack_from(">H", jt_data, i)[0]
                func_addr = struct.unpack_from(">I", jt_data, i + 4)[0]
                if seg_num == 0 or seg_num > 15:
                    continue
                jt_seg_nums.add(seg_num)
                if seg_num not in all_funcs_per_seg:
                    all_funcs_per_seg[seg_num] = []
                all_funcs_per_seg[seg_num].append(func_addr)
            elif opcode == 0x3F3C:  # MOVE.W #seg,-(SP) -- unloaded segment
                seg_num = struct.unpack_from(">H", jt_data, i + 4)[0]
                if seg_num > 0 and seg_num <= 15:
                    jt_seg_nums.add(seg_num)
            else:
                word0 = struct.unpack_from(">H", jt_data, i)[0]
                if word0 == 0 and opcode == 0:
                    continue  # skip empty entries
                break  # stop scanning on unknown data

        log(f"discover_segments: JT segments: {sorted(jt_seg_nums)}")

        # Validate against expected segments
        if expected_seg_nums is not None:
            if not expected_seg_nums.issubset(jt_seg_nums):
                missing = expected_seg_nums - jt_seg_nums
                log(f"discover_segments: not our app (missing {sorted(missing)})")
                return False
            log("discover_segments: validation passed — this is our app")

        # Calculate runtime bases for loaded segments.
        # For segments with a reference function, all JT entries for that segment
        # should calculate the same base. Use the median of all calculated bases.
        self.runtime_bases = {}
        for seg_num, func_addrs in all_funcs_per_seg.items():
            seg_info = self.segments.get(seg_num, {})
            ref_vma = seg_info.get("ref_func_vma")
            seg_vma_start = seg_info.get("vma_start", 0)

            if ref_vma is not None:
                ref_offset = ref_vma - seg_vma_start
                # Calculate base for each function address
                bases = [addr - ref_offset for addr in func_addrs]
                # Use median base (most consistent value)
                bases.sort()
                median_base = bases[len(bases) // 2]
                self.runtime_bases[seg_num] = median_base
                log(f"  seg {seg_num}: base=0x{median_base:08x} (median of {len(bases)} calculations)")
                if len(set(bases)) > 1:
                    log(f"    (range: 0x{min(bases):08x} - 0x{max(bases):08x}, spread: {max(bases)-min(bases)} bytes)")
            elif func_addrs:
                self.runtime_bases[seg_num] = func_addrs[0]
                log(f"  seg {seg_num}: base=0x{func_addrs[0]:08x} (first func, no ref)")

        log(f"discover_segments: found {len(self.runtime_bases)} segment bases")
        return len(self.runtime_bases) > 0

    def vma_to_runtime(self, vma):
        seg_num = self.find_segment_for_vma(vma)
        if seg_num == 0:
            return None
        base = self.runtime_bases.get(seg_num)
        if base is None:
            return None
        seg_vma_start = self.segments[seg_num].get("vma_start", 0)
        runtime = base + (vma - seg_vma_start)
        log(f"vma_to_runtime: 0x{vma:08x} -> seg {seg_num} -> 0x{runtime:08x}")
        return runtime

    def runtime_to_vma(self, runtime_addr):
        for seg_num, base in self.runtime_bases.items():
            info = self.segments.get(seg_num, {})
            seg_size = info.get("vma_end", 0) - info.get("vma_start", 0)
            if base <= runtime_addr < base + seg_size:
                return info["vma_start"] + (runtime_addr - base)
        return None


# ---------------------------------------------------------------------------
# BasiliskII Orchestration
# ---------------------------------------------------------------------------

def kill_existing_basilisk():
    try:
        subprocess.run(["pkill", "-x", "BasiliskII"], capture_output=True)
        time.sleep(1)
    except Exception:
        pass

def start_basilisk(basilisk_app, gdb_port, segments_file=None):
    basilisk_bin = os.path.join(basilisk_app, "Contents/MacOS/BasiliskII")
    cmd = [basilisk_bin, "--automation", AUTOMATION_SOCKET,
           "--gdb-port", str(gdb_port)]
    if segments_file:
        cmd.extend(["--gdb-segments", segments_file])
    log(f"Starting BasiliskII: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=open("/tmp/basilisk_stderr.txt", "w"))
    return proc

def build_app(build_dir):
    log(f"Building in {build_dir}...")
    result = subprocess.run(["make"], cwd=build_dir, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"Build failed: {result.stderr}")
        return False
    log("Build successful")
    return True


# ---------------------------------------------------------------------------
# DAP Adapter  (main class)
# ---------------------------------------------------------------------------

class Retro68DAPAdapter:
    def __init__(self, args, gdb, dwarf, segments):
        self.args = args
        self.app_name = args.app_name
        self.dap = DAPIO()
        self.gdb = gdb
        self.dwarf = dwarf
        self.segments = segments
        self.running = True
        self._cpu_running = False   # True after c/s, False when stopped
        self.source_dir = os.path.dirname(os.path.dirname(args.symbol_file))
        self.breakpoints = {}  # {source_path: [(line, vma, runtime_addr), ...]}

        self._segments_discovered = False
        self._need_arm = False  # Set by background thread, acted on by main loop

    def run(self):
        """Main event loop using select() on stdin + GDB socket."""
        log("DAP adapter starting main loop")
        stdin_fd = sys.stdin.buffer

        while self.running:
            fds = [stdin_fd]
            if self.gdb.sock and self._cpu_running:
                fds.append(self.gdb.sock)

            try:
                log(f"[SELECT] Waiting on fds: stdin={stdin_fd in fds}, gdb_sock={self.gdb.sock in fds if self.gdb.sock else False}")
                ready, _, _ = select.select(fds, [], [], 0.5)
                if ready:
                    log(f"[SELECT] Got ready: {len(ready)} fds")
            except (ValueError, OSError) as e:
                log(f"[SELECT] Error: {e}")
                break

            if stdin_fd in ready:
                log("[READ] Reading from stdin...")
                msg = self.dap.read_message()
                if msg is None:
                    log("DAP: stdin closed, exiting")
                    break
                self._handle_message(msg)

            if self.gdb.sock in ready:
                log("[GDB] Handling stop reply...")
                self._handle_stop_reply()

            # Check if background thread wants us to arm breakpoints
            if self._need_arm and self._cpu_running and self.gdb.sock:
                self._need_arm = False
                self._arm_breakpoints()

        self._cleanup()
        log("DAP adapter exiting")

    def run_test(self):
        """Auto-run through debug sequence for testing."""
        log("TEST MODE: Auto-running debug sequence")
        test_results = {
            "initialize": False,
            "attach": False,
            "setBreakpoints": False,
            "configurationDone": False,
            "breakpoint_hits": [],
            "errors": []
        }

        # Synthetic DAP messages (bypass stdin)
        def inject_message(command, arguments=None):
            """Create and handle a synthetic DAP message."""
            msg = {
                "type": "request",
                "command": command,
                "seq": 1,
            }
            if arguments:
                msg["arguments"] = arguments
            return self._handle_message(msg)

        # Phase 1: Initialize
        try:
            log("TEST: Sending initialize")
            inject_message("initialize", {
                "clientID": "test",
                "adapterID": "retro68-gdb",
            })
            test_results["initialize"] = True
        except Exception as e:
            test_results["errors"].append(f"initialize failed: {e}")

        time.sleep(0.5)

        # Phase 2: Attach
        try:
            log("TEST: Sending attach")
            inject_message("attach", {})
            test_results["attach"] = True
        except Exception as e:
            test_results["errors"].append(f"attach failed: {e}")

        time.sleep(0.5)

        # Phase 3: Set breakpoints
        try:
            log("TEST: Sending setBreakpoints")
            app_file = os.path.join(self.source_dir, "main.cpp")
            inject_message("setBreakpoints", {
                "source": {"path": app_file},
                "breakpoints": [{"line": 63}, {"line": 80}],
            })
            test_results["setBreakpoints"] = True
        except Exception as e:
            test_results["errors"].append(f"setBreakpoints failed: {e}")

        time.sleep(0.5)

        # Phase 4: Configuration done (resume CPU)
        try:
            log("TEST: Sending configurationDone")
            inject_message("configurationDone", {})
            test_results["configurationDone"] = True
            self._cpu_running = True
        except Exception as e:
            test_results["errors"].append(f"configurationDone failed: {e}")

        # Phase 4.5: Launch the app in the emulator (test mode automation)
        time.sleep(1)  # Wait for boot to progress
        try:
            log("TEST: Launching app in emulator")
            self._test_launch_app()
        except Exception as e:
            log(f"TEST: App launch failed: {e}")

        # Phase 5: Wait for breakpoint hits
        log("TEST: Waiting for breakpoints (60 second timeout)")
        start_time = time.time()
        timeout = 60
        hit_count = 0

        while time.time() - start_time < timeout and hit_count < 2:
            if not self.gdb.sock:
                break

            try:
                ready, _, _ = select.select([self.gdb.sock], [], [], 0.5)
                if self.gdb.sock in ready:
                    self._handle_stop_reply()
                    hit_count += 1
                    # Resume CPU execution after each stop (except the last one if we hit enough)
                    if hit_count < 2:
                        log("TEST: Resuming CPU execution with 'c'")
                        self.gdb.send_packet("c")
                        self._cpu_running = True
            except Exception as e:
                log(f"TEST: Error in breakpoint wait loop: {e}")
                break

        # Report test results
        self._print_test_results(test_results)
        self._cleanup()
        log("TEST MODE: Complete")

    def _delayed_auto_launch(self):
        """Launch the app after a delay (in background thread for normal mode).
        Sets _need_arm flag for main loop to handle (avoids GDB socket race condition)."""
        try:
            # Wait for boot to progress
            time.sleep(2)
            log("Auto-launching app in background thread")
            self._launch_app_via_automation()
            # Signal the main loop to arm breakpoints (don't touch GDB socket from this thread)
            log("Setting _need_arm flag for main loop")
            self._need_arm = True
        except Exception as e:
            log(f"Auto-launch failed: {e}")

    def _launch_app_via_automation(self):
        """Common app launch code used by both test and normal modes."""
        try:
            # Add the script directory to path to import basilisk_client
            script_dir = os.path.dirname(os.path.abspath(__file__))
            sys.path.insert(0, script_dir)
            from basilisk_client import BasiliskClient

            # Connect to automation socket
            client = BasiliskClient("basilisk_auto")

            app_name = self.app_name
            folder_name = "Unix"

            # Key codes
            KEY_COMMAND = 0x37
            KEY_Q = 0x0C
            KEY_O = 0x1F
            KEY_W = 0x0D
            KEY_ESCAPE = 0x35
            KEY_RETURN = 0x24

            def send_keystroke(keycode, command=False):
                """Send a keystroke with optional Command modifier"""
                if command:
                    client.key_down(KEY_COMMAND)
                client.key_down(keycode)
                time.sleep(0.05)
                client.key_up(keycode)
                if command:
                    client.key_up(KEY_COMMAND)
                client.wait_ms(100)

            def type_text(text):
                """Type text"""
                client.type_text(text)
                client.wait_ms(300)

            # Step 1: Quit any running app
            log("Sending Cmd+Q to quit current app")
            send_keystroke(KEY_Q, command=True)
            client.wait_ms(500)

            # Step 2: Close windows with Cmd+W
            log("Closing windows with Cmd+W")
            send_keystroke(KEY_W, command=True)
            client.wait_ms(300)

            # Step 3: Press Escape and navigate to folder
            log("Pressing Escape and typing folder name")
            send_keystroke(KEY_ESCAPE)
            type_text(folder_name)
            log("Opening folder with Cmd+O")
            send_keystroke(KEY_O, command=True)
            client.wait_ms(1000)

            # Step 4: Select and launch the app
            log(f"Typing app name '{app_name}'")
            type_text(app_name)
            log("Opening app with Cmd+O")
            send_keystroke(KEY_O, command=True)
            client.wait_ms(2000)  # Wait for app to launch

            log(f"App '{app_name}' launched")
            client.close()

        except Exception as e:
            log(f"App launch error: {e}")
            raise

    def _arm_breakpoints(self):
        """Tell the GDB stub to start resolving breakpoints (app is now running).
        Must pause the CPU first since GDB protocol requires stop state for commands."""
        try:
            log("Arming breakpoints: pausing CPU")
            self.gdb.send_break()
            stop_reply = self.gdb.recv_packet(timeout=5)
            log(f"Arming breakpoints: stop reply = {stop_reply}")

            log("Arming breakpoints: sending qArmBreakpoints")
            self.gdb.send_packet("qArmBreakpoints")
            resp = self.gdb.recv_packet(timeout=2)
            log(f"Arming breakpoints: response = {resp}")

            log("Arming breakpoints: resuming CPU")
            self.gdb.send_packet("c")
            self._cpu_running = True
        except Exception as e:
            log(f"Failed to arm breakpoints: {e}")

    def _test_launch_app(self):
        """Launch the app in the emulator via automation socket (test mode only)."""
        log("TEST: Launching app in emulator")
        self._launch_app_via_automation()
        self._arm_breakpoints()

    def _print_test_results(self, results):
        """Print formatted test results."""
        log("\n" + "="*60)
        log("TEST RESULTS")
        log("="*60)
        log(f"Initialize:        {'PASS' if results['initialize'] else 'FAIL'}")
        log(f"Attach:            {'PASS' if results['attach'] else 'FAIL'}")
        log(f"Set Breakpoints:   {'PASS' if results['setBreakpoints'] else 'FAIL'}")
        log(f"Configuration:     {'PASS' if results['configurationDone'] else 'FAIL'}")
        log(f"Breakpoint Hits:   {len(results['breakpoint_hits'])}")
        if results['breakpoint_hits']:
            for hit in results['breakpoint_hits']:
                log(f"  - {hit}")
        if results['errors']:
            log("Errors:")
            for err in results['errors']:
                log(f"  - {err}")
        log("="*60 + "\n")

    def _handle_message(self, msg):
        if msg.get("type") != "request":
            return
        command = msg.get("command", "")
        handler = getattr(self, f"_cmd_{command}", None)
        if handler:
            try:
                handler(msg)
            except Exception as e:
                log(f"Error handling {command}: {e}")
                import traceback
                log(traceback.format_exc())
                self.dap.send_response(msg, success=False, message=str(e))
        else:
            log(f"Unhandled DAP command: {command}")
            self.dap.send_response(msg)

    def _handle_stop_reply(self):
        """Called when the GDB socket has data while CPU is running."""
        reply = self.gdb.recv_packet(timeout=1)
        if reply is None:
            log("GDB connection lost")
            self._cpu_running = False
            self.dap.send_event("terminated")
            self.running = False
            return

        log(f"Stop reply: {reply}")
        self._cpu_running = False

        reason = "breakpoint"
        if reply.startswith("S"):
            sig = int(reply[1:3], 16)
            if sig == 2:
                reason = "pause"

        # Discover segments on second+ stop (app should be running by then).
        # On first stop (boot), jump table might be wrong. Wait for multiple breakpoint
        # hits to ensure we're looking at the app's real jump table.
        if not self._segments_discovered:
            self._stop_count = getattr(self, '_stop_count', 0) + 1
            if self._stop_count > 1:  # Skip first stop, discover on 2nd+
                if self.segments.discover_segments(self.gdb):
                    self._segments_discovered = True
                    status(f"Discovered {len(self.segments.runtime_bases)} segment(s)")
            else:
                log(f"First stop (boot?), deferring segment discovery until 2nd stop")

        # Query fresh segment bases from stub before translating PC.
        # This ensures we capture segment relocations that happen between stops.
        # Segments can move when _LoadSeg is called during app launch.
        if self._segments_discovered:
            try:
                self.segments._query_stub_segment_bases(self.gdb)
                log("Refreshed segment bases from stub")
            except Exception as e:
                log(f"Failed to refresh segment bases: {e}")

        # Log PC for debugging
        regs = self.gdb.read_registers()
        if regs:
            pc = regs[16]
            vma = self.segments.runtime_to_vma(pc)
            src = self.dwarf.get_source_line(vma) if vma else None
            if src:
                log(f"Stopped at PC=0x{pc:08x} -> {src[0]}:{src[1]}")
            else:
                log(f"Stopped at PC=0x{pc:08x} (no source mapping)")

        self.dap.send_event("stopped", {
            "reason": reason,
            "threadId": 1,
            "allThreadsStopped": True,
        })

    def _ensure_stopped(self):
        """If CPU is running, pause it. Returns True if now stopped."""
        if not self._cpu_running:
            return True
        self.gdb.send_break()
        reply = self.gdb.recv_packet(timeout=5)
        if reply and reply.startswith("S"):
            self._cpu_running = False
            return True
        return False

    def _cleanup(self):
        if self.gdb.sock:
            try:
                self.gdb.send_packet("D")
            except Exception:
                pass
            self.gdb.close()

    # -- DAP Command Handlers -----------------------------------------------

    def _cmd_initialize(self, request):
        self.dap.send_response(request, body={
            "supportsConfigurationDoneRequest": True,
            "supportsFunctionBreakpoints": False,
            "supportsConditionalBreakpoints": False,
            "supportsEvaluateForHovers": False,
            "supportsSetVariable": False,
            "supportsStepBack": False,
            "supportsRestartFrame": False,
            "supportsGotoTargetsRequest": False,
            "supportsCompletionsRequest": False,
            "supportsModulesRequest": False,
            "supportsExceptionOptions": False,
            "supportsValueFormattingOptions": False,
            "supportsExceptionInfoRequest": False,
            "supportTerminateDebuggee": True,
            "supportsDelayedStackTraceLoading": False,
            "supportsLoadedSourcesRequest": False,
            "supportsTerminateRequest": True,
        })

    def _cmd_attach(self, request):
        """GDB is already connected (done in main). Negotiate if needed and signal ready."""
        # Try to negotiate if we haven't already (in case it failed in main())
        if not self.gdb.no_ack:
            try:
                log("Attempting GDB negotiation in attach handler...")
                self.gdb.negotiate()
            except Exception as e:
                log(f"GDB negotiation in attach failed: {e}")
                # Continue anyway - might still work

        self.dap.send_response(request)
        self.dap.send_event("initialized")

    def _cmd_setBreakpoints(self, request):
        args = request.get("arguments", {})
        source = args.get("source", {})
        source_path = source.get("path", "")
        bp_requests = args.get("breakpoints", [])

        # If CPU is running, pause it to set breakpoints
        was_running = self._cpu_running
        if was_running:
            if not self._ensure_stopped():
                bps = [{"verified": False, "line": b.get("line", 0)} for b in bp_requests]
                self.dap.send_response(request, body={"breakpoints": bps})
                return

        result_breakpoints = []
        new_bps = []

        for bp in bp_requests:
            line = bp.get("line", 0)
            vma = self.dwarf.get_vma(source_path, line)
            if vma is None:
                log(f"setBreakpoints: no VMA for {source_path}:{line}")
                result_breakpoints.append({
                    "verified": False, "line": line,
                    "message": "No debug info for this line",
                })
                continue

            # Translate VMA to runtime address if segments are known
            runtime_addr = self.segments.vma_to_runtime(vma)
            if runtime_addr is None:
                # Send VMA to stub — it will defer until segments load
                runtime_addr = vma
                log(f"setBreakpoints: sending VMA 0x{vma:08x} to stub (deferred)")

            # Set breakpoint in stub
            self.gdb.send_packet(f"Z0,{runtime_addr:x},0")
            resp = self.gdb.recv_packet(timeout=5)
            verified = (resp == "OK")
            log(f"setBreakpoints: Z0,{runtime_addr:x},0 -> {resp}")

            new_bps.append((line, vma, runtime_addr))
            result_breakpoints.append({
                "verified": verified, "line": line, "source": source,
            })

        self.breakpoints[source_path] = new_bps
        self.dap.send_response(request, body={"breakpoints": result_breakpoints})

        # Resume if we paused just for this
        if was_running:
            self.gdb.send_packet("c")
            self._cpu_running = True

    def _cmd_setExceptionBreakpoints(self, request):
        self.dap.send_response(request, body={"breakpoints": []})

    def _cmd_configurationDone(self, request):
        """Configuration is done. Resume the CPU so boot continues."""
        self.dap.send_response(request)
        if self.gdb.sock:
            self.gdb.send_packet("c")
            self._cpu_running = True
            status("Debugging active — launching app in emulator")
            # Launch app automatically after a short delay (in background thread)
            if not self.args.test_mode:
                threading.Thread(target=self._delayed_auto_launch, daemon=True).start()

    def _cmd_threads(self, request):
        self.dap.send_response(request, body={
            "threads": [{"id": 1, "name": "m68k"}],
        })

    def _cmd_stackTrace(self, request):
        frames = []
        regs = self.gdb.read_registers() if self.gdb.sock else None
        if regs:
            pc = regs[16]
            vma = self.segments.runtime_to_vma(pc)
            source_info = self.dwarf.get_source_line(vma) if vma else None

            frame = {"id": 1, "name": f"0x{pc:08x}", "line": 0, "column": 0}
            if source_info:
                filename, line_num = source_info
                frame["name"] = f"{filename}:{line_num}"
                frame["line"] = line_num
                frame["column"] = 1
                full_path = self._find_source_path(filename)
                if full_path:
                    frame["source"] = {"name": filename, "path": full_path}
            frames.append(frame)

        self.dap.send_response(request, body={
            "stackFrames": frames, "totalFrames": len(frames),
        })

    def _cmd_scopes(self, request):
        self.dap.send_response(request, body={
            "scopes": [{
                "name": "Registers",
                "variablesReference": 1,
                "expensive": False,
            }],
        })

    def _cmd_variables(self, request):
        variables = []
        regs = self.gdb.read_registers() if self.gdb.sock else None
        if regs:
            for i, name in enumerate(REG_NAMES):
                variables.append({
                    "name": name,
                    "value": f"0x{regs[i]:08x}",
                    "variablesReference": 0,
                })
        self.dap.send_response(request, body={"variables": variables})

    def _cmd_continue(self, request):
        self.dap.send_response(request, body={"allThreadsContinued": True})
        if self.gdb.sock:
            # Stub handles stepping past breakpoints internally
            # (gdb_skip_next_bp flag)
            self.gdb.send_packet("c")
            self._cpu_running = True

    def _cmd_next(self, request):
        self.dap.send_response(request)
        if self.gdb.sock:
            self.gdb.send_packet("s")
            self._cpu_running = True

    def _cmd_stepIn(self, request):
        self.dap.send_response(request)
        if self.gdb.sock:
            self.gdb.send_packet("s")
            self._cpu_running = True

    def _cmd_stepOut(self, request):
        self.dap.send_response(request)
        if self.gdb.sock:
            self.gdb.send_packet("c")
            self._cpu_running = True

    def _cmd_pause(self, request):
        if self.gdb.sock:
            self.gdb.send_break()
        self.dap.send_response(request)

    def _cmd_disconnect(self, request):
        self.running = False
        if self.gdb.sock:
            try:
                if not self._cpu_running:
                    self.gdb.send_packet("D")
                    self.gdb.recv_packet(timeout=2)
            except Exception:
                pass
            self.gdb.close()
        self.dap.send_response(request)

    def _cmd_terminate(self, request):
        self.running = False
        if self.gdb.sock:
            try:
                self.gdb.send_packet("k")
            except Exception:
                pass
            self.gdb.close()
        self.dap.send_response(request)

    # -- Helpers -------------------------------------------------------------

    def _find_source_path(self, filename):
        candidate = os.path.join(self.source_dir, filename)
        if os.path.exists(candidate):
            return candidate
        for root, dirs, files in os.walk(self.source_dir):
            if filename in files:
                return os.path.join(root, filename)
        return None


# ---------------------------------------------------------------------------
# Main — connect early, enter DAP loop, launch app later via timer
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Custom DAP adapter for Retro68 + BasiliskII debugging")
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--symbol-file", required=True)
    parser.add_argument("--segments-file")
    parser.add_argument("--basilisk", required=True)
    parser.add_argument("--launch-script")  # unused, kept for CLI compat
    parser.add_argument("--build-dir")
    parser.add_argument("--objdump")
    parser.add_argument("--port", type=int, default=GDB_PORT_DEFAULT)
    parser.add_argument("--test-mode", action="store_true",
                        help="Auto-run through debug sequence for testing")
    args = parser.parse_args()

    log(f"Args: {vars(args)}")

    # ---- Build ----
    # Nova builds the app before launching Debug task, so skip building here
    # The build_dir is passed for reference only (for finding segments file, etc)

    if args.build_dir:
        log(f"Build directory: {args.build_dir} (skipping rebuild - Nova built it)")

    # ---- Start BasiliskII ----

    status("Starting BasiliskII...")
    kill_existing_basilisk()
    proc = start_basilisk(args.basilisk, args.port, args.segments_file)
    time.sleep(5)

    # ---- Connect early (CPU pauses on connect) ----

    status("Connecting to debugger...")
    gdb = GDBRemote("localhost", args.port)
    if not gdb.connect(timeout=15):
        status("Failed to connect to GDB stub!")
        sys.exit(1)

    try:
        gdb.negotiate()
    except Exception as e:
        log(f"Warning: GDB negotiation failed: {e}")
        log("Continuing anyway - will retry during attach")

    # ---- Load DWARF + segment metadata (no discovery yet) ----

    dwarf = DWARFLineTable()
    if args.objdump and args.symbol_file:
        dwarf.load(args.objdump, args.symbol_file)

    segments = SegmentMap()
    if args.segments_file:
        segments.load(args.segments_file)

    # Don't discover segments yet — too early, app hasn't loaded.
    # Segments are discovered on the first breakpoint stop.

    status("Ready — entering DAP mode")

    # ---- DAP loop ----
    # CPU is stopped. Nova will send attach/setBreakpoints/configurationDone.
    # Breakpoints sent as VMAs -> stub defers them.
    # configurationDone resumes CPU. User launches app manually.
    # _LoadSeg resolves deferred BPs. First stop discovers segments.

    adapter = Retro68DAPAdapter(args, gdb, dwarf, segments)

    if args.test_mode:
        adapter.run_test()
    else:
        adapter.run()


if __name__ == "__main__":
    main()
