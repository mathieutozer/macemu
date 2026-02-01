#!/usr/bin/env python3
"""
GDB Monitor for BasiliskII 68k Debugging

This script runs alongside BasiliskII and monitors for crashes via the GDB stub.
When a crash occurs, it captures the state and outputs a readable crash report
with symbol information from the Retro68-generated .gdb files.

Usage:
    python3 gdb_monitor.py --app-name "AppName" --build-dir /path/to/build [--symbol-file path.gdb]
"""

import argparse
import os
import re
import socket
import subprocess
import sys
import time
import signal
from pathlib import Path

GDB_PORT = 9999

class GdbMonitor:
    def __init__(self, symbol_file=None, app_name=None):
        self.symbol_file = symbol_file
        self.app_name = app_name
        self.sock = None
        self.connected = False
        self.no_ack_mode = False

    def connect(self, host='localhost', port=GDB_PORT, timeout=5.0):
        """Connect to the GDB stub"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect((host, port))
            self.connected = True
            # Initial handshake
            self._send_packet('qSupported:multiprocess+')
            self._recv_packet()  # Don't care about response
            return True
        except (socket.timeout, ConnectionRefusedError) as e:
            return False

    def disconnect(self):
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        self.connected = False

    def _checksum(self, data):
        return sum(ord(c) for c in data) & 0xff

    def _send_packet(self, data):
        """Send a GDB packet"""
        packet = f'${data}#{self._checksum(data):02x}'
        self.sock.sendall(packet.encode('ascii'))

    def _recv_packet(self, timeout=2.0):
        """Receive a GDB packet"""
        self.sock.settimeout(timeout)
        try:
            response = b''
            # Read until we get a complete packet
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                # Check for complete packet: $...#xx
                decoded = response.decode('ascii', errors='ignore')
                if '#' in decoded and len(decoded) >= decoded.index('#') + 3:
                    break

            decoded = response.decode('ascii', errors='ignore')
            # Extract packet content
            match = re.search(r'\$([^#]*)', decoded)
            if match:
                return match.group(1)
            return decoded
        except socket.timeout:
            return None

    def get_registers(self):
        """Get all registers"""
        self._send_packet('g')
        response = self._recv_packet()
        if not response or response.startswith('E'):
            return None

        # Parse register hex dump (each register is 8 hex chars = 32 bits)
        regs = {}
        reg_names = ['d0', 'd1', 'd2', 'd3', 'd4', 'd5', 'd6', 'd7',
                     'a0', 'a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'sp', 'sr', 'pc']

        for i, name in enumerate(reg_names):
            start = i * 8
            if start + 8 <= len(response):
                hex_val = response[start:start+8]
                try:
                    regs[name] = int(hex_val, 16)
                except ValueError:
                    regs[name] = 0

        return regs

    def read_memory(self, addr, length):
        """Read memory from target"""
        self._send_packet(f'm{addr:x},{length:x}')
        response = self._recv_packet()
        if not response or response.startswith('E'):
            return None
        return bytes.fromhex(response)

    def continue_execution(self):
        """Continue execution"""
        self._send_packet('c')

    def wait_for_stop(self, timeout=None):
        """Wait for the target to stop (crash or breakpoint)"""
        if timeout:
            self.sock.settimeout(timeout)
        else:
            self.sock.settimeout(None)  # Block indefinitely

        try:
            response = self._recv_packet(timeout=timeout if timeout else 3600)
            if response and response.startswith('S') or response and response.startswith('T'):
                # Stopped - extract signal number
                signal_hex = response[1:3]
                try:
                    signal_num = int(signal_hex, 16)
                    return signal_num
                except ValueError:
                    return 5  # Default to SIGTRAP
            return None
        except socket.timeout:
            return None


def signal_to_name(sig):
    """Convert signal number to name"""
    signals = {
        2: 'SIGINT (Interrupt)',
        4: 'SIGILL (Illegal Instruction)',
        5: 'SIGTRAP (Breakpoint/Trace)',
        7: 'SIGBUS (Bus Error)',
        8: 'SIGFPE (Floating Point Exception)',
        11: 'SIGSEGV (Segmentation Fault)',
    }
    return signals.get(sig, f'Signal {sig}')


def run_addr2line(symbol_file, address):
    """Use addr2line to get source location"""
    try:
        # Try m68k-apple-macos-addr2line first
        addr2line = os.path.expanduser('~/.retro68/Retro68-build/toolchain/bin/m68k-apple-macos-addr2line')
        result = subprocess.run(
            [addr2line, '-e', symbol_file, '-f', '-C', f'0x{address:x}'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                func_name = lines[0]
                location = lines[1]
                if func_name != '??' and location != '??:0':
                    return func_name, location
    except Exception as e:
        pass
    return None, None


def generate_crash_report(monitor, signal_num, symbol_file=None):
    """Generate a human-readable crash report"""
    report = []
    report.append("")
    report.append("=" * 60)
    report.append("  68k CRASH REPORT")
    report.append("=" * 60)
    report.append("")
    report.append(f"Signal: {signal_to_name(signal_num)}")
    report.append("")

    regs = monitor.get_registers()
    if regs:
        pc = regs.get('pc', 0)
        report.append(f"PC (Program Counter): 0x{pc:08X}")

        # Try to get source location
        if symbol_file and os.path.exists(symbol_file):
            func_name, location = run_addr2line(symbol_file, pc)
            if func_name:
                report.append(f"Function: {func_name}")
            if location:
                report.append(f"Location: {location}")

        report.append("")
        report.append("Registers:")
        report.append("-" * 40)

        # Data registers
        d_line = "  "
        for i in range(8):
            d_line += f"D{i}={regs.get(f'd{i}', 0):08X}  "
            if i == 3:
                report.append(d_line)
                d_line = "  "
        report.append(d_line)

        # Address registers
        a_line = "  "
        for i in range(8):
            reg_name = 'sp' if i == 7 else f'a{i}'
            a_line += f"A{i}={regs.get(reg_name, 0):08X}  "
            if i == 3:
                report.append(a_line)
                a_line = "  "
        report.append(a_line)

        report.append(f"  SR={regs.get('sr', 0):08X}  PC={regs.get('pc', 0):08X}")

        # Try to show stack trace
        sp = regs.get('sp', 0)
        if sp > 0 and symbol_file:
            report.append("")
            report.append("Stack (potential return addresses):")
            report.append("-" * 40)

            # Read some stack memory
            stack_data = monitor.read_memory(sp, 64)
            if stack_data:
                for i in range(0, min(len(stack_data), 64), 4):
                    if i + 4 <= len(stack_data):
                        addr = int.from_bytes(stack_data[i:i+4], 'big')
                        # Check if this looks like a code address
                        if 0x1000 < addr < 0x1000000:
                            func_name, location = run_addr2line(symbol_file, addr)
                            if func_name and func_name != '??':
                                report.append(f"  0x{addr:08X} -> {func_name}")
                                if location and location != '??:0':
                                    report.append(f"                {location}")

    report.append("")
    report.append("=" * 60)
    report.append("")

    return '\n'.join(report)


def main():
    parser = argparse.ArgumentParser(description='GDB Monitor for BasiliskII')
    parser.add_argument('--app-name', help='Application name')
    parser.add_argument('--build-dir', help='Build directory containing symbol file')
    parser.add_argument('--symbol-file', help='Path to .gdb symbol file')
    parser.add_argument('--port', type=int, default=GDB_PORT, help='GDB port')
    parser.add_argument('--timeout', type=float, help='Timeout in seconds (default: wait forever)')
    args = parser.parse_args()

    # Find symbol file
    symbol_file = args.symbol_file
    if not symbol_file and args.build_dir and args.app_name:
        candidate = os.path.join(args.build_dir, f'{args.app_name}.code.bin.gdb')
        if os.path.exists(candidate):
            symbol_file = candidate

    if symbol_file:
        print(f"Symbol file: {symbol_file}", file=sys.stderr)
    else:
        print("Warning: No symbol file found, crash reports will have limited info", file=sys.stderr)

    monitor = GdbMonitor(symbol_file=symbol_file, app_name=args.app_name)

    # Try to connect
    print(f"Connecting to GDB stub on port {args.port}...", file=sys.stderr)

    # Retry connection for a bit
    for attempt in range(10):
        if monitor.connect(port=args.port, timeout=2.0):
            break
        time.sleep(1)

    if not monitor.connected:
        print("Failed to connect to GDB stub", file=sys.stderr)
        print("Make sure BasiliskII is running with --gdb-port", file=sys.stderr)
        sys.exit(1)

    print("Connected. Monitoring for crashes...", file=sys.stderr)
    print("(Press Ctrl+C to stop monitoring)", file=sys.stderr)

    # Continue execution in case it's stopped
    monitor.continue_execution()

    try:
        while True:
            signal_num = monitor.wait_for_stop(timeout=args.timeout)

            if signal_num is None:
                if args.timeout:
                    print("Timeout - no crash detected", file=sys.stderr)
                    break
                continue

            # Got a stop - generate crash report
            report = generate_crash_report(monitor, signal_num, symbol_file)
            print(report)

            # For non-breakpoint stops, we're done
            if signal_num != 5:  # Not SIGTRAP
                break

            # For breakpoints, continue
            monitor.continue_execution()

    except KeyboardInterrupt:
        print("\nMonitoring stopped by user", file=sys.stderr)
    finally:
        monitor.disconnect()


if __name__ == '__main__':
    main()
