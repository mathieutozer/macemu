#ifndef GDBSTUB_H
#define GDBSTUB_H

#include "sysdeps.h"
#ifdef __cplusplus
extern "C" {
#endif

// GDB stub declarations - implementations in gdbstub.cpp
bool GdbStubInit(uint16 port, bool wait_for_client, bool break_on_start);
void GdbStubShutdown(void);

bool GdbStubIsEnabled(void);
bool GdbStubIsConnected(void);

// Call near CPU start to optionally wait for a client/break at entry.
void GdbStubOnCpuStart(void);

// Non-blocking accept for new clients.
void GdbStubMaybeAccept(void);

// Returns true if a new client just connected (one-shot).
bool GdbStubNewConnection(void);

// Returns true if a CTRL-C (0x03) break was received.
bool GdbStubCheckAsyncBreak(void);

// Returns true if a breakpoint matches the given PC.
bool GdbStubCheckBreakpoint(uaecptr pc);

// Returns true if a single-step stop is pending and consumes it.
bool GdbStubConsumeSingleStep(void);

// Enter the debugger stop loop (blocks until continue/step).
void GdbStubEnterStop(int signal);

// Set/get the CODE resource base address for symbol offset calculation.
void GdbStubSetCodeBase(uaecptr base);
uaecptr GdbStubGetCodeBase(void);

// Track CODE segment runtime addresses for multi-segment apps.
#define GDB_MAX_SEGMENTS 16
void GdbStubAddSegment(uint16 seg_num, uaecptr runtime_addr);

// Rescan jump table to find newly loaded segments
void GdbStubSetA5(uaecptr a5);
void GdbStubRescanJumpTable(void);

// Returns true if there are VMA breakpoints waiting for segment resolution.
bool GdbStubHasPendingBreakpoints(void);

// Set path to .segments file for multi-segment address translation
void GdbStubSetSegmentsFile(const char *path);

#ifdef __cplusplus
}
#endif

#endif
