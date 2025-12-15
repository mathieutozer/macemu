#!/usr/bin/env python3
"""
Simple automation client for BasiliskII
"""
import socket
import struct
import os

# Protocol constants
RPC_START = -3000
RPC_END = -3001
RPC_ACK = -3002
RPC_REPLY = -3003
TYPE_INT32 = -2002
TYPE_UINT32 = -2003
TYPE_STRING = -2005
TYPE_ARRAY = -2006
TYPE_CHAR = -2001

# Method IDs
METHOD_KEY_DOWN = 101
METHOD_KEY_UP = 102
METHOD_MOUSE_MOVE = 103
METHOD_MOUSE_DOWN = 104
METHOD_MOUSE_UP = 105
METHOD_GET_SCREEN_SIZE = 106
METHOD_SCREENSHOT = 107
METHOD_TYPE_TEXT = 108
METHOD_CLICK = 109
METHOD_PING = 110
METHOD_WAIT_MS = 111

class BasiliskClient:
    def __init__(self, socket_name):
        self.socket_path = f"/tmp/{socket_name}"
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)

    def close(self):
        self.sock.close()

    def _send_int32(self, val):
        self.sock.send(struct.pack('>i', val))

    def _send_uint32(self, val):
        self.sock.send(struct.pack('>I', val))

    def _recv_int32(self):
        data = self.sock.recv(4)
        if len(data) != 4:
            raise Exception("Failed to receive int32")
        return struct.unpack('>i', data)[0]

    def _recv_uint32(self):
        data = self.sock.recv(4)
        if len(data) != 4:
            raise Exception("Failed to receive uint32")
        return struct.unpack('>I', data)[0]

    def _send_call(self, method_id, *args):
        """Send an RPC call with given method and arguments"""
        self._send_int32(RPC_START)
        self._send_int32(method_id)
        for arg_type, arg_val in args:
            self._send_int32(arg_type)
            if arg_type == TYPE_INT32:
                self._send_int32(arg_val)
            elif arg_type == TYPE_UINT32:
                self._send_uint32(arg_val)
            elif arg_type == TYPE_STRING:
                encoded = arg_val.encode('utf-8')
                self._send_int32(len(encoded))
                self.sock.send(encoded)
        self._send_int32(RPC_END)

    def _recv_reply(self):
        """Receive RPC reply and return list of values"""
        reply = self._recv_int32()
        if reply != RPC_REPLY:
            raise Exception(f"Expected REPLY, got {reply}")

        values = []
        while True:
            type_tag = self._recv_int32()
            if type_tag == RPC_END:
                break
            elif type_tag == TYPE_INT32:
                values.append(self._recv_int32())
            elif type_tag == TYPE_UINT32:
                values.append(self._recv_uint32())
            elif type_tag == TYPE_ARRAY:
                elem_type = self._recv_int32()
                array_len = self._recv_uint32()
                if elem_type == TYPE_CHAR:
                    data = b''
                    while len(data) < array_len:
                        chunk = self.sock.recv(array_len - len(data))
                        if not chunk:
                            break
                        data += chunk
                    values.append(data)

        ack = self._recv_int32()
        if ack != RPC_ACK:
            raise Exception(f"Expected ACK, got {ack}")

        return values

    def ping(self):
        """Test connection"""
        self._send_call(METHOD_PING)
        result = self._recv_reply()
        return result[0] if result else 0

    def get_screen_size(self):
        """Get screen dimensions"""
        self._send_call(METHOD_GET_SCREEN_SIZE)
        result = self._recv_reply()
        return result[0], result[1], result[2]

    def click(self, x, y, button=0):
        """Click at position"""
        self._send_call(METHOD_CLICK,
                       (TYPE_INT32, x),
                       (TYPE_INT32, y),
                       (TYPE_INT32, button))
        self._recv_reply()

    def mouse_move(self, x, y):
        """Move mouse to position"""
        self._send_call(METHOD_MOUSE_MOVE,
                       (TYPE_INT32, x),
                       (TYPE_INT32, y))
        self._recv_reply()

    def mouse_down(self, button=0):
        """Press mouse button"""
        self._send_call(METHOD_MOUSE_DOWN, (TYPE_INT32, button))
        self._recv_reply()

    def mouse_up(self, button=0):
        """Release mouse button"""
        self._send_call(METHOD_MOUSE_UP, (TYPE_INT32, button))
        self._recv_reply()

    def key_down(self, keycode):
        """Press key"""
        self._send_call(METHOD_KEY_DOWN, (TYPE_INT32, keycode))
        self._recv_reply()

    def key_up(self, keycode):
        """Release key"""
        self._send_call(METHOD_KEY_UP, (TYPE_INT32, keycode))
        self._recv_reply()

    def type_text(self, text):
        """Type text string"""
        self._send_call(METHOD_TYPE_TEXT, (TYPE_STRING, text))
        self._recv_reply()

    def wait_ms(self, ms):
        """Wait for specified milliseconds"""
        self._send_call(METHOD_WAIT_MS, (TYPE_INT32, ms))
        self._recv_reply()

    def screenshot(self):
        """Capture screenshot, returns (width, height, depth, bpr, data)"""
        self._send_call(METHOD_SCREENSHOT)
        result = self._recv_reply()
        if len(result) >= 5:
            return result[0], result[1], result[2], result[3], result[4]
        return None


if __name__ == "__main__":
    import sys

    socket_name = sys.argv[1] if len(sys.argv) > 1 else "basilisk_automation.sock"

    print(f"Connecting to {socket_name}...")
    client = BasiliskClient(socket_name)
    print("Connected!")

    print("Testing ping...")
    result = client.ping()
    print(f"Ping result: {result}")

    print("Getting screen size...")
    width, height, depth = client.get_screen_size()
    print(f"Screen: {width}x{height} depth={depth}")

    print("\nAll tests passed!")
    client.close()
