#!/usr/bin/env python3
"""
Debug wrapper for Retro68 + BasiliskII + GDB DAP.

This script:
1. Builds the app (if build_dir provided)
2. Starts BasiliskII with GDB stub enabled
3. Launches the app in the emulator
4. Waits for GDB port to be ready
5. Exec's GDB in DAP mode to connect

Usage:
    debug_wrapper.py --app-name NAME --symbol-file PATH --basilisk PATH --launch-script PATH [--build-dir PATH]
"""

import argparse
import os
import socket
import subprocess
import sys
import time

GDB_PORT = 9999
GDB_PATH = "/opt/homebrew/bin/gdb"
AUTOMATION_SOCKET = "basilisk_auto"


def wait_for_port(port, host='localhost', timeout=30):
    """Wait for a port to become available."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                return True
        except:
            pass
        time.sleep(0.5)
    return False


def kill_existing_basilisk():
    """Kill any running BasiliskII process."""
    try:
        subprocess.run(['pkill', '-x', 'BasiliskII'], capture_output=True)
        time.sleep(1)
    except:
        pass


def main():
    parser = argparse.ArgumentParser(description='Debug wrapper for Retro68 + BasiliskII')
    parser.add_argument('--app-name', required=True, help='Name of the app to debug')
    parser.add_argument('--symbol-file', required=True, help='Path to .code.bin.gdb symbol file')
    parser.add_argument('--basilisk', required=True, help='Path to BasiliskII.app')
    parser.add_argument('--launch-script', required=True, help='Path to launch_app.py')
    parser.add_argument('--build-dir', help='Build directory (will run make if provided)')
    parser.add_argument('--gdb-port', type=int, default=GDB_PORT, help='GDB port')
    args = parser.parse_args()

    # Build if requested
    if args.build_dir:
        print(f"Building in {args.build_dir}...", file=sys.stderr)
        result = subprocess.run(['make'], cwd=args.build_dir, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Build failed:\n{result.stderr}", file=sys.stderr)
            sys.exit(1)
        print("Build successful", file=sys.stderr)

    # Kill existing BasiliskII
    print("Stopping any existing BasiliskII...", file=sys.stderr)
    kill_existing_basilisk()

    # Start BasiliskII with GDB stub
    basilisk_bin = os.path.join(args.basilisk, "Contents/MacOS/BasiliskII")
    stderr_file = "/tmp/basilisk_stderr.txt"

    # Find the segments file for VMA → runtime address translation
    segments_file = None
    if args.build_dir:
        segments_file = os.path.join(args.build_dir, args.app_name + ".segments")
        if not os.path.exists(segments_file):
            # Try alternate naming
            segments_file = os.path.join(args.build_dir, args.app_name + ".code.bin.segments")
        if not os.path.exists(segments_file):
            print(f"Warning: segments file not found, breakpoints may not work correctly", file=sys.stderr)
            segments_file = None

    print(f"Starting BasiliskII with GDB port {args.gdb_port}...", file=sys.stderr)
    basilisk_args = [basilisk_bin, '--automation', AUTOMATION_SOCKET, '--gdb-port', str(args.gdb_port)]
    if segments_file:
        basilisk_args.extend(['--gdb-segments', segments_file])
        print(f"Using segments file: {segments_file}", file=sys.stderr)

    with open(stderr_file, 'w') as stderr_out:
        basilisk_proc = subprocess.Popen(
            basilisk_args,
            stdout=subprocess.DEVNULL,
            stderr=stderr_out
        )

    # Wait for BasiliskII to start
    time.sleep(5)

    # Launch the app
    print(f"Launching {args.app_name} in emulator...", file=sys.stderr)
    result = subprocess.run(
        ['python3', args.launch_script, args.app_name, '--socket', AUTOMATION_SOCKET, '--folder', 'Unix'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Failed to launch app: {result.stderr}", file=sys.stderr)

    # Don't probe the GDB port - connecting and disconnecting would consume
    # the stub's single client slot, preventing GDB from connecting later.
    # GDB DAP will retry the connection itself.
    print(f"GDB port {args.gdb_port} should be ready (BasiliskII started)", file=sys.stderr)

    print("Starting GDB DAP...", file=sys.stderr)

    # Get the source directory (parent of build dir, or same as symbol file dir's parent)
    source_dir = os.path.dirname(os.path.dirname(args.symbol_file))

    print(f"Source directory: {source_dir}", file=sys.stderr)
    print(f"Symbol file: {args.symbol_file}", file=sys.stderr)

    # Create a GDB init script to load symbols and connect before DAP starts
    # Note: 'target remote' will pause the emulator (stub sends S05).
    # Nova should show the debug toolbar - user clicks Continue to resume.
    gdb_init = "/tmp/gdb_init.gdb"
    with open(gdb_init, 'w') as f:
        f.write(f"set architecture m68k:68020\n")
        f.write(f"set remotetimeout 30\n")
        f.write(f"directory {source_dir}\n")
        f.write(f"symbol-file {args.symbol_file}\n")
        f.write(f"target remote localhost:{args.gdb_port}\n")

    print(f"GDB init script: {gdb_init}", file=sys.stderr)

    # Exec DAP logger (wraps GDB) to capture DAP traffic for debugging
    dap_logger = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dap_logger.py")
    if os.path.exists(dap_logger):
        print(f"Using DAP logger: {dap_logger}", file=sys.stderr)
        os.execv(sys.executable, [
            sys.executable, dap_logger,
            '--interpreter=dap',
            '-x', gdb_init,
        ])
    else:
        # Exec GDB in DAP mode directly
        os.execv(GDB_PATH, [
            GDB_PATH,
            '--interpreter=dap',
            '-x', gdb_init,
        ])


if __name__ == '__main__':
    main()
