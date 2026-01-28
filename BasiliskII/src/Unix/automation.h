/*
 *  automation.h - Automation server for headless testing
 *
 *  Basilisk II (C) 1997-2008 Christian Bauer
 *  Automation extension (C) 2024
 *
 *  This program is free software; you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation; either version 2 of the License, or
 *  (at your option) any later version.
 */

#ifndef AUTOMATION_H
#define AUTOMATION_H

#include "sysdeps.h"

// Automation RPC Method IDs (starting at 100 to avoid conflicts)
enum {
    AUTOMATION_METHOD_KEY_DOWN = 100,
    AUTOMATION_METHOD_KEY_UP,
    AUTOMATION_METHOD_MOUSE_MOVE,
    AUTOMATION_METHOD_MOUSE_DOWN,
    AUTOMATION_METHOD_MOUSE_UP,
    AUTOMATION_METHOD_SCREENSHOT,
    AUTOMATION_METHOD_GET_SCREEN_SIZE,
    AUTOMATION_METHOD_TYPE_TEXT,
    AUTOMATION_METHOD_CLICK,
    AUTOMATION_METHOD_WAIT_MS,
    AUTOMATION_METHOD_PING,
    // Recording methods (112+)
    AUTOMATION_METHOD_START_RECORDING,
    AUTOMATION_METHOD_STOP_RECORDING,
    AUTOMATION_METHOD_GET_EVENTS,
    AUTOMATION_METHOD_CLEAR_EVENTS,
};

// Recorded event types
enum {
    RECORD_EVENT_KEY_DOWN = 1,
    RECORD_EVENT_KEY_UP,
    RECORD_EVENT_MOUSE_DOWN,
    RECORD_EVENT_MOUSE_UP,
    RECORD_EVENT_MOUSE_MOVE,
};

// Initialize automation server
// socket_path: Unix domain socket path (e.g., "/tmp/basilisk_automation")
// Returns true on success
extern bool AutomationInit(const char *socket_path);

// Poll for incoming automation commands
// Should be called periodically from the main loop
// timeout_us: microseconds to wait (0 for non-blocking)
extern void AutomationPoll(int timeout_us);

// Shutdown automation server
extern void AutomationExit(void);

// Check if automation is enabled
extern bool AutomationEnabled(void);

// Recording functions - called from video_sdl2.cpp to record user input
extern void AutomationRecordKeyDown(int keycode);
extern void AutomationRecordKeyUp(int keycode);
extern void AutomationRecordMouseDown(int button, int x, int y);
extern void AutomationRecordMouseUp(int button, int x, int y);
extern void AutomationRecordMouseMove(int x, int y);

// Check if recording is active (to avoid recording playback events)
extern bool AutomationIsRecording(void);

#endif /* AUTOMATION_H */
