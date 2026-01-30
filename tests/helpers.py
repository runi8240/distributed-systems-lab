import threading
from typing import Any, Dict

from common.tcp_server import JsonRequestHandler, JsonTCPServer


class ThreadedServer:
    def __init__(self, host: str, port: int, handler_fn):
        class _Server(JsonTCPServer):
            def handle_request_msg(self, req: Dict[str, Any], _addr):
                return handler_fn(req)

        self._server = _Server((host, port), JsonRequestHandler)
        self.host, self.port = self._server.server_address
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
