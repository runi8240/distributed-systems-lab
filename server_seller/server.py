import os
import sys
from typing import Any, Dict

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from common.tcp_client import tcp_request
from common.tcp_server import run_server


def _ok(req, data=None):
    return {
        "type": "Response",
        "request_id": req.get("request_id"),
        "ok": True,
        "error": None,
        "data": data,
    }


def _err(req, code, message):
    return {
        "type": "Response",
        "request_id": req.get("request_id"),
        "ok": False,
        "error": {"code": code, "message": message},
        "data": None,
    }


def handle_request_factory(customer_host, customer_port, product_host, product_port):
    def db_call(host, port, api, data, request_id):
        return tcp_request(
            host,
            port,
            {
                "type": "Request",
                "request_id": request_id,
                "api": api,
                "data": data,
            },
        )

    def validate_session(session_id, request_id):
        resp = db_call(
            customer_host,
            customer_port,
            "ValidateSession",
            {"session_id": session_id},
            request_id,
        )
        return resp

    def handle(req: Dict[str, Any]):
        api = req.get("api")
        data = req.get("data") or {}
        request_id = req.get("request_id")

        if api == "Ping":
            return _ok(req, {"ok": True})

        if api == "CreateAccount":
            return db_call(customer_host, customer_port, "CreateSeller", data, request_id)

        if api == "Login":
            payload = {"role": "seller", **data}
            return db_call(customer_host, customer_port, "Login", payload, request_id)

        if api == "Logout":
            return db_call(customer_host, customer_port, "Logout", {"session_id": data.get("session_id")}, request_id)

        if api in (
            "GetSellerRating",
            "RegisterItemForSale",
            "ChangeItemPrice",
            "UpdateUnitsForSale",
            "DisplayItemsForSale",
        ):
            session_id = data.get("session_id")
            if not session_id:
                return _err(req, "NOT_LOGGED_IN", "session_id required")
            sess = validate_session(session_id, request_id)
            if not sess.get("ok"):
                return sess
            seller_id = sess["data"]["user_id"]

            if api == "GetSellerRating":
                return db_call(customer_host, customer_port, "GetSellerRating", {"seller_id": seller_id}, request_id)

            if api == "RegisterItemForSale":
                payload = {"seller_id": seller_id, **data}
                return db_call(product_host, product_port, "RegisterItem", payload, request_id)

            if api == "ChangeItemPrice":
                return db_call(product_host, product_port, "ChangeItemPrice", data, request_id)

            if api == "UpdateUnitsForSale":
                return db_call(product_host, product_port, "UpdateUnitsForSale", data, request_id)

            if api == "DisplayItemsForSale":
                return db_call(product_host, product_port, "DisplayItemsForSale", {"seller_id": seller_id}, request_id)

        return _err(req, "UNIMPLEMENTED", f"unknown api {api}")

    return handle


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

    handler = handle_request_factory(args.customer_host, args.customer_port, args.product_host, args.product_port)
    run_server(args.host, args.port, handler)


if __name__ == "__main__":
    main()
