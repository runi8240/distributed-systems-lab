import json
import socket
import struct
from typing import Any, Dict


_HEADER_FMT = ">I"  # 4-byte big-endian unsigned length
_HEADER_SIZE = 4


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("socket closed")
        data.extend(chunk)
    return bytes(data)


def send_msg(sock: socket.socket, obj: Dict[str, Any]) -> None:
    payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    header = struct.pack(_HEADER_FMT, len(payload))
    sock.sendall(header + payload)


def recv_msg(sock: socket.socket) -> Dict[str, Any]:
    header = _recv_exact(sock, _HEADER_SIZE)
    (length,) = struct.unpack(_HEADER_FMT, header)
    payload = _recv_exact(sock, length)
    return json.loads(payload.decode("utf-8"))
