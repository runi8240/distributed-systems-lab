import socketserver
from typing import Any, Dict

from .protocol import recv_msg, send_msg


class JsonTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class JsonRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while True:
            try:
                req = recv_msg(self.request)
            except Exception:
                break
            resp = self.server.handle_request_msg(req, self.client_address)  # type: ignore[attr-defined]
            if resp is not None:
                try:
                    send_msg(self.request, resp)
                except Exception:
                    break


def run_server(host: str, port: int, handler_fn):
    class _Server(JsonTCPServer):
        def handle_request_msg(self, req: Dict[str, Any], _addr):
            return handler_fn(req)

    with _Server((host, port), JsonRequestHandler) as server:
        server.serve_forever()
