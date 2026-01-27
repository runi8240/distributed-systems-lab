import socket
from typing import Any, Dict

from .protocol import recv_msg, send_msg


def tcp_request(host: str, port: int, req: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        send_msg(sock, req)
        return recv_msg(sock)
