import os
import sys
import time
from typing import Any, Dict, List

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from common.storage import load_json, save_json_atomic
from common.tcp_server import run_server

DEFAULT_STATE = {
    "items": {},
    "next_item_id_by_category": {},
}


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


def _assign_item_id(state: Dict[str, Any], category: int) -> str:
    key = str(category)
    next_id = int(state["next_item_id_by_category"].get(key, 1))
    state["next_item_id_by_category"][key] = next_id + 1
    return f"{category}:{next_id}"


def _keyword_score(item_keywords: List[str], query_keywords: List[str]) -> int:
    if not query_keywords:
        return 0
    item_set = {k.lower() for k in item_keywords}
    return sum(1 for k in query_keywords if k.lower() in item_set)


def handle_request_factory(state_path: str):
    state = load_json(state_path, DEFAULT_STATE)

    def persist():
        save_json_atomic(state_path, state)

    def handle(req: Dict[str, Any]):
        api = req.get("api")
        data = req.get("data") or {}

        if api == "Ping":
            return _ok(req, {"now": time.time()})

        if api == "RegisterItem":
            name = data.get("name")
            category = data.get("category")
            keywords = data.get("keywords", [])
            condition = data.get("condition")
            price = data.get("price")
            quantity = data.get("quantity")
            seller_id = data.get("seller_id")
            if None in (name, category, condition, price, quantity, seller_id):
                return _err(req, "INVALID_ARGUMENT", "missing required item fields")
            item_id = _assign_item_id(state, int(category))
            state["items"][item_id] = {
                "item_id": item_id,
                "name": name,
                "category": int(category),
                "keywords": keywords,
                "condition": condition,
                "price": float(price),
                "quantity": int(quantity),
                "seller_id": int(seller_id),
                "feedback": {"up": 0, "down": 0},
            }
            persist()
            return _ok(req, {"item_id": item_id})

        if api == "ChangeItemPrice":
            item_id = data.get("item_id")
            price = data.get("price")
            item = state["items"].get(item_id)
            if not item:
                return _err(req, "NOT_FOUND", "item not found")
            item["price"] = float(price)
            persist()
            return _ok(req, {"item_id": item_id, "price": item["price"]})

        if api == "UpdateUnitsForSale":
            item_id = data.get("item_id")
            quantity_delta = data.get("quantity_delta")
            if item_id is None or quantity_delta is None:
                return _err(req, "INVALID_ARGUMENT", "item_id and quantity_delta required")
            item = state["items"].get(item_id)
            if not item:
                return _err(req, "NOT_FOUND", "item not found")
            new_qty = int(item["quantity"]) + int(quantity_delta)
            if new_qty < 0:
                return _err(req, "INVALID_ARGUMENT", "quantity cannot be negative")
            item["quantity"] = new_qty
            persist()
            return _ok(req, {"item_id": item_id, "quantity": new_qty})

        if api == "DisplayItemsForSale":
            seller_id = int(data.get("seller_id"))
            items = [i for i in state["items"].values() if i.get("seller_id") == seller_id]
            return _ok(req, {"items": items})

        if api == "SearchItems":
            category = data.get("category")
            keywords = data.get("keywords", [])
            if category is None:
                return _err(req, "INVALID_ARGUMENT", "category required")
            matches = []
            for item in state["items"].values():
                if int(item.get("category")) != int(category):
                    continue
                if int(item.get("quantity", 0)) <= 0:
                    continue
                score = _keyword_score(item.get("keywords", []), keywords)
                if keywords and score == 0:
                    continue
                matches.append((score, item))
            matches.sort(key=lambda t: t[0], reverse=True)
            return _ok(req, {"items": [m[1] for m in matches]})

        if api == "GetItem":
            item_id = data.get("item_id")
            item = state["items"].get(item_id)
            if not item:
                return _err(req, "NOT_FOUND", "item not found")
            return _ok(req, {"item": item})

        if api == "ProvideFeedback":
            item_id = data.get("item_id")
            vote = data.get("vote")  # "up" or "down"
            item = state["items"].get(item_id)
            if not item:
                return _err(req, "NOT_FOUND", "item not found")
            if vote not in ("up", "down"):
                return _err(req, "INVALID_ARGUMENT", "vote must be up or down")
            item["feedback"][vote] += 1
            persist()
            return _ok(req, {"item_id": item_id, "feedback": item["feedback"]})

        if api == "CheckAvailability":
            item_id = data.get("item_id")
            qty = int(data.get("quantity", 0))
            item = state["items"].get(item_id)
            if not item:
                return _err(req, "NOT_FOUND", "item not found")
            available = int(item.get("quantity", 0))
            return _ok(req, {"available": available, "ok": available >= qty})

        return _err(req, "UNIMPLEMENTED", f"unknown api {api}")

    return handle


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6002)
    parser.add_argument("--state", default="db_product/state.json")
    args = parser.parse_args()

    handler = handle_request_factory(args.state)
    run_server(args.host, args.port, handler)


if __name__ == "__main__":
    main()
