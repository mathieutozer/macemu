/*
 *  automation.cpp - Automation server for headless testing
 *
 *  Basilisk II (C) 1997-2008 Christian Bauer
 *  Automation extension (C) 2024
 *
 *  This program is free software; you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation; either version 2 of the License, or
 *  (at your option) any later version.
 */

#include "sysdeps.h"
#include "automation.h"
#include "adb.h"
#include "video.h"

#include <unistd.h>
#include <string.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <fcntl.h>
#include <errno.h>

#define DEBUG 1
#include "debug.h"

// Simple socket server (bypassing RPC library)
static int server_socket = -1;
static int client_socket = -1;
static pthread_t automation_thread;
static volatile bool automation_thread_running = false;
static char socket_path[256];
static void *automation_thread_func(void *arg);

// External functions to access framebuffer (defined in video_sdl.cpp)
extern uint8 *AutomationGetFrameBuffer(void);
extern uint32 AutomationGetFrameBufferSize(void);
extern uint32 AutomationGetScreenWidth(void);
extern uint32 AutomationGetScreenHeight(void);
extern uint32 AutomationGetScreenDepth(void);
extern uint32 AutomationGetBytesPerRow(void);
extern void AutomationLockFrameBuffer(void);
extern void AutomationUnlockFrameBuffer(void);

// Global variables
static bool automation_enabled = false;

// Mac keycode for common keys (partial list - expand as needed)
// These are hardware-independent Mac keycodes
static const int MAC_KEY_A = 0x00;
static const int MAC_KEY_RETURN = 0x24;
static const int MAC_KEY_TAB = 0x30;
static const int MAC_KEY_SPACE = 0x31;
static const int MAC_KEY_DELETE = 0x33;  // Backspace
static const int MAC_KEY_ESCAPE = 0x35;
static const int MAC_KEY_COMMAND = 0x37;
static const int MAC_KEY_SHIFT = 0x38;
static const int MAC_KEY_CAPSLOCK = 0x39;
static const int MAC_KEY_OPTION = 0x3A;
static const int MAC_KEY_CONTROL = 0x3B;
static const int MAC_KEY_LEFT = 0x7B;
static const int MAC_KEY_RIGHT = 0x7C;
static const int MAC_KEY_DOWN = 0x7D;
static const int MAC_KEY_UP = 0x7E;

// ASCII to Mac keycode mapping (lowercase letters)
static int ascii_to_keycode[128] = {
    -1, -1, -1, -1, -1, -1, -1, -1,  // 0-7
    0x33, 0x30, 0x24, -1, -1, 0x24, -1, -1,  // 8-15 (BS, TAB, LF, CR)
    -1, -1, -1, -1, -1, -1, -1, -1,  // 16-23
    -1, -1, -1, 0x35, -1, -1, -1, -1,  // 24-31 (ESC)
    0x31, 0x12, 0x27, 0x14, 0x15, 0x17, 0x1A, 0x27,  // 32-39 (space, !, ", #, $, %, &, ')
    0x19, 0x1D, 0x1C, 0x18, 0x2B, 0x1B, 0x2F, 0x2C,  // 40-47 ( (, ), *, +, ,, -, ., / )
    0x1D, 0x12, 0x13, 0x14, 0x15, 0x17, 0x16, 0x1A,  // 48-55 (0-7)
    0x1C, 0x19, 0x29, 0x29, 0x2B, 0x18, 0x2F, 0x2C,  // 56-63 (8, 9, :, ;, <, =, >, ?)
    0x13, 0x00, 0x0B, 0x08, 0x02, 0x0E, 0x03, 0x05,  // 64-71 (@, A-G)
    0x04, 0x22, 0x26, 0x28, 0x25, 0x2E, 0x2D, 0x1F,  // 72-79 (H-O)
    0x23, 0x0C, 0x0F, 0x01, 0x11, 0x20, 0x09, 0x0D,  // 80-87 (P-W)
    0x07, 0x10, 0x06, 0x21, 0x2A, 0x1E, 0x16, 0x1B,  // 88-95 (X-Z, [, \, ], ^, _)
    0x32, 0x00, 0x0B, 0x08, 0x02, 0x0E, 0x03, 0x05,  // 96-103 (`, a-g)
    0x04, 0x22, 0x26, 0x28, 0x25, 0x2E, 0x2D, 0x1F,  // 104-111 (h-o)
    0x23, 0x0C, 0x0F, 0x01, 0x11, 0x20, 0x09, 0x0D,  // 112-119 (p-w)
    0x07, 0x10, 0x06, 0x21, 0x2A, 0x1E, 0x32, -1,   // 120-127 (x-z, {, |, }, ~, DEL)
};

