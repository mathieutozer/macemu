#!/usr/bin/env python3
"""
Simple GDB remote debugging client for BasiliskII.
Stays connected and waits for breakpoint hits.
"""

import socket
import sys
import select
import argparse

def compute_checksum(data):
    """Compute GDB packet checksum."""
    return sum(ord(c) for c in data) & 0xff

def send_packet(sock, data):
    """Send a GDB packet."""
    checksum = compute_checksum(data)
    packet = f"${data}#{checksum:02x}"
    print(f">>> {packet}")
    sock.sendall(packet.encode())

def recv_packet(sock, timeout=None):
    """Receive a GDB packet. Returns payload or None on error."""
    # Wait for data with timeout
    if timeout is not None:
        ready = select.select([sock], [], [], timeout)
        if not ready[0]:
            return None

    # Read until we get '$'
    while True:
        c = sock.recv(1)
        if not c:
            return None  # Connection closed
        if c == b'$':
            break
        # Handle ack/nack
        if c in (b'+', b'-'):
            continue

    # Read payload until '#'
    payload = b''
    while True:
        c = sock.recv(1)
        if not c:
            return None
        if c == b'#':
            break
        payload += c

    # Read checksum (2 hex chars)
    csum = sock.recv(2)
    if len(csum) != 2:
        return None

    result = payload.decode('latin-1')
    print(f"<<< ${result}#{csum.decode()}")

    # Send ack
    sock.sendall(b'+')

    return result

def read_memory(sock, addr, length):
    """Read memory from emulator."""
    send_packet(sock, f"m{addr:x},{length}")
    return recv_packet(sock)

def read_registers(sock):
    """Read all registers."""
    send_packet(sock, "g")
    return recv_packet(sock)

def set_breakpoint(sock, addr):
    """Set a breakpoint at address."""
    send_packet(sock, f"Z0,{addr:x},1")
    return recv_packet(sock)

def continue_execution(sock):
    """Continue execution."""
    send_packet(sock, "c")
    # Don't wait for response - we'll get S05 when it stops

def main():
    parser = argparse.ArgumentParser(description='GDB client for BasiliskII')
    parser.add_argument('--port', type=int, default=2345, help='GDB port')
    parser.add_argument('--host', default='localhost', help='Host')
    parser.add_argument('--breakpoint', '-b', type=str, help='Set breakpoint at address (hex)')
    args = parser.parse_args()

    print(f"Connecting to {args.host}:{args.port}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((args.host, args.port))
    print("Connected!")

    # Wait for initial stop (if emulator sends one)
    print("Waiting for initial response...")
    response = recv_packet(sock, timeout=1.0)
    if response:
        print(f"Initial response: {response}")

    # Set breakpoint if specified
    if args.breakpoint:
        addr = int(args.breakpoint, 16)
        print(f"Setting breakpoint at 0x{addr:08x}")
        resp = set_breakpoint(sock, addr)
        if resp == "OK":
            print("Breakpoint set!")
        else:
            print(f"Failed to set breakpoint: {resp}")
            return

    # Interactive loop
    print("\nCommands:")
    print("  c         - continue")
    print("  g         - read registers")
    print("  m addr,len - read memory")
    print("  b addr    - set breakpoint")
    print("  q         - quit")
    print("\nWaiting for commands or breakpoint hits...")

    try:
        while True:
            # Wait for either user input or data from emulator
            ready = select.select([sys.stdin, sock], [], [], 0.5)

            if sock in ready[0]:
                # Data from emulator (probably a stop reply)
                response = recv_packet(sock, timeout=0.1)
                if response is None:
                    print("Connection closed by emulator")
                    break
                if response.startswith('S'):
                    signal = int(response[1:3], 16)
                    print(f"\n*** STOPPED (signal {signal}) ***")
                    # Read PC
                    regs = read_registers(sock)
                    if regs and len(regs) >= 144:
                        # PC is register 17 (68th-71st bytes = chars 136-143)
                        pc_hex = regs[136:144]
                        pc = int(pc_hex, 16)
                        print(f"PC = 0x{pc:08x}")
                    print("Enter command (c to continue):")

            if sys.stdin in ready[0]:
                line = sys.stdin.readline().strip()
                if not line:
                    continue

                if line == 'q':
                    break
                elif line == 'c':
                    continue_execution(sock)
                    print("Continuing...")
                elif line == 'g':
                    regs = read_registers(sock)
                    if regs:
                        # Parse registers (D0-D7, A0-A7, SR, PC)
                        print("Registers:")
                        for i in range(8):
                            d = int(regs[i*8:(i+1)*8], 16)
                            a = int(regs[(i+8)*8:(i+9)*8], 16)
                            print(f"  D{i}=0x{d:08x}  A{i}=0x{a:08x}")
                        sr = int(regs[128:132], 16)
                        pc = int(regs[136:144], 16)
                        print(f"  SR=0x{sr:04x}  PC=0x{pc:08x}")
                elif line.startswith('m '):
                    parts = line[2:].split(',')
                    addr = int(parts[0], 16)
                    length = int(parts[1]) if len(parts) > 1 else 16
                    mem = read_memory(sock, addr, length)
                    if mem:
                        print(f"Memory at 0x{addr:08x}: {mem}")
                elif line.startswith('b '):
                    addr = int(line[2:], 16)
                    resp = set_breakpoint(sock, addr)
                    print(f"Breakpoint: {resp}")
                else:
                    print(f"Unknown command: {line}")

    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        sock.close()
        print("Disconnected")

if __name__ == '__main__':
    main()
