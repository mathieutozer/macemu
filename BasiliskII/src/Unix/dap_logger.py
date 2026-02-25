#!/usr/bin/env python3
"""
DAP traffic logger - sits between Nova and GDB DAP to log all messages.
"""

import sys
import os
import json
import subprocess
import threading
import time

LOG_FILE = "/tmp/dap_traffic.log"
GDB_PATH = "/opt/homebrew/bin/gdb"

def log(direction, data):
    with open(LOG_FILE, "a") as f:
        timestamp = time.strftime("%H:%M:%S")
        try:
            # Try to parse as JSON for pretty printing
            text = data.decode('utf-8', errors='replace')
            f.write(f"[{timestamp}] {direction}: {text}\n")
        except:
            f.write(f"[{timestamp}] {direction}: {data!r}\n")
        f.flush()

def forward_stdin_to_gdb(gdb_proc):
    """Read from our stdin (Nova) and forward to GDB's stdin."""
    try:
        while True:
            data = sys.stdin.buffer.read(1)
            if not data:
                log("NOVA->GDB", b"[EOF]")
                gdb_proc.stdin.close()
                break
            # Buffer until we have a complete message
            buf = data
            # Read the header
            while not buf.endswith(b'\r\n\r\n'):
                c = sys.stdin.buffer.read(1)
                if not c:
                    break
                buf += c

            # Parse content length
            header = buf.decode('utf-8', errors='replace')
            content_length = 0
            for line in header.split('\r\n'):
                if line.startswith('Content-Length:'):
                    content_length = int(line.split(':')[1].strip())

            # Read the body
            body = sys.stdin.buffer.read(content_length)

            log("NOVA->GDB", buf + body)
            gdb_proc.stdin.write(buf + body)
            gdb_proc.stdin.flush()
    except Exception as e:
        log("ERROR", f"stdin forward: {e}".encode())

def forward_gdb_to_stdout(gdb_proc):
    """Read from GDB's stdout and forward to our stdout (Nova)."""
    try:
        while True:
            data = gdb_proc.stdout.read(1)
            if not data:
                log("GDB->NOVA", b"[EOF]")
                break
            # Buffer until we have a complete message
            buf = data
            while not buf.endswith(b'\r\n\r\n'):
                c = gdb_proc.stdout.read(1)
                if not c:
                    break
                buf += c

            # Parse content length
            header = buf.decode('utf-8', errors='replace')
            content_length = 0
            for line in header.split('\r\n'):
                if line.startswith('Content-Length:'):
                    content_length = int(line.split(':')[1].strip())

            # Read the body
            body = gdb_proc.stdout.read(content_length)

            log("GDB->NOVA", buf + body)
            sys.stdout.buffer.write(buf + body)
            sys.stdout.buffer.flush()
    except Exception as e:
        log("ERROR", f"stdout forward: {e}".encode())

def main():
    # Clear log
    with open(LOG_FILE, "w") as f:
        f.write(f"=== DAP Logger started at {time.strftime('%H:%M:%S')} ===\n")
        f.write(f"Args: {sys.argv}\n")

    # Build GDB command - pass through all args after our script name
    # The debug_wrapper.py will exec this script instead of GDB directly
    gdb_args = [GDB_PATH] + sys.argv[1:]

    log("INFO", f"Starting GDB: {gdb_args}".encode())

    gdb_proc = subprocess.Popen(
        gdb_args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr  # Pass stderr through directly
    )

    # Start forwarding threads
    stdin_thread = threading.Thread(target=forward_stdin_to_gdb, args=(gdb_proc,), daemon=True)
    stdout_thread = threading.Thread(target=forward_gdb_to_stdout, args=(gdb_proc,), daemon=True)

    stdin_thread.start()
    stdout_thread.start()

    # Wait for GDB to exit
    gdb_proc.wait()
    log("INFO", f"GDB exited with code {gdb_proc.returncode}".encode())

    # Give threads time to flush
    time.sleep(0.5)
    sys.exit(gdb_proc.returncode)

if __name__ == '__main__':
    main()
