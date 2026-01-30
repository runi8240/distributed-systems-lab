import socket
import threading
from typing import Any, Dict

from .protocol import recv_msg, send_msg


_tls = threading.local()


def _get_pooled_socket(host: str, port: int, timeout: float) -> socket.socket:
    key = (host, port)
    pool = getattr(_tls, "pool", None)
    if pool is None:
        pool = {}
        _tls.pool = pool
    sock = pool.get(key)
    if sock is None:
        sock = socket.create_connection((host, port), timeout=timeout)
        pool[key] = sock
    sock.settimeout(timeout)
    return sock


def _drop_pooled_socket(host: str, port: int) -> None:
    pool = getattr(_tls, "pool", None)
    if not pool:
        return
    sock = pool.pop((host, port), None)
    if sock is not None:
        try:
            sock.close()
        except Exception:
            pass


def tcp_request(
    host: str,
    port: int,
    req: Dict[str, Any],
    timeout: float = 5.0,
    reuse_socket: bool = False,
) -> Dict[str, Any]:
    if not reuse_socket:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            send_msg(sock, req)
            return recv_msg(sock)

    sock = _get_pooled_socket(host, port, timeout)
    try:
        send_msg(sock, req)
        return recv_msg(sock)
    except (OSError, ConnectionError):
        _drop_pooled_socket(host, port)
        sock = _get_pooled_socket(host, port, timeout)
        send_msg(sock, req)
        return recv_msg(sock)
