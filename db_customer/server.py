import os
import sys
import time
import uuid
from typing import Any, Dict

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from common.storage import load_json, save_json_atomic
from common.tcp_server import run_server

DEFAULT_STATE = {
    "buyers": {},
    "sellers": {},
    "sessions": {},
    "next_ids": {"buyer": 1, "seller": 1},
}

SESSION_TIMEOUT_SEC = 5 * 60


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


def _find_user_by_name(users: Dict[str, Any], name: str):
    for uid, u in users.items():
        if u.get("name") == name:
            return uid, u
    return None, None


def _new_session(state, role, user_id):
    session_id = str(uuid.uuid4())
    state["sessions"][session_id] = {
        "role": role,
        "user_id": user_id,
        "last_active": time.time(),
    }
    return session_id


def handle_request_factory(state_path: str):
    state = load_json(state_path, DEFAULT_STATE)

    def persist():
        save_json_atomic(state_path, state)

    def handle(req: Dict[str, Any]):
        api = req.get("api")
        data = req.get("data") or {}

        if api == "Ping":
            return _ok(req, {"now": time.time()})

        if api == "CreateBuyer":
            name = data.get("name")
            password = data.get("password")
            if not name or not password:
                return _err(req, "INVALID_ARGUMENT", "name and password required")
            uid = str(state["next_ids"]["buyer"])
            state["next_ids"]["buyer"] += 1
            state["buyers"][uid] = {
                "name": name,
                "password": password,
                "purchases": [],
                "cart": {},
            }
            persist()
            return _ok(req, {"buyer_id": int(uid)})

        if api == "CreateSeller":
            name = data.get("name")
            password = data.get("password")
            if not name or not password:
                return _err(req, "INVALID_ARGUMENT", "name and password required")
            uid = str(state["next_ids"]["seller"])
            state["next_ids"]["seller"] += 1
            state["sellers"][uid] = {
                "name": name,
                "password": password,
                "feedback": {"up": 0, "down": 0},
                "items_sold": 0,
            }
            persist()
            return _ok(req, {"seller_id": int(uid)})

        if api == "Login":
            role = data.get("role")
            name = data.get("name")
            password = data.get("password")
            if role not in ("buyer", "seller"):
                return _err(req, "INVALID_ARGUMENT", "role must be buyer or seller")
            users = state["buyers"] if role == "buyer" else state["sellers"]
            uid, user = _find_user_by_name(users, name)
            if not user or user.get("password") != password:
                return _err(req, "AUTH_FAILED", "invalid credentials")
            session_id = _new_session(state, role, uid)
            persist()
            return _ok(req, {"session_id": session_id, "user_id": int(uid), "role": role})

        if api == "Logout":
            session_id = data.get("session_id")
            if not session_id:
                return _err(req, "INVALID_ARGUMENT", "session_id required")
            state["sessions"].pop(session_id, None)
            persist()
            return _ok(req, {"logged_out": True})

        if api == "ValidateSession":
            session_id = data.get("session_id")
            if not session_id:
                return _err(req, "INVALID_ARGUMENT", "session_id required")
            sess = state["sessions"].get(session_id)
            if not sess:
                return _err(req, "NOT_LOGGED_IN", "invalid session")
            now = time.time()
            if now - sess.get("last_active", 0) > SESSION_TIMEOUT_SEC:
                state["sessions"].pop(session_id, None)
                persist()
                return _err(req, "SESSION_TIMEOUT", "session expired")
            sess["last_active"] = now
            persist()
            return _ok(req, {"role": sess["role"], "user_id": int(sess["user_id"])})

        if api == "GetSellerRating":
            seller_id = str(data.get("seller_id"))
            seller = state["sellers"].get(seller_id)
            if not seller:
                return _err(req, "NOT_FOUND", "seller not found")
            return _ok(req, {"seller_id": int(seller_id), "feedback": seller["feedback"]})

        if api == "GetBuyerPurchases":
            buyer_id = str(data.get("buyer_id"))
            buyer = state["buyers"].get(buyer_id)
            if not buyer:
                return _err(req, "NOT_FOUND", "buyer not found")
            return _ok(req, {"buyer_id": int(buyer_id), "purchases": buyer.get("purchases", [])})

        if api == "GetCart":
            buyer_id = str(data.get("buyer_id"))
            buyer = state["buyers"].get(buyer_id)
            if not buyer:
                return _err(req, "NOT_FOUND", "buyer not found")
            return _ok(req, {"cart": buyer.get("cart", {})})

        if api == "UpdateCart":
            buyer_id = str(data.get("buyer_id"))
            item_id = data.get("item_id")
            delta = int(data.get("quantity_delta", 0))
            if not item_id or delta == 0:
                return _err(req, "INVALID_ARGUMENT", "item_id and quantity_delta required")
            buyer = state["buyers"].get(buyer_id)
            if not buyer:
                return _err(req, "NOT_FOUND", "buyer not found")
            cart = buyer.setdefault("cart", {})
            cur = int(cart.get(item_id, 0))
            new_qty = cur + delta
            if new_qty < 0:
                return _err(req, "INVALID_ARGUMENT", "cart quantity cannot be negative")
            if new_qty == 0:
                cart.pop(item_id, None)
            else:
                cart[item_id] = new_qty
            persist()
            return _ok(req, {"item_id": item_id, "quantity": cart.get(item_id, 0)})

        if api == "ClearCart":
            buyer_id = str(data.get("buyer_id"))
            buyer = state["buyers"].get(buyer_id)
            if not buyer:
                return _err(req, "NOT_FOUND", "buyer not found")
            buyer["cart"] = {}
            persist()
            return _ok(req, {"cleared": True})

        return _err(req, "UNIMPLEMENTED", f"unknown api {api}")

    return handle


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6001)
    parser.add_argument("--state", default="db_customer/state.json")
    args = parser.parse_args()

    handler = handle_request_factory(args.state)
    run_server(args.host, args.port, handler)


if __name__ == "__main__":
    main()
