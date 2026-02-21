import os
import sys
import uuid
from typing import Any, Dict

from flask import Flask, jsonify, request

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from common.grpc_db_client import CustomerDBClient, ProductDBClient


def _resp_to_http(resp: Dict[str, Any]):
    if resp.get("ok"):
        return jsonify({"ok": True, "data": resp.get("data"), "error": None}), 200
    err = resp.get("error") or {}
    code = str(err.get("code", "INTERNAL"))
    status = 400
    if code in ("NOT_LOGGED_IN", "AUTH_FAILED", "SESSION_TIMEOUT"):
        status = 401
    elif code == "NOT_AUTHORIZED":
        status = 403
    elif code == "NOT_FOUND":
        status = 404
    return jsonify({"ok": False, "data": None, "error": err}), status


def create_app(customer_host: str, customer_port: int, product_host: str, product_port: int) -> Flask:
    app = Flask(__name__)
    customer_db = CustomerDBClient(customer_host, customer_port)
    product_db = ProductDBClient(product_host, product_port)

    def request_id() -> str:
        return request.headers.get("X-Request-Id", str(uuid.uuid4()))

    def require_session(session_id: str, req_id: str):
        if not session_id:
            return None, {"ok": False, "error": {"code": "NOT_LOGGED_IN", "message": "session_id required"}}
        sess = customer_db.call("ValidateSession", {"session_id": session_id}, req_id)
        if not sess.get("ok"):
            return None, sess
        if (sess.get("data") or {}).get("role") != "seller":
            return None, {"ok": False, "error": {"code": "NOT_AUTHORIZED", "message": "seller session required"}}
        return sess.get("data") or {}, None

    @app.get("/health")
    def health():
        return jsonify({"ok": True}), 200

    @app.post("/seller/accounts")
    def create_account():
        payload = request.get_json(silent=True) or {}
        resp = customer_db.call(
            "CreateSeller",
            {"name": payload.get("name", ""), "password": payload.get("password", "")},
            request_id(),
        )
        return _resp_to_http(resp)

    @app.post("/seller/login")
    def login():
        payload = request.get_json(silent=True) or {}
        resp = customer_db.call(
            "Login",
            {"role": "seller", "name": payload.get("name", ""), "password": payload.get("password", "")},
            request_id(),
        )
        return _resp_to_http(resp)

    @app.post("/seller/logout")
    def logout():
        payload = request.get_json(silent=True) or {}
        resp = customer_db.call("Logout", {"session_id": payload.get("session_id", "")}, request_id())
        return _resp_to_http(resp)

    @app.get("/seller/items")
    def list_items():
        session_id = request.args.get("session_id", "")
        req_id = request_id()
        sess_data, err = require_session(session_id, req_id)
        if err:
            return _resp_to_http(err)
        resp = product_db.call("DisplayItemsForSale", {"seller_id": int(sess_data["user_id"])}, req_id)
        return _resp_to_http(resp)

    @app.post("/seller/items")
    def register_item():
        payload = request.get_json(silent=True) or {}
        req_id = request_id()
        sess_data, err = require_session(str(payload.get("session_id", "")), req_id)
        if err:
            return _resp_to_http(err)
        resp = product_db.call(
            "RegisterItem",
            {
                "seller_id": int(sess_data["user_id"]),
                "name": payload.get("name", ""),
                "category": payload.get("category", 0),
                "keywords": payload.get("keywords", []),
                "condition": payload.get("condition", ""),
                "price": payload.get("price", 0.0),
                "quantity": payload.get("quantity", 0),
            },
            req_id,
        )
        return _resp_to_http(resp)

    return app


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6004)
    parser.add_argument("--customer-host", default="127.0.0.1")
    parser.add_argument("--customer-port", type=int, default=6001)
    parser.add_argument("--product-host", default="127.0.0.1")
    parser.add_argument("--product-port", type=int, default=6002)
    args = parser.parse_args()

    app = create_app(args.customer_host, args.customer_port, args.product_host, args.product_port)
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
