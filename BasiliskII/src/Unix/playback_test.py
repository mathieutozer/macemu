#!/usr/bin/env python3
"""
Playback Test - Replay recorded tests and compare results

Usage:
    python3 playback_test.py test.json [--report report.json]

The script will:
1. Load the recorded test
2. Replay each action with the same timing
3. Take screenshots at the same points as the recording
4. Compare screenshots to detect differences
5. Generate a report with pass/fail status

Exit codes:
    0 - All screenshots match (test passed)
    1 - Screenshots differ (test failed)
    2 - Error running test
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from basilisk_client import BasiliskClient

try:
    from PIL import Image, ImageChops, ImageStat
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("Warning: PIL not installed, screenshot comparison disabled")

AUTOMATION_SOCKET = "basilisk_auto"


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


def compare_screenshots(path1, path2, threshold=0.01):
    """
    Compare two screenshots.

    Returns (match, diff_percent, diff_image_path)
    - match: True if images are similar enough
    - diff_percent: Percentage of pixels that differ
    - diff_image_path: Path to diff image (if created)
    """
    if not HAS_PIL:
        return True, 0, None

    try:
        img1 = Image.open(path1).convert('RGB')
        img2 = Image.open(path2).convert('RGB')

        if img1.size != img2.size:
            return False, 100.0, None

        # Calculate difference
        diff = ImageChops.difference(img1, img2)
        stat = ImageStat.Stat(diff)

        # Calculate percentage of different pixels
        # Sum of mean differences across RGB channels
        diff_score = sum(stat.mean) / (255 * 3)

        # Save diff image if there's a difference
        diff_path = None
        if diff_score > 0:
            diff_path = path2.parent / f"{path2.stem}_diff.png"
            # Enhance the diff for visibility
            diff_enhanced = diff.point(lambda x: min(255, x * 10))
            diff_enhanced.save(diff_path)

        return diff_score <= threshold, diff_score * 100, diff_path

    except Exception as e:
        print(f"Comparison error: {e}")
        return False, 100.0, None


def playback_test(test_path, report_path=None, speed=1.0):
    """
    Play back a recorded test.

    Args:
        test_path: Path to the test JSON file
        report_path: Path to save the report (optional)
        speed: Playback speed multiplier (1.0 = real time, 2.0 = twice as fast)

    Returns:
        (success, report_dict)
    """
    test_path = Path(test_path)
    test_dir = test_path.parent

    # Load test
    with open(test_path) as f:
        test = json.load(f)

    print(f"Playing back test: {test['name']}")
    print(f"Recorded: {test['recorded']}")
    print(f"Events: {test.get('event_count', len(test['steps']))}")

    # Connect to emulator
    print(f"\nConnecting to emulator...")
    try:
        client = BasiliskClient(AUTOMATION_SOCKET)
    except Exception as e:
        print(f"Failed to connect: {e}")
        return False, {"error": str(e)}

    print("Connected!")

    # Create playback screenshots directory
    playback_dir = test_dir / f"{test_path.stem}_playback_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    playback_dir.mkdir(parents=True, exist_ok=True)

    # Initialize report
    report = {
        "test_name": test['name'],
        "test_file": str(test_path),
        "playback_time": datetime.now().isoformat(),
        "speed": speed,
        "steps": [],
        "screenshots_compared": 0,
        "screenshots_matched": 0,
        "screenshots_failed": 0,
        "passed": True,
    }

    print(f"\nStarting playback...")
    print("="*60)

    last_timestamp = 0
    step_num = 0

    for step in test['steps']:
        step_num += 1
        step_report = {"step": step_num, "action": step.get('action', 'unknown')}

        # Handle timing
        if 'timestamp_ms' in step:
            delay_ms = step['timestamp_ms'] - last_timestamp
            if delay_ms > 0 and speed > 0:
                actual_delay = delay_ms / (speed * 1000)
                time.sleep(actual_delay)
            last_timestamp = step['timestamp_ms']

        action = step.get('action')

        if action == 'screenshot':
            label = step.get('label', f'step_{step_num}')
            playback_screenshot = playback_dir / f"{label}.png"

            if save_screenshot(client, playback_screenshot):
                step_report['screenshot'] = str(playback_screenshot)
                report['screenshots_compared'] += 1

                # Compare with recorded screenshot if available
                if 'file' in step:
                    recorded_screenshot = test_dir / step['file']
                    if recorded_screenshot.exists():
                        match, diff_pct, diff_path = compare_screenshots(
                            recorded_screenshot, playback_screenshot
                        )
                        step_report['compared_to'] = str(recorded_screenshot)
                        step_report['diff_percent'] = round(diff_pct, 2)
                        step_report['match'] = match

                        if match:
                            report['screenshots_matched'] += 1
                            print(f"  [{label}] Screenshot MATCH (diff: {diff_pct:.2f}%)")
                        else:
                            report['screenshots_failed'] += 1
                            report['passed'] = False
                            print(f"  [{label}] Screenshot DIFFER (diff: {diff_pct:.2f}%)")
                            if diff_path:
                                step_report['diff_image'] = str(diff_path)
                    else:
                        print(f"  [{label}] Screenshot taken (no reference to compare)")
            else:
                print(f"  [{label}] Screenshot FAILED")
                step_report['error'] = "Failed to capture screenshot"

        elif action == 'key_down':
            keycode = step['keycode']
            client.key_down(keycode)
            print(f"  Key down: 0x{keycode:02x}")

        elif action == 'key_up':
            keycode = step['keycode']
            client.key_up(keycode)
            print(f"  Key up: 0x{keycode:02x}")

        elif action == 'mouse_down':
            x, y = step['x'], step['y']
            button = step.get('button', 0)
            client.mouse_move(x, y)
            client.wait_ms(10)
            client.mouse_down(button)
            print(f"  Mouse down: button={button} at ({x}, {y})")

        elif action == 'mouse_up':
            x, y = step['x'], step['y']
            button = step.get('button', 0)
            client.mouse_move(x, y)
            client.wait_ms(10)
            client.mouse_up(button)
            print(f"  Mouse up: button={button} at ({x}, {y})")

        elif action == 'click':
            x, y = step['x'], step['y']
            button = step.get('button', 0)
            client.click(x, y, button)
            print(f"  Click: button={button} at ({x}, {y})")

        elif action == 'type':
            text = step['text']
            client.type_text(text)
            print(f"  Type: {repr(text)}")

        elif action == 'wait':
            ms = step['ms']
            client.wait_ms(ms)
            print(f"  Wait: {ms}ms")

        else:
            print(f"  Unknown action: {action}")
            step_report['warning'] = f"Unknown action: {action}"

        report['steps'].append(step_report)

    print("="*60)
    print("Playback complete!")
    print()

    client.close()

    # Summary
    if report['screenshots_compared'] > 0:
        print(f"Screenshots: {report['screenshots_matched']}/{report['screenshots_compared']} matched")
        if report['screenshots_failed'] > 0:
            print(f"  {report['screenshots_failed']} screenshots DIFFER")

    if report['passed']:
        print("\nRESULT: PASSED")
    else:
        print("\nRESULT: FAILED")

    print(f"Playback screenshots saved to: {playback_dir}")

    # Save report
    if report_path:
        report_path = Path(report_path)
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"Report saved to: {report_path}")

    return report['passed'], report


def main():
    parser = argparse.ArgumentParser(description="Play back a recorded BasiliskII test")
    parser.add_argument("test", help="Path to test JSON file")
    parser.add_argument("--report", "-r", help="Path to save report JSON")
    parser.add_argument("--speed", "-s", type=float, default=1.0,
                        help="Playback speed (1.0 = real time, 2.0 = 2x speed)")

    args = parser.parse_args()

    if not os.path.exists(args.test):
        print(f"Error: Test file not found: {args.test}")
        return 2

    success, report = playback_test(args.test, args.report, args.speed)

    if 'error' in report:
        return 2
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
