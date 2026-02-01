#!/usr/bin/env python3
"""
Parse 68k crash dumps and resolve symbols.

Usage:
    parse_crash.py --symbol-file path/to/app.code.bin.gdb --pc 0x07d75984 [--main-addr 0x07d75aae]

If main-addr is provided, it's used to calculate the code base.
Otherwise, tries to find a known function to calculate offset.
"""

import argparse
import os
import re
import subprocess
import sys

TOOLCHAIN_BIN = os.path.expanduser("~/.retro68/Retro68-build/toolchain/bin")
ADDR2LINE = os.path.join(TOOLCHAIN_BIN, "m68k-apple-macos-addr2line")
NM = os.path.join(TOOLCHAIN_BIN, "m68k-apple-macos-nm")


def get_symbol_addresses(symbol_file):
    """Get key symbol addresses from the symbol file."""
    symbols = {}
    try:
        result = subprocess.run(
            [NM, symbol_file],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.split('\n'):
            parts = line.strip().split()
            if len(parts) >= 3:
                addr = int(parts[0], 16)
                name = parts[2]
                # Store demangled-friendly names
                if name == 'main' or name.startswith('_Z'):
                    symbols[name] = addr
                # Look for common entry points
                if 'main' in name.lower():
                    symbols[name] = addr
    except Exception as e:
        print(f"Warning: Could not read symbols: {e}", file=sys.stderr)
    return symbols


def resolve_address(symbol_file, relative_addr):
    """Resolve a relative address to file:line using addr2line."""
    try:
        result = subprocess.run(
            [ADDR2LINE, '-e', symbol_file, '-f', '-C', f'0x{relative_addr:x}'],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().split('\n')
        if len(lines) >= 2:
            func_name = lines[0]
            location = lines[1]
            if func_name != '??' and ':' in location:
                return func_name, location
    except Exception as e:
        print(f"Warning: addr2line failed: {e}", file=sys.stderr)
    return None, None


def parse_crash_output(text):
    """Parse crash dump from stderr output."""
    # Look for our crash dump format
    pc_match = re.search(r'PC:\s*([0-9a-fA-F]+)', text)
    if pc_match:
        return int(pc_match.group(1), 16)
    return None


def main():
    parser = argparse.ArgumentParser(description='Parse 68k crash dumps and resolve symbols')
    parser.add_argument('--symbol-file', required=True, help='Path to .code.bin.gdb symbol file')
    parser.add_argument('--pc', help='PC address from crash (hex, e.g., 0x07d75984)')
    parser.add_argument('--main-addr', help='Runtime address of main() if known')
    parser.add_argument('--stdin', action='store_true', help='Read crash dump from stdin')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    args = parser.parse_args()

    if not os.path.exists(args.symbol_file):
        print(f"Error: Symbol file not found: {args.symbol_file}", file=sys.stderr)
        sys.exit(1)

    # Get PC from args or stdin
    pc = None
    if args.pc:
        pc = int(args.pc, 16) if args.pc.startswith('0x') else int(args.pc, 16)
    elif args.stdin:
        crash_text = sys.stdin.read()
        pc = parse_crash_output(crash_text)

    if pc is None:
        print("Error: No PC address provided", file=sys.stderr)
        sys.exit(1)

    # Get symbol addresses
    symbols = get_symbol_addresses(args.symbol_file)

    # Find main's address in symbols
    main_sym_addr = symbols.get('main')
    if main_sym_addr is None:
        print("Error: Could not find 'main' in symbol file", file=sys.stderr)
        sys.exit(1)

    # Calculate code base
    # If we know the runtime address of main, use it
    # Otherwise, we need to guess or be told
    if args.main_addr:
        runtime_main = int(args.main_addr, 16) if args.main_addr.startswith('0x') else int(args.main_addr)
        code_base = runtime_main - main_sym_addr
    else:
        # Try to find code base by looking for known patterns
        # This is a heuristic - check if PC - main_sym_addr gives a reasonable base
        # Typically code loads in the 0x00xxxxxx to 0x0Fxxxxxx range
        possible_base = pc - main_sym_addr
        if possible_base > 0:
            code_base = possible_base
        else:
            # Try finding any function that PC might be in
            for name, addr in sorted(symbols.items(), key=lambda x: x[1]):
                if pc > addr:
                    possible_base = pc - addr
                    # Reasonable base check
                    if 0 < possible_base < 0x10000000:
                        code_base = possible_base - addr + addr  # Effectively just the offset
                        break
            else:
                print("Error: Could not determine code base address", file=sys.stderr)
                sys.exit(1)

    # Calculate relative PC
    relative_pc = pc - code_base

    # Resolve to file:line
    func_name, location = resolve_address(args.symbol_file, relative_pc)

    if args.json:
        import json
        result = {
            "pc": f"0x{pc:08x}",
            "relative_pc": f"0x{relative_pc:x}",
            "code_base": f"0x{code_base:x}",
            "function": func_name,
            "location": location
        }
        if location and ':' in location:
            parts = location.split(':')
            result["file"] = parts[0]
            result["line"] = int(parts[1]) if parts[1].isdigit() else 0
        print(json.dumps(result))
    else:
        print(f"PC: 0x{pc:08x}")
        print(f"Code base: 0x{code_base:x}")
        print(f"Relative: 0x{relative_pc:x}")
        if func_name:
            print(f"Function: {func_name}")
        if location:
            print(f"Location: {location}")


if __name__ == '__main__':
    main()
