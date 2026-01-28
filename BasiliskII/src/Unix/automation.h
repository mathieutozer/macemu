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
};

// Initialize automation server
// socket_path: Unix domain socket path (e.g., "/tmp/basilisk_automation")
// Returns true on success
#ifdef USE_XCODE
// Stub implementations for macOS Xcode build
inline bool AutomationInit(const char *socket_path) { return false; }
inline void AutomationPoll(int timeout_us) {}
inline void AutomationExit(void) {}
inline bool AutomationEnabled(void) { return false; }
#else
extern bool AutomationInit(const char *socket_path);

// Poll for incoming automation commands
// Should be called periodically from the main loop
// timeout_us: microseconds to wait (0 for non-blocking)
extern void AutomationPoll(int timeout_us);

// Shutdown automation server
extern void AutomationExit(void);

// Check if automation is enabled
extern bool AutomationEnabled(void);
#endif

#endif /* AUTOMATION_H */
