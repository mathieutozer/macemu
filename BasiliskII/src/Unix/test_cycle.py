#!/usr/bin/env python3
"""
Test Cycle Harness for 68k Development

Automates the build-launch-screenshot-analyze cycle for Classic Mac app development.
Designed to be called by Claude Code for iterative debugging without human intervention.

Usage:
    python3 test_cycle.py --build-dir /path/to/build --app-name "MyApp" [options]

Returns JSON with:
    - success: bool
    - stage: where it got to (build/launch/run/screenshot)
    - screenshot_path: path to captured screenshot (if any)
    - process_status: running/crashed/killed
    - error: error message if any
    - crash_log: path to crash log if crashed
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Try to import PIL for image conversion
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Import our automation client
sys.path.insert(0, str(Path(__file__).parent))
from basilisk_client import BasiliskClient

# Configuration
BASILISK_APP = "/Users/tozer/code/macemu/BasiliskII/BasiliskII.app"
AUTOMATION_SOCKET = "basilisk_auto"
LAUNCH_SCRIPT = Path(__file__).parent / "launch_app.py"
SCREENSHOT_DIR = Path("/tmp/basilisk_screenshots")
CRASH_LOG_DIR = Path.home() / "Library/Logs/DiagnosticReports"

def ensure_dirs():
    """Ensure screenshot directory exists."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

