#!/usr/bin/env python3
"""
Launch an app inside BasiliskII via automation.

Usage:
    launch_app.py <app_name> [--socket <name>] [--folder <name>]

Example:
    launch_app.py "StarWars" --socket basilisk_auto --folder Unix

This script:
1. Force quits any running app (Cmd+Q)
2. Opens a new Finder window (Cmd+N)
3. Types the folder name to select it, opens it (Cmd+O)
4. Types the app name to select it, opens it (Cmd+O)
"""

import sys
import os
import time
import argparse

# Add the directory containing this script to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from basilisk_client import BasiliskClient

# Mac keycodes
KEY_COMMAND = 0x37
KEY_OPTION = 0x3A
KEY_SHIFT = 0x38
KEY_RETURN = 0x24
KEY_ESCAPE = 0x35
KEY_Q = 0x0C
KEY_O = 0x1F
KEY_N = 0x2D
KEY_W = 0x0D
KEY_DOWN = 0x7D
KEY_TAB = 0x30

def send_keystroke(client, keycode, command=False, option=False, shift=False):
    """Send a keystroke with optional modifiers"""
    if command:
        client.key_down(KEY_COMMAND)
    if option:
        client.key_down(KEY_OPTION)
    if shift:
        client.key_down(KEY_SHIFT)

    client.key_down(keycode)
    time.sleep(0.05)
    client.key_up(keycode)

    if shift:
        client.key_up(KEY_SHIFT)
    if option:
        client.key_up(KEY_OPTION)
    if command:
        client.key_up(KEY_COMMAND)

    client.wait_ms(100)

def get_screen_hash(client):
    """Get a hash of the current framebuffer."""
    try:
        result = client.screenshot()
        if result and result[4]:
            import hashlib
            return hashlib.md5(result[4]).hexdigest()
    except:
        pass
    return None

def dismiss_startup_dialogs(client, max_attempts=3):
    """
    Press Return to dismiss any startup dialogs (like improper shutdown warning).
    Detects if a dialog was dismissed by checking if the screen changed.
    """
    dismissed_count = 0

    for attempt in range(max_attempts):
        # Get screen hash before
        before_hash = get_screen_hash(client)

        # Press Return to dismiss dialogs
        print(f"  Checking for startup dialogs (attempt {attempt + 1})...")
        client.key_down(KEY_RETURN)
        time.sleep(0.05)
        client.key_up(KEY_RETURN)
        client.wait_ms(300)

        # Get screen hash after
        after_hash = get_screen_hash(client)

        # If screen changed, we probably dismissed a dialog
        if before_hash and after_hash and before_hash != after_hash:
            dismissed_count += 1
            print(f"  Dismissed a dialog!")
            time.sleep(0.3)
        else:
            # No change, no more dialogs
            break

    if dismissed_count > 0:
        print(f"  Dismissed {dismissed_count} dialog(s)")
    else:
        print("  No dialogs detected")

    return dismissed_count

def force_quit_app(client):
    """Send Cmd+Q to quit current app"""
    print("  Sending Cmd+Q to quit current app...")
    send_keystroke(client, KEY_Q, command=True)
    client.wait_ms(500)

def close_all_windows(client):
    """Send Cmd+Option+W to close all windows"""
    print("  Closing all windows (Cmd+Option+W)...")
    client.key_down(KEY_COMMAND)
    client.key_down(KEY_OPTION)
    client.key_down(KEY_W)
    time.sleep(0.05)
    client.key_up(KEY_W)
    client.key_up(KEY_OPTION)
    client.key_up(KEY_COMMAND)
    client.wait_ms(300)

def activate_finder(client):
    """Click on desktop area to make sure Finder is active"""
    print("  Clicking desktop to activate Finder...")
    # Click in a safe area (top-left area of desktop, avoiding menu bar)
    client.click(10, 50, 0)
    client.wait_ms(300)

def select_and_open(client, name):
    """Type name to select item, then Cmd+O to open"""
    print(f"  Pressing Escape to clear any dialogs...")
    send_keystroke(client, KEY_ESCAPE)
    client.wait_ms(200)
    print(f"  Typing '{name}' to select...")
    client.type_text(name)
    client.wait_ms(500)
    print("  Opening with Cmd+O...")
    send_keystroke(client, KEY_O, command=True)
    client.wait_ms(1000)  # Wait for folder/app to open

def select_and_open_with_down(client, name):
    """Type name to select item, then Cmd+Down to open"""
    print(f"  Typing '{name}' to select...")
    client.type_text(name)
    client.wait_ms(400)
    print("  Opening with Cmd+Down...")
    send_keystroke(client, KEY_DOWN, command=True)
    client.wait_ms(800)

def main():
    parser = argparse.ArgumentParser(description='Launch an app inside BasiliskII')
    parser.add_argument('app_name', help='Name of the app to launch')
    parser.add_argument('--socket', default='basilisk_auto', help='Automation socket name (default: basilisk_auto)')
    parser.add_argument('--folder', default='Unix', help='Shared folder name (default: Unix)')
    parser.add_argument('--no-quit', action='store_true', help='Skip quitting current app')
    parser.add_argument('--no-new-window', action='store_true', help='Skip opening new Finder window')

    args = parser.parse_args()

    print(f"Connecting to automation socket: {args.socket}")
    try:
        client = BasiliskClient(args.socket)
    except Exception as e:
        print(f"Error: Could not connect to BasiliskII automation socket.")
        print(f"Make sure BasiliskII is running with: --automation {args.socket}")
        print(f"Details: {e}")
        sys.exit(1)

    print("Connected to BasiliskII!")

    try:
        # Step 0: Dismiss any startup dialogs (like "improper shutdown" warning)
        print("\nStep 0: Checking for startup dialogs...")
        dismiss_startup_dialogs(client)

        # Step 1: Quit current app
        if not args.no_quit:
            print("\nStep 1: Quitting current app...")
            force_quit_app(client)
            # Send it a few times in case of dialogs
            force_quit_app(client)
            client.wait_ms(300)

        # Step 2: Close any open window that might capture keystrokes
        print("\nStep 2: Closing any open window (Cmd+W)...")
        send_keystroke(client, KEY_W, command=True)
        client.wait_ms(300)
        # Do it twice in case there are multiple windows
        send_keystroke(client, KEY_W, command=True)
        client.wait_ms(300)

        # Step 3: Click on desktop to ensure Finder is active with no selection
        print("\nStep 3: Clicking desktop to activate Finder...")
        activate_finder(client)
        client.wait_ms(300)

        # Step 4: Navigate to shared folder
        print(f"\nStep 4: Navigating to '{args.folder}' folder...")
        select_and_open(client, args.folder)

        # Step 5: Select and launch the app
        print(f"\nStep 5: Launching '{args.app_name}'...")
        select_and_open(client, args.app_name)

        print("\nDone! App should be launching.")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        client.close()

if __name__ == "__main__":
    main()
