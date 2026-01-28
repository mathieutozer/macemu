#ifndef GDBSTUB_H
#define GDBSTUB_H

#include "sysdeps.h"
#ifdef __cplusplus
extern "C" {
#endif

#ifdef USE_XCODE
// Stub implementations for macOS Xcode build
inline bool GdbStubInit(uint16 port, bool wait_for_client, bool break_on_start) { return false; }
inline void GdbStubShutdown(void) {}
inline bool GdbStubIsEnabled(void) { return false; }
inline bool GdbStubIsConnected(void) { return false; }
inline void GdbStubOnCpuStart(void) {}
inline void GdbStubMaybeAccept(void) {}
inline bool GdbStubCheckAsyncBreak(void) { return false; }
inline bool GdbStubCheckBreakpoint(uaecptr pc) { return false; }
inline bool GdbStubConsumeSingleStep(void) { return false; }
inline void GdbStubEnterStop(int signal) {}
#else
bool GdbStubInit(uint16 port, bool wait_for_client, bool break_on_start);
void GdbStubShutdown(void);

bool GdbStubIsEnabled(void);
bool GdbStubIsConnected(void);

// Call near CPU start to optionally wait for a client/break at entry.
void GdbStubOnCpuStart(void);

// Non-blocking accept for new clients.
void GdbStubMaybeAccept(void);

// Returns true if a CTRL-C (0x03) break was received.
bool GdbStubCheckAsyncBreak(void);

// Returns true if a breakpoint matches the given PC.
bool GdbStubCheckBreakpoint(uaecptr pc);

// Returns true if a single-step stop is pending and consumes it.
bool GdbStubConsumeSingleStep(void);

// Enter the debugger stop loop (blocks until continue/step).
void GdbStubEnterStop(int signal);
#endif

#ifdef __cplusplus
}
#endif

#endif