// Characters that require shift
static bool needs_shift[128] = {
    0,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,0,  // 0-15
    0,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,0,  // 16-31
    0,1,1,1,1,1,1,0, 1,1,1,1,0,0,0,0,  // 32-47: space,!,",#,$,%,&,',(,),*,+,comma,-,.,/
    0,0,0,0,0,0,0,0, 0,0,1,0,1,0,1,1,  // 48-63: 0-9,:,;,<,=,>,?
    1,1,1,1,1,1,1,1, 1,1,1,1,1,1,1,1,  // 64-79: @,A-O
    1,1,1,1,1,1,1,1, 1,1,1,0,0,0,1,1,  // 80-95: P-Z,[,\,],^,_
    0,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,0,  // 96-111: `,a-o
    0,0,0,0,0,0,0,0, 0,0,0,1,1,1,1,0,  // 112-127: p-z,{,|,},~,DEL
};

// Method IDs (must match basilisk_client.py)
#define METHOD_KEY_DOWN       101
#define METHOD_KEY_UP         102
#define METHOD_MOUSE_MOVE     103
#define METHOD_MOUSE_DOWN     104
#define METHOD_MOUSE_UP       105
#define METHOD_GET_SCREEN_SIZE 106
#define METHOD_SCREENSHOT     107
#define METHOD_TYPE_TEXT      108
#define METHOD_CLICK          109
#define METHOD_PING           110
#define METHOD_WAIT_MS        111

// Protocol constants
#define RPC_START    (-3000)
#define RPC_END      (-3001)
#define RPC_ACK      (-3002)
#define RPC_REPLY    (-3003)
#define TYPE_INT32   (-2002)
#define TYPE_UINT32  (-2003)
#define TYPE_STRING  (-2005)
#define TYPE_ARRAY   (-2006)
#define TYPE_CHAR    (-2001)

bool AutomationInit(const char *path)
{
    if (path == NULL) {
        return false;
    }

    // Build full socket path in /tmp/
    snprintf(socket_path, sizeof(socket_path), "/tmp/%s", path);
    D(bug("AutomationInit: socket_path=%s\n", socket_path));

    // Remove any existing socket file
    unlink(socket_path);

    // Create socket
    server_socket = socket(AF_UNIX, SOCK_STREAM, 0);
    if (server_socket < 0) {
        perror("automation socket");
        return false;
    }

    // Bind socket
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, socket_path, sizeof(addr.sun_path) - 1);

    if (bind(server_socket, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("automation bind");
        close(server_socket);
        server_socket = -1;
        return false;
    }

    // Listen
    if (listen(server_socket, 1) < 0) {
        perror("automation listen");
        close(server_socket);
        server_socket = -1;
        return false;
    }

    automation_enabled = true;

    // Start automation thread
    automation_thread_running = true;
    if (pthread_create(&automation_thread, NULL, automation_thread_func, NULL) != 0) {
        perror("automation thread");
        close(server_socket);
        server_socket = -1;
        automation_enabled = false;
        return false;
    }

    D(bug("AutomationInit: server ready\n"));
    return true;
}

// Helper functions for socket I/O
static bool send_int32(int sock, int32_t val) {
    int32_t nval = htonl(val);
    return send(sock, &nval, 4, 0) == 4;
}

static bool recv_int32(int sock, int32_t *val) {
    int32_t nval;
    ssize_t n = recv(sock, &nval, 4, 0);
    if (n != 4) {
        printf("recv_int32: recv returned %zd, errno=%d (%s)\n", n, errno, strerror(errno));
        fflush(stdout);
        return false;
    }
    *val = ntohl(nval);
    return true;
}

static bool send_uint32(int sock, uint32_t val) {
    uint32_t nval = htonl(val);
    return send(sock, &nval, 4, 0) == 4;
}