def get_basilisk_pid():
    """Get BasiliskII process ID if running."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "BasiliskII"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return int(result.stdout.strip().split('\n')[0])
    except:
        pass
    return None

def is_basilisk_running():
    """Check if BasiliskII is running."""
    return get_basilisk_pid() is not None

def kill_basilisk():
    """Kill BasiliskII process."""
    pid = get_basilisk_pid()
    if pid:
        subprocess.run(["kill", str(pid)], capture_output=True)
        time.sleep(0.5)
        # Force kill if still running
        if is_basilisk_running():
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)
        return True
    return False

def _get_screen_hash_with_client(client):
    """Get a hash of the current framebuffer using an existing client connection."""
    try:
        result = client.screenshot()
        if result and result[4]:
            import hashlib
            return hashlib.md5(result[4]).hexdigest()
    except:
        pass
    return None

def dismiss_startup_dialog(max_attempts=3):
    """
    Press Return to dismiss any startup dialogs (like improper shutdown warning).

    Detects if a dialog was actually dismissed by checking if the screen changed
    after pressing Return. Repeats up to max_attempts times to handle multiple dialogs.

    Returns the number of dialogs dismissed.
    """
    dismissed_count = 0

    try:
        client = BasiliskClient(AUTOMATION_SOCKET)

        for attempt in range(max_attempts):
            # Get screen hash before
            before_hash = _get_screen_hash_with_client(client)

            # Press Return to dismiss dialogs
            client.key_down(0x24)  # KEY_RETURN = 0x24
            time.sleep(0.05)
            client.key_up(0x24)
            client.wait_ms(300)

            # Get screen hash after
            after_hash = _get_screen_hash_with_client(client)

            # If screen changed, we probably dismissed a dialog
            if before_hash and after_hash and before_hash != after_hash:
                dismissed_count += 1
                print(f"Dismissed dialog (attempt {attempt + 1})", file=sys.stderr)
                time.sleep(0.3)  # Wait a bit before checking for more dialogs
            else:
                # No change, no more dialogs
                break

        client.close()
        return dismissed_count
    except Exception as e:
        print(f"Warning: Could not dismiss dialog: {e}", file=sys.stderr)
        return dismissed_count

def launch_basilisk():
    """Launch BasiliskII with automation socket."""
    if is_basilisk_running():
        # Still try to dismiss any dialogs
        dismiss_startup_dialog()
        return True, "Already running"

    try:
        subprocess.Popen(
            ["open", "-a", BASILISK_APP, "--args", "--automation", AUTOMATION_SOCKET],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        # Wait for it to start
        for _ in range(30):  # 15 seconds max
            time.sleep(0.5)
            if is_basilisk_running():
                # Give it a bit more time to fully initialize
                time.sleep(3)
                # Dismiss any startup dialogs (like "improper shutdown" warning)
                dismiss_startup_dialog()
                return True, "Launched"
        return False, "Failed to start within timeout"
    except Exception as e:
        return False, str(e)

def run_build(build_dir: str):
    """Run the build in the specified directory."""
    build_path = Path(build_dir)

    if not build_path.exists():
        return False, f"Build directory does not exist: {build_dir}", None

    # Look for build system
    if (build_path / "Makefile").exists():
        cmd = ["make", "-C", str(build_path)]
    elif (build_path / "CMakeLists.txt").exists():
        cmd = ["cmake", "--build", str(build_path)]
    else:
        return False, "No Makefile or CMakeLists.txt found", None

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode == 0:
            return True, "Build successful", result.stdout
        else:
            return False, f"Build failed: {result.stderr}", result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Build timed out", None
    except Exception as e:
        return False, str(e), None

def launch_app(app_name: str, timeout: int = 10):
    """Launch the app inside the emulator."""
    try:
        result = subprocess.run(
            [sys.executable, str(LAUNCH_SCRIPT), app_name],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode == 0:
            return True, result.stdout
        else:
            return False, result.stderr or result.stdout
    except subprocess.TimeoutExpired:
        return False, "Launch script timed out"
    except Exception as e:
        return False, str(e)

def take_screenshot_via_automation(prefix: str = "test") -> str:
    """Take a screenshot via the automation socket (captures just the emulator)."""
    if not HAS_PIL:
        return None

    ensure_dirs()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = SCREENSHOT_DIR / f"{prefix}_{timestamp}.png"

    try:
        client = BasiliskClient(AUTOMATION_SOCKET)
        result = client.screenshot()
        client.close()

        if result is None:
            return None

        width, height, depth, bytes_per_row, data = result

        if not data or len(data) == 0:
            return None

        # Convert framebuffer to RGB image
        # SDL texture format is ARGB8888 on macOS (32-bit with alpha in high byte)
        # Memory layout per pixel: [A, R, G, B] or as 32-bit: 0xAARRGGBB
        # But PIL reads bytes in order, so we need to handle this correctly
        img = Image.frombytes('RGBA', (width, height), data, 'raw', 'ARGB', bytes_per_row)
        img = img.convert('RGB')
        img.save(str(screenshot_path), 'PNG')

        if screenshot_path.exists():
            return str(screenshot_path)

    except Exception as e:
        print(f"Automation screenshot error: {e}", file=sys.stderr)

    return None

def take_screenshot_via_screencapture(prefix: str = "test") -> str:
    """Take a screenshot of the entire screen using screencapture (fallback)."""
    ensure_dirs()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = SCREENSHOT_DIR / f"{prefix}_{timestamp}.png"

    try:
        subprocess.run(
            ["screencapture", "-x", str(screenshot_path)],
            capture_output=True,
            timeout=5
        )
        if screenshot_path.exists():
            return str(screenshot_path)
    except Exception as e:
        print(f"Screencapture error: {e}", file=sys.stderr)

    return None

def take_screenshot(prefix: str = "test", use_automation: bool = True) -> str:
    """
    Take a screenshot, return path.

    If use_automation=True and the automation socket is available, captures
    just the emulator framebuffer (no screen permissions needed).
    Falls back to screencapture if automation fails.
    """
    if use_automation and is_basilisk_running():
        path = take_screenshot_via_automation(prefix)
        if path:
            return path

    # Fallback to screencapture
    return take_screenshot_via_screencapture(prefix)

def get_recent_crash_log():
    """Get the most recent BasiliskII crash log if any."""
    if not CRASH_LOG_DIR.exists():
        return None

    crash_logs = list(CRASH_LOG_DIR.glob("BasiliskII*.ips")) + \
                 list(CRASH_LOG_DIR.glob("BasiliskII*.crash"))

    if not crash_logs:
        return None

    # Get most recent
    crash_logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    most_recent = crash_logs[0]

    # Only return if it's from the last minute
    if time.time() - most_recent.stat().st_mtime < 60:
        return str(most_recent)

    return None

def get_screenshot_hash():
    """Get a hash of the current emulator framebuffer for comparison."""
    try:
        client = BasiliskClient(AUTOMATION_SOCKET)
        result = client.screenshot()
        client.close()
        if result and result[4]:
            import hashlib
            return hashlib.md5(result[4]).hexdigest()
    except:
        pass
    return None

def wait_and_monitor(duration: int, check_interval: float = 0.5):
    """
    Wait for specified duration while monitoring BasiliskII.
    Returns (still_running, crash_log_path)

    Note: We don't detect "hangs" based on static screens because a GUI app
    waiting for user input will naturally have a static screen. A static
    screen is normal behavior, not a hang.
    """
    start_time = time.time()

    while time.time() - start_time < duration:
        if not is_basilisk_running():
            # Process died - check for crash log
            time.sleep(0.5)  # Give crash reporter time
            crash_log = get_recent_crash_log()
            return False, crash_log

        time.sleep(check_interval)

    return True, None

def run_test_cycle(
    build_dir: str = None,
    app_name: str = None,
    wait_time: int = 5,
    skip_build: bool = False,
    skip_launch: bool = False,
    kill_after: bool = True,
    ensure_emulator: bool = True
):
    """
    Run a complete test cycle.

    Args:
        build_dir: Directory containing the project to build
        app_name: Name of the app to launch in emulator
        wait_time: Seconds to wait after launching app before screenshot
        skip_build: Skip the build step
        skip_launch: Skip launching the app (just screenshot current state)
        kill_after: Kill BasiliskII after the test
        ensure_emulator: Launch BasiliskII if not running

    Returns:
        dict with results
    """
    result = {
        "success": False,
        "stage": "init",
        "screenshot_path": None,
        "process_status": "unknown",
        "error": None,
        "crash_log": None,
        "build_output": None,
        "timestamp": datetime.now().isoformat()
    }

    try:
        # Step 1: Build (if requested)
        if not skip_build and build_dir:
            result["stage"] = "build"
            build_ok, build_msg, build_output = run_build(build_dir)
            result["build_output"] = build_output
            if not build_ok:
                result["error"] = build_msg
                return result

        # Step 2: Ensure emulator is running
        if ensure_emulator:
            result["stage"] = "emulator"
            if not is_basilisk_running():
                launched, msg = launch_basilisk()
                if not launched:
                    result["error"] = f"Failed to launch emulator: {msg}"
                    return result

        # Check emulator status
        if not is_basilisk_running():
            result["error"] = "Emulator not running"
            result["process_status"] = "not_running"
            return result

        result["process_status"] = "running"

        # Step 3: Launch app (if requested)
        if not skip_launch and app_name:
            result["stage"] = "launch"
            launch_ok, launch_msg = launch_app(app_name)
            if not launch_ok:
                result["error"] = f"Failed to launch app: {launch_msg}"
                # Still continue to screenshot to see what happened

        # Step 4: Wait and monitor
        result["stage"] = "wait"
        still_running, crash_log = wait_and_monitor(wait_time)

        if not still_running:
            result["process_status"] = "crashed"
            result["crash_log"] = crash_log
            result["error"] = "Emulator crashed"
            # Try to get a screenshot anyway (might show crash dialog)
            result["screenshot_path"] = take_screenshot("crash")
            return result

        # Step 5: Take screenshot
        result["stage"] = "screenshot"
        screenshot_path = take_screenshot(app_name or "test")
        result["screenshot_path"] = screenshot_path

        if not screenshot_path:
            result["error"] = "Failed to capture screenshot"
            return result

        # Step 6: Kill if requested
        if kill_after:
            result["stage"] = "cleanup"
            kill_basilisk()
            result["process_status"] = "killed"

        result["success"] = True
        result["stage"] = "complete"

    except Exception as e:
        result["error"] = str(e)

    return result

def main():
    parser = argparse.ArgumentParser(description="Test cycle harness for 68k development")
    parser.add_argument("--build-dir", "-b", help="Directory containing the project to build")
    parser.add_argument("--app-name", "-a", help="Name of the app to launch in emulator")
    parser.add_argument("--wait-time", "-w", type=int, default=5, help="Seconds to wait after launch (default: 5)")
    parser.add_argument("--skip-build", action="store_true", help="Skip the build step")
    parser.add_argument("--skip-launch", action="store_true", help="Skip launching the app")
    parser.add_argument("--no-kill", action="store_true", help="Don't kill emulator after test")
    parser.add_argument("--no-emulator", action="store_true", help="Don't auto-launch emulator")
    parser.add_argument("--screenshot-only", action="store_true", help="Just take a screenshot of current state")
    parser.add_argument("--kill", action="store_true", help="Just kill the emulator and exit")
    parser.add_argument("--status", action="store_true", help="Just check emulator status")

    args = parser.parse_args()

    # Handle simple commands
    if args.status:
        running = is_basilisk_running()
        print(json.dumps({"running": running, "pid": get_basilisk_pid()}))
        return 0

    if args.kill:
        killed = kill_basilisk()
        print(json.dumps({"killed": killed}))
        return 0

    if args.screenshot_only:
        path = take_screenshot("manual")
        print(json.dumps({"screenshot_path": path}))
        return 0 if path else 1

    # Run full test cycle
    result = run_test_cycle(
        build_dir=args.build_dir,
        app_name=args.app_name,
        wait_time=args.wait_time,
        skip_build=args.skip_build,
        skip_launch=args.skip_launch,
        kill_after=not args.no_kill,
        ensure_emulator=not args.no_emulator
    )

    print(json.dumps(result, indent=2))
    return 0 if result["success"] else 1

if __name__ == "__main__":
    sys.exit(main())
