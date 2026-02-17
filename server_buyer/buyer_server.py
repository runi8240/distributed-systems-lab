import os
import sys
import threading
from typing import Any, Dict

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

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
    session_carts: Dict[str, Dict[str, int]] = {}
    carts_lock = threading.Lock()

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
            reuse_socket=True,
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

    def require_session(data, request_id):
        session_id = data.get("session_id")
        if not session_id:
            return None, _err({"request_id": request_id}, "NOT_LOGGED_IN", "session_id required")
        sess = validate_session(session_id, request_id)
        if not sess.get("ok"):
            return None, sess
        return sess["data"], None

    def get_session_cart(session_id: str, buyer_id: int, request_id: Any):
        with carts_lock:
            existing = session_carts.get(session_id)
            if existing is not None:
                return dict(existing), None
        resp = db_call(customer_host, customer_port, "GetCart", {"buyer_id": buyer_id}, request_id)
        if not resp.get("ok"):
            return None, resp
        cart = {str(item_id): int(qty) for item_id, qty in resp.get("data", {}).get("cart", {}).items()}
        with carts_lock:
            session_carts[session_id] = dict(cart)
        return cart, None

    def set_session_cart(session_id: str, cart: Dict[str, int]):
        with carts_lock:
            session_carts[session_id] = dict(cart)

    def clear_session_cart(session_id: str):
        with carts_lock:
            session_carts.pop(session_id, None)

    def handle(req: Dict[str, Any]):
        api = req.get("api")
        data = req.get("data") or {}
        request_id = req.get("request_id")

        if api == "Ping":
            return _ok(req, {"ok": True})

        if api == "CreateAccount":
            return db_call(customer_host, customer_port, "CreateBuyer", data, request_id)

        if api == "Login":
            payload = {"role": "buyer", **data}
            return db_call(customer_host, customer_port, "Login", payload, request_id)

        if api == "Logout":
            session_id = data.get("session_id")
            resp = db_call(customer_host, customer_port, "Logout", {"session_id": session_id}, request_id)
            if resp.get("ok") and session_id:
                clear_session_cart(str(session_id))
            return resp

        if api in ("SearchItemsForSale", "GetItem"):
            mapped = {
                "SearchItemsForSale": "SearchItems",
                "GetItem": "GetItem",
            }[api]
            return db_call(product_host, product_port, mapped, data, request_id)

        if api in (
            "AddItemToCart",
            "RemoveItemFromCart",
            "SaveCart",
            "ClearCart",
            "DisplayCart",
            "ProvideFeedback",
            "GetSellerRating",
            "GetBuyerPurchases",
        ):
            sess_data, err = require_session(data, request_id)
            if err:
                return err
            buyer_id = sess_data["user_id"]
            session_id = str(data.get("session_id"))

            if api == "AddItemToCart":
                item_id = data.get("item_id")
                qty = int(data.get("quantity", 0))
                if not item_id or qty <= 0:
                    return _err(req, "INVALID_ARGUMENT", "item_id and positive quantity required")
                avail = db_call(product_host, product_port, "CheckAvailability", {"item_id": item_id, "quantity": qty}, request_id)
                if not avail.get("ok"):
                    return avail
                if not avail["data"]["ok"]:
                    return _err(req, "OUT_OF_STOCK", "requested quantity not available")
                cart, load_err = get_session_cart(session_id, buyer_id, request_id)
                if load_err:
                    return load_err
                new_qty = int(cart.get(item_id, 0)) + qty
                cart[item_id] = new_qty
                set_session_cart(session_id, cart)
                return _ok(req, {"item_id": item_id, "quantity": new_qty})

            if api == "RemoveItemFromCart":
                item_id = data.get("item_id")
                qty = int(data.get("quantity", 0))
                if not item_id or qty <= 0:
                    return _err(req, "INVALID_ARGUMENT", "item_id and positive quantity required")
                cart, load_err = get_session_cart(session_id, buyer_id, request_id)
                if load_err:
                    return load_err
                cur_qty = int(cart.get(item_id, 0))
                new_qty = cur_qty - qty
                if new_qty < 0:
                    return _err(req, "INVALID_ARGUMENT", "cart quantity cannot be negative")
                if new_qty == 0:
                    cart.pop(item_id, None)
                else:
                    cart[item_id] = new_qty
                set_session_cart(session_id, cart)
                return _ok(req, {"item_id": item_id, "quantity": max(new_qty, 0)})

            if api == "SaveCart":
                cart, load_err = get_session_cart(session_id, buyer_id, request_id)
                if load_err:
                    return load_err
                clear_resp = db_call(customer_host, customer_port, "ClearCart", {"buyer_id": buyer_id}, request_id)
                if not clear_resp.get("ok"):
                    return clear_resp
                for item_id, qty in cart.items():
                    upd = db_call(
                        customer_host,
                        customer_port,
                        "UpdateCart",
                        {"buyer_id": buyer_id, "item_id": item_id, "quantity_delta": int(qty)},
                        request_id,
                    )
                    if not upd.get("ok"):
                        return upd
                return db_call(customer_host, customer_port, "SaveCart", {"buyer_id": buyer_id}, request_id)

            if api == "ClearCart":
                set_session_cart(session_id, {})
                return _ok(req, {"cleared": True})

            if api == "DisplayCart":
                cart, load_err = get_session_cart(session_id, buyer_id, request_id)
                if load_err:
                    return load_err
                return _ok(req, {"cart": cart})

            if api == "ProvideFeedback":
                return db_call(product_host, product_port, "ProvideFeedback", data, request_id)

            if api == "GetSellerRating":
                return db_call(customer_host, customer_port, "GetSellerRating", data, request_id)

            if api == "GetBuyerPurchases":
                return db_call(customer_host, customer_port, "GetBuyerPurchases", {"buyer_id": buyer_id}, request_id)

        return _err(req, "UNIMPLEMENTED", f"unknown api {api}")

    return handle


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6003)
    parser.add_argument("--customer-host", default="127.0.0.1")
    parser.add_argument("--customer-port", type=int, default=6001)
    parser.add_argument("--product-host", default="127.0.0.1")
    parser.add_argument("--product-port", type=int, default=6002)
    args = parser.parse_args()

    handler = handle_request_factory(args.customer_host, args.customer_port, args.product_host, args.product_port)
    run_server(args.host, args.port, handler)


if __name__ == "__main__":
    main()