// Read a string from socket (TYPE_STRING, length, chars)
static char *recv_string(int sock) {
    int32_t len;
    if (!recv_int32(sock, &len)) {
        printf("recv_string: failed to read length\n");
        fflush(stdout);
        return NULL;
    }
    printf("recv_string: len=%d\n", len);
    fflush(stdout);
    if (len < 0 || len > 10000) {
        printf("recv_string: invalid length\n");
        fflush(stdout);
        return NULL;
    }
    char *str = (char *)malloc(len + 1);
    if (!str) return NULL;
    if (len > 0) {
        ssize_t received = 0;
        while (received < len) {
            ssize_t n = recv(sock, str + received, len - received, 0);
            printf("recv_string: recv returned %zd\n", n);
            fflush(stdout);
            if (n <= 0) { free(str); return NULL; }
            received += n;
        }
    }
    str[len] = '\0';
    printf("recv_string: result='%s'\n", str);
    fflush(stdout);
    return str;
}

// Send reply with no return values
static void send_empty_reply(int sock) {
    send_int32(sock, RPC_REPLY);
    send_int32(sock, RPC_END);
    send_int32(sock, RPC_ACK);
}

// Handle a single command from client
// Returns true if command was handled, false if client disconnected
static bool handle_command(int sock) {
    int32_t start, method_id;

    // Read: START, method_id
    printf("handle_command: reading RPC_START...\n");
    fflush(stdout);
    if (!recv_int32(sock, &start)) {
        printf("handle_command: failed to read RPC_START\n");
        fflush(stdout);
        return false;
    }
    printf("handle_command: got start=%d (expected %d)\n", start, RPC_START);
    fflush(stdout);
    if (start != RPC_START) {
        printf("handle_command: start != RPC_START, returning false\n");
        fflush(stdout);
        return false;
    }
    if (!recv_int32(sock, &method_id)) return false;

    D(bug("Automation: received method %d\n", method_id));

    // Handle method based on ID
    switch (method_id) {
        case METHOD_PING: {
            // Skip to END
            int32_t val;
            while (recv_int32(sock, &val) && val != RPC_END) {}

            D(bug("Automation: ping\n"));
            send_int32(sock, RPC_REPLY);
            send_int32(sock, TYPE_INT32);
            send_int32(sock, 1);
            send_int32(sock, RPC_END);
            send_int32(sock, RPC_ACK);
            break;
        }

        case METHOD_GET_SCREEN_SIZE: {
            // Skip to END
            int32_t val;
            while (recv_int32(sock, &val) && val != RPC_END) {}

            D(bug("Automation: get_screen_size\n"));
            send_int32(sock, RPC_REPLY);
            send_int32(sock, TYPE_UINT32);
            send_uint32(sock, AutomationGetScreenWidth());
            send_int32(sock, TYPE_UINT32);
            send_uint32(sock, AutomationGetScreenHeight());
            send_int32(sock, TYPE_UINT32);
            send_uint32(sock, AutomationGetScreenDepth());
            send_int32(sock, RPC_END);
            send_int32(sock, RPC_ACK);
            break;
        }

        case METHOD_KEY_DOWN: {
            int32_t type, keycode;
            recv_int32(sock, &type);  // TYPE_INT32
            recv_int32(sock, &keycode);
            int32_t end;
            recv_int32(sock, &end);  // END

            D(bug("Automation: key_down(%d)\n", keycode));
            ADBKeyDown(keycode);
            send_empty_reply(sock);
            break;
        }

        case METHOD_KEY_UP: {
            int32_t type, keycode;
            recv_int32(sock, &type);
            recv_int32(sock, &keycode);
            int32_t end;
            recv_int32(sock, &end);

            D(bug("Automation: key_up(%d)\n", keycode));
            ADBKeyUp(keycode);
            send_empty_reply(sock);
            break;
        }

        case METHOD_MOUSE_MOVE: {
            int32_t type, x, y;
            recv_int32(sock, &type);
            recv_int32(sock, &x);
            recv_int32(sock, &type);
            recv_int32(sock, &y);
            int32_t end;
            recv_int32(sock, &end);

            D(bug("Automation: mouse_move(%d, %d)\n", x, y));
            ADBMouseMoved(x, y);
            send_empty_reply(sock);
            break;
        }

        case METHOD_MOUSE_DOWN: {
            int32_t type, button;
            recv_int32(sock, &type);
            recv_int32(sock, &button);
            int32_t end;
            recv_int32(sock, &end);

            D(bug("Automation: mouse_down(%d)\n", button));
            ADBMouseDown(button);
            send_empty_reply(sock);
            break;
        }

        case METHOD_MOUSE_UP: {
            int32_t type, button;
            recv_int32(sock, &type);
            recv_int32(sock, &button);
            int32_t end;
            recv_int32(sock, &end);

            D(bug("Automation: mouse_up(%d)\n", button));
            ADBMouseUp(button);
            send_empty_reply(sock);
            break;
        }

        case METHOD_CLICK: {
            int32_t type, x, y, button;
            recv_int32(sock, &type);
            recv_int32(sock, &x);
            recv_int32(sock, &type);
            recv_int32(sock, &y);
            recv_int32(sock, &type);
            recv_int32(sock, &button);
            int32_t end;
            recv_int32(sock, &end);

            D(bug("Automation: click(%d, %d, %d)\n", x, y, button));
            ADBMouseMoved(x, y);
            usleep(10000);
            ADBMouseDown(button);
            usleep(50000);
            ADBMouseUp(button);
            send_empty_reply(sock);
            break;
        }

        case METHOD_TYPE_TEXT: {
            int32_t type;
            if (!recv_int32(sock, &type)) {
                printf("TYPE_TEXT: failed to read type tag\n");
                fflush(stdout);
                return false;  // Protocol error, disconnect
            }
            printf("TYPE_TEXT: type tag = %d (expected %d)\n", type, TYPE_STRING);
            fflush(stdout);

            char *text = recv_string(sock);
            if (!text) {
                printf("TYPE_TEXT: recv_string failed\n");
                fflush(stdout);
                return false;  // Protocol error, disconnect
            }

            int32_t end;
            if (!recv_int32(sock, &end)) {
                printf("TYPE_TEXT: failed to read END tag\n");
                fflush(stdout);
                free(text);
                return false;
            }

            D(bug("Automation: type_text(\"%s\")\n", text));
            for (int i = 0; text[i] != '\0'; i++) {
                unsigned char c = text[i];
                if (c < 128) {
                    int keycode = ascii_to_keycode[c];
                    if (keycode >= 0) {
                        if (needs_shift[c]) {
                            ADBKeyDown(MAC_KEY_SHIFT);
                        }
                        ADBKeyDown(keycode);
                        usleep(10000);
                        ADBKeyUp(keycode);
                        if (needs_shift[c]) {
                            ADBKeyUp(MAC_KEY_SHIFT);
                        }
                        usleep(20000);
                    }
                }
            }
            free(text);
            send_empty_reply(sock);
            break;
        }

        case METHOD_WAIT_MS: {
            int32_t type, ms;
            recv_int32(sock, &type);
            recv_int32(sock, &ms);
            int32_t end;
            recv_int32(sock, &end);

            D(bug("Automation: wait_ms(%d)\n", ms));
            if (ms > 0 && ms < 60000) {
                usleep(ms * 1000);
            }
            send_empty_reply(sock);
            break;
        }

        case METHOD_SCREENSHOT: {
            int32_t end;
            recv_int32(sock, &end);  // END

            D(bug("Automation: screenshot\n"));
            // Note: Skipping lock to avoid potential deadlock with main thread
            // AutomationLockFrameBuffer();

            uint8 *buffer = AutomationGetFrameBuffer();
            uint32 size = AutomationGetFrameBufferSize();
            uint32 width = AutomationGetScreenWidth();
            uint32 height = AutomationGetScreenHeight();
            uint32 depth = AutomationGetScreenDepth();
            uint32 bytes_per_row = AutomationGetBytesPerRow();

            D(bug("Screenshot: %ux%u depth=%u bpr=%u size=%u buffer=%p\n",
                  width, height, depth, bytes_per_row, size, buffer));

            send_int32(sock, RPC_REPLY);
            send_int32(sock, TYPE_UINT32);
            send_uint32(sock, width);
            send_int32(sock, TYPE_UINT32);
            send_uint32(sock, height);
            send_int32(sock, TYPE_UINT32);
            send_uint32(sock, depth);
            send_int32(sock, TYPE_UINT32);
            send_uint32(sock, bytes_per_row);
            send_int32(sock, TYPE_ARRAY);
            send_int32(sock, TYPE_CHAR);
            send_uint32(sock, size);

            // Send framebuffer data in chunks to handle partial sends
            if (buffer && size > 0) {
                uint32 sent = 0;
                const uint32 chunk_size = 32768;  // 32KB chunks
                while (sent < size) {
                    uint32 to_send = (size - sent > chunk_size) ? chunk_size : (size - sent);
                    ssize_t n = send(sock, buffer + sent, to_send, MSG_NOSIGNAL);
                    if (n < 0) {
                        if (errno == EAGAIN || errno == EWOULDBLOCK) {
                            usleep(1000);  // Brief pause if buffer full
                            continue;
                        }
                        D(bug("Screenshot send error at %u/%u: %s\n", sent, size, strerror(errno)));
                        break;
                    } else if (n == 0) {
                        D(bug("Screenshot send returned 0 at %u/%u\n", sent, size));
                        break;
                    }
                    sent += n;
                }
                D(bug("Screenshot: sent %u/%u bytes\n", sent, size));
            }

            // AutomationUnlockFrameBuffer();

            send_int32(sock, RPC_END);
            send_int32(sock, RPC_ACK);
            break;
        }

        default: {
            // Unknown command - skip to END and send empty reply
            int32_t val;
            while (recv_int32(sock, &val) && val != RPC_END) {}
            D(bug("Automation: unknown method %d\n", method_id));
            send_empty_reply(sock);
            break;
        }
    }
    return true;
}

