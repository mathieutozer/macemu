#!/usr/bin/env python3
"""
Record Test - Record user interactions with the BasiliskII emulator

Usage:
    python3 record_test.py --name "My Test" --output test.json

The script will:
1. Connect to the running emulator
2. Take an initial screenshot
3. Start recording user input (clicks, key presses)
4. When you press Ctrl+C (in terminal) or Escape (in emulator), stop recording
5. Save the test to a JSON file with screenshots

The recorded test can be played back with playback_test.py
"""

import argparse
import json
import os
import sys
import signal
import time
from datetime import datetime
from pathlib import Path

# Add this directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from basilisk_client import (
    BasiliskClient,
    RECORD_EVENT_KEY_DOWN,
    RECORD_EVENT_KEY_UP,
    RECORD_EVENT_MOUSE_DOWN,
    RECORD_EVENT_MOUSE_UP,
)

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("Warning: PIL not installed, screenshots will be skipped")

AUTOMATION_SOCKET = "basilisk_auto"
MAC_KEY_ESCAPE = 0x35

# Event type names for readability
EVENT_TYPE_NAMES = {
    RECORD_EVENT_KEY_DOWN: "key_down",
    RECORD_EVENT_KEY_UP: "key_up",
    RECORD_EVENT_MOUSE_DOWN: "mouse_down",
    RECORD_EVENT_MOUSE_UP: "mouse_up",
}


def save_screenshot(client, path):
    """Save a screenshot to the given path"""
    if not HAS_PIL:
        return False

    result = client.screenshot()
    if result is None:
        return False

    width, height, depth, bytes_per_row, data = result
    if not data:
        return False

    try:
        img = Image.frombytes('RGBA', (width, height), data, 'raw', 'ARGB', bytes_per_row)
        img = img.convert('RGB')
        img.save(str(path), 'PNG')
        return True
    except Exception as e:
        print(f"Screenshot error: {e}")
        return False


def format_event(ev):
    """Format an event for display"""
    etype = EVENT_TYPE_NAMES.get(ev['event_type'], f"unknown({ev['event_type']})")
    ts = ev['timestamp_ms']

    if ev['event_type'] in (RECORD_EVENT_KEY_DOWN, RECORD_EVENT_KEY_UP):
        return f"[{ts:6}ms] {etype} keycode=0x{ev['param1']:02x}"
    elif ev['event_type'] in (RECORD_EVENT_MOUSE_DOWN, RECORD_EVENT_MOUSE_UP):
        return f"[{ts:6}ms] {etype} button={ev['param3']} at ({ev['param1']}, {ev['param2']})"
    else:
        return f"[{ts:6}ms] {etype} params=({ev['param1']}, {ev['param2']}, {ev['param3']})"


def record_test(name, output_path, screenshot_interval=None):
    """
    Record a test.

    Args:
        name: Test name
        output_path: Path to save the test JSON
        screenshot_interval: If set, take screenshots every N seconds during recording
    """
    output_path = Path(output_path)
    output_dir = output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create screenshots directory
    screenshots_dir = output_dir / f"{output_path.stem}_screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    print(f"Connecting to emulator...")
    try:
        client = BasiliskClient(AUTOMATION_SOCKET)
    except Exception as e:
        print(f"Failed to connect: {e}")
        print("Make sure BasiliskII is running with --automation basilisk_auto")
        return False

    print(f"Connected!")

    # Take initial screenshot
    initial_screenshot = screenshots_dir / "initial.png"
    if save_screenshot(client, initial_screenshot):
        print(f"Saved initial screenshot: {initial_screenshot}")

    # Start recording
    print("\n" + "="*60)
    print("RECORDING STARTED")
    print("="*60)
    print("Interact with the emulator. Your actions will be recorded.")
    print("Press Ctrl+C in this terminal to stop recording.")
    print("="*60 + "\n")

    client.start_recording()
    recording_start = time.time()
    screenshot_count = 0
    stop_requested = False

    def signal_handler(sig, frame):
        nonlocal stop_requested
        stop_requested = True
        print("\nStopping recording...")

    signal.signal(signal.SIGINT, signal_handler)

    try:
        last_screenshot_time = time.time()
        while not stop_requested:
            time.sleep(0.1)

            # Take periodic screenshots if requested
            if screenshot_interval and (time.time() - last_screenshot_time) >= screenshot_interval:
                screenshot_count += 1
                screenshot_path = screenshots_dir / f"during_{screenshot_count:04d}.png"
                save_screenshot(client, screenshot_path)
                last_screenshot_time = time.time()

    except KeyboardInterrupt:
        pass

    # Stop recording
    event_count = client.stop_recording()
    recording_duration = time.time() - recording_start
    print(f"\nRecording stopped. Duration: {recording_duration:.1f}s, Events: {event_count}")

    # Get recorded events
    events = client.get_events()

    # Take final screenshot
    final_screenshot = screenshots_dir / "final.png"
    if save_screenshot(client, final_screenshot):
        print(f"Saved final screenshot: {final_screenshot}")

    client.close()

    # Convert events to serializable format
    steps = []

    # Add initial screenshot step
    steps.append({
        "action": "screenshot",
        "label": "initial",
        "file": str(initial_screenshot.relative_to(output_dir))
    })

    # Add recorded events
    for ev in events:
        step = {
            "timestamp_ms": ev['timestamp_ms'],
        }

        if ev['event_type'] == RECORD_EVENT_KEY_DOWN:
            step["action"] = "key_down"
            step["keycode"] = ev['param1']
        elif ev['event_type'] == RECORD_EVENT_KEY_UP:
            step["action"] = "key_up"
            step["keycode"] = ev['param1']
        elif ev['event_type'] == RECORD_EVENT_MOUSE_DOWN:
            step["action"] = "mouse_down"
            step["x"] = ev['param1']
            step["y"] = ev['param2']
            step["button"] = ev['param3']
        elif ev['event_type'] == RECORD_EVENT_MOUSE_UP:
            step["action"] = "mouse_up"
            step["x"] = ev['param1']
            step["y"] = ev['param2']
            step["button"] = ev['param3']
        else:
            continue

        steps.append(step)

    # Add final screenshot step
    steps.append({
        "action": "screenshot",
        "label": "final",
        "file": str(final_screenshot.relative_to(output_dir))
    })

    # Build test document
    test_doc = {
        "name": name,
        "recorded": datetime.now().isoformat(),
        "duration_ms": int(recording_duration * 1000),
        "event_count": len(events),
        "screenshots_dir": str(screenshots_dir.relative_to(output_dir)),
        "steps": steps,
    }

    # Save test file
    with open(output_path, 'w') as f:
        json.dump(test_doc, f, indent=2)

    print(f"\nTest saved to: {output_path}")
    print(f"Screenshots in: {screenshots_dir}")

    # Print summary
    print(f"\nRecorded {len(events)} events:")
    for ev in events[:10]:
        print(f"  {format_event(ev)}")
    if len(events) > 10:
        print(f"  ... and {len(events) - 10} more")

    return True


def main():
    parser = argparse.ArgumentParser(description="Record a test for BasiliskII")
    parser.add_argument("--name", "-n", default="Recorded Test", help="Test name")
    parser.add_argument("--output", "-o", default="recorded_test.json", help="Output file path")
    parser.add_argument("--screenshot-interval", "-s", type=float, help="Take screenshots every N seconds")

    args = parser.parse_args()

    success = record_test(args.name, args.output, args.screenshot_interval)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