// Automation thread function
static void *automation_thread_func(void *arg)
{
    D(bug("Automation thread started\n"));

    // Make server socket non-blocking
    int flags = fcntl(server_socket, F_GETFL, 0);
    fcntl(server_socket, F_SETFL, flags | O_NONBLOCK);

    // Wait longer for emulator to initialize fully
    sleep(5);

    while (automation_thread_running) {
        // Use select() to wait for connection with timeout
        fd_set rfds;
        struct timeval tv;
        FD_ZERO(&rfds);
        FD_SET(server_socket, &rfds);
        tv.tv_sec = 0;
        tv.tv_usec = 500000;  // 500ms timeout

        int ret = select(server_socket + 1, &rfds, NULL, NULL, &tv);
        if (ret <= 0) {
            // Timeout or error - loop and check automation_thread_running
            continue;
        }

        // Accept connection (non-blocking)
        struct sockaddr_un client_addr;
        socklen_t client_len = sizeof(client_addr);
        client_socket = accept(server_socket, (struct sockaddr *)&client_addr, &client_len);

        if (client_socket < 0) {
            continue;
        }

        // Set client socket to blocking mode (it may inherit non-blocking from server)
        int flags = fcntl(client_socket, F_GETFL, 0);
        fcntl(client_socket, F_SETFL, flags & ~O_NONBLOCK);

        printf("Automation: client connected (fd=%d)\n", client_socket);
        fflush(stdout);

        // Handle commands until client disconnects
        while (automation_thread_running) {
            FD_ZERO(&rfds);
            FD_SET(client_socket, &rfds);
            tv.tv_sec = 0;
            tv.tv_usec = 100000;  // 100ms

            ret = select(client_socket + 1, &rfds, NULL, NULL, &tv);
            if (ret > 0) {
                printf("Automation: handling command...\n");
                fflush(stdout);
                if (!handle_command(client_socket)) {
                    printf("Automation: handle_command failed\n");
                    fflush(stdout);
                    break;  // Client disconnected
                }
                printf("Automation: command handled successfully\n");
                fflush(stdout);
            } else if (ret < 0) {
                printf("Automation: select error (errno=%d)\n", errno);
                fflush(stdout);
                break;  // Error
            }
        }

        close(client_socket);
        client_socket = -1;
        printf("Automation: client disconnected\n");
        fflush(stdout);
    }

    D(bug("Automation thread exiting\n"));
    return NULL;
}

void AutomationPoll(int timeout_us)
{
    // Polling is now handled by the automation thread
    // This function is kept for API compatibility but does nothing
}

void AutomationExit(void)
{
    D(bug("AutomationExit: stopping...\n"));

    // Stop automation thread
    automation_thread_running = false;

    // Close sockets to unblock accept()
    if (client_socket >= 0) {
        close(client_socket);
        client_socket = -1;
    }
    if (server_socket >= 0) {
        close(server_socket);
        server_socket = -1;
    }

    // Wait for thread to exit
    pthread_join(automation_thread, NULL);

    // Clean up socket file
    if (socket_path[0] != '\0') {
        unlink(socket_path);
    }

    automation_enabled = false;
    D(bug("AutomationExit: server stopped\n"));
}

bool AutomationEnabled(void)
{
    return automation_enabled;
}
