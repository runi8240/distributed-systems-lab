import os
import sqlite3
import sys
import threading
import time
from concurrent import futures
from typing import Any, Dict, List

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import grpc
except ModuleNotFoundError as exc:
    raise RuntimeError("grpcio is required. Install dependencies from requirements.txt.") from exc

try:
    from common.grpc_gen import marketplace_pb2 as pb
    from common.grpc_gen import marketplace_pb2_grpc as pb_grpc
except ModuleNotFoundError as exc:
    raise RuntimeError(
        "Generated protobuf modules are missing. Run scripts/gen_protos.sh after installing grpcio-tools."
    ) from exc

MAX_KEYWORDS = 5
MAX_KEYWORD_LEN = 8


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


def _assign_item_id(conn: sqlite3.Connection, category: int) -> str:
    cur = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM items WHERE category = ?", (category,))
    next_seq = int(cur.fetchone()[0]) + 1
    return f"{category}:{next_seq}", next_seq


def _keyword_score(item_keywords: List[str], query_keywords: List[str]) -> int:
    if not query_keywords:
        return 0
    item_set = {k.lower() for k in item_keywords}
    return sum(1 for k in query_keywords if k.lower() in item_set)


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            item_id TEXT PRIMARY KEY,
            name TEXT,
            category INTEGER,
            seq INTEGER,
            condition TEXT,
            price REAL,
            quantity INTEGER,
            seller_id INTEGER,
            feedback_up INTEGER DEFAULT 0,
            feedback_down INTEGER DEFAULT 0
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_category ON items(category)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS item_keywords (
            item_id TEXT,
            keyword TEXT,
            PRIMARY KEY (item_id, keyword),
            FOREIGN KEY(item_id) REFERENCES items(item_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_keywords_keyword ON item_keywords(keyword)")
    conn.commit()


def _get_item_row(conn: sqlite3.Connection, item_id, req):
    cur = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,))
    row = cur.fetchone()
    if not row:
        return None, _err(req, "NOT_FOUND", "item not found")
    return row, None


def _item_keywords(conn: sqlite3.Connection, item_id: str) -> List[str]:
    cur = conn.execute("SELECT keyword FROM item_keywords WHERE item_id = ?", (item_id,))
    return [row[0] for row in cur.fetchall()]


def _row_to_item(row, keywords: List[str]) -> Dict[str, Any]:
    return {
        "item_id": row[0],
        "name": row[1],
        "category": int(row[2]),
        "keywords": keywords,
        "condition": row[4],
        "price": float(row[5]),
        "quantity": int(row[6]),
        "seller_id": int(row[7]),
        "feedback": {"up": int(row[8]), "down": int(row[9])},
    }


def _validate_keywords(keywords) -> str | None:
    if not isinstance(keywords, list):
        return "keywords must be a list"
    if len(keywords) > MAX_KEYWORDS:
        return f"keywords must have at most {MAX_KEYWORDS} entries"
    for k in keywords:
        if not isinstance(k, str):
            return "each keyword must be a string"
        if len(k) > MAX_KEYWORD_LEN:
            return f"keyword '{k}' exceeds {MAX_KEYWORD_LEN} characters"
    return None


def handle_request_factory(state_path: str):
    conn = sqlite3.connect(state_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _init_db(conn)
    lock = threading.Lock()

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
            kw_err = _validate_keywords(keywords)
            if kw_err:
                return _err(req, "INVALID_ARGUMENT", kw_err)
            with lock:
                item_id, seq = _assign_item_id(conn, int(category))
                conn.execute(
                    """
                    INSERT INTO items(item_id, name, category, seq, condition, price, quantity, seller_id, feedback_up, feedback_down)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
                    """,
                    (item_id, name, int(category), seq, condition, float(price), int(quantity), int(seller_id)),
                )
                for kw in keywords:
                    conn.execute(
                        "INSERT OR IGNORE INTO item_keywords(item_id, keyword) VALUES (?, ?)",
                        (item_id, kw),
                    )
                conn.commit()
                return _ok(req, {"item_id": item_id})

        if api == "ChangeItemPrice":
            item_id = data.get("item_id")
            price = data.get("price")
            with lock:
                row, err = _get_item_row(conn, item_id, req)
                if err:
                    return err
                conn.execute("UPDATE items SET price = ? WHERE item_id = ?", (float(price), item_id))
                conn.commit()
                return _ok(req, {"item_id": item_id, "price": float(price)})

        if api == "UpdateUnitsForSale":
            item_id = data.get("item_id")
            quantity_delta = data.get("quantity_delta")
            if item_id is None or quantity_delta is None:
                return _err(req, "INVALID_ARGUMENT", "item_id and quantity_delta required")
            with lock:
                row, err = _get_item_row(conn, item_id, req)
                if err:
                    return err
                new_qty = int(row[6]) + int(quantity_delta)
                if new_qty < 0:
                    return _err(req, "INVALID_ARGUMENT", "quantity cannot be negative")
                conn.execute("UPDATE items SET quantity = ? WHERE item_id = ?", (new_qty, item_id))
                conn.commit()
                return _ok(req, {"item_id": item_id, "quantity": new_qty})

        if api == "DisplayItemsForSale":
            seller_id = int(data.get("seller_id"))
            with lock:
                cur = conn.execute("SELECT * FROM items WHERE seller_id = ?", (seller_id,))
                items = []
                for row in cur.fetchall():
                    items.append(_row_to_item(row, _item_keywords(conn, row[0])))
                return _ok(req, {"items": items})

        if api == "SearchItems":
            category = data.get("category")
            keywords = data.get("keywords", [])
            if not keywords:
                return _err(req, "INVALID_ARGUMENT", "keywords required")
            kw_err = _validate_keywords(keywords)
            if kw_err:
                return _err(req, "INVALID_ARGUMENT", kw_err)
            with lock:
                if category is None:
                    cur = conn.execute("SELECT * FROM items WHERE quantity > 0")
                else:
                    cur = conn.execute(
                        "SELECT * FROM items WHERE quantity > 0 AND category = ?",
                        (int(category),),
                    )
                matches = []
                for row in cur.fetchall():
                    kws = _item_keywords(conn, row[0])
                    score = _keyword_score(kws, keywords)
                    if score == 0:
                        continue
                    matches.append((score, _row_to_item(row, kws)))
                matches.sort(key=lambda t: t[0], reverse=True)
                return _ok(req, {"items": [m[1] for m in matches]})

        if api == "GetItem":
            item_id = data.get("item_id")
            with lock:
                row, err = _get_item_row(conn, item_id, req)
                if err:
                    return err
                return _ok(req, {"item": _row_to_item(row, _item_keywords(conn, item_id))})

        if api == "ProvideFeedback":
            item_id = data.get("item_id")
            vote = data.get("vote")  # "up" or "down"
            if vote not in ("up", "down"):
                return _err(req, "INVALID_ARGUMENT", "vote must be up or down")
            with lock:
                row, err = _get_item_row(conn, item_id, req)
                if err:
                    return err
                if vote == "up":
                    conn.execute("UPDATE items SET feedback_up = feedback_up + 1 WHERE item_id = ?", (item_id,))
                    feedback = {"up": int(row[8]) + 1, "down": int(row[9])}
                else:
                    conn.execute("UPDATE items SET feedback_down = feedback_down + 1 WHERE item_id = ?", (item_id,))
                    feedback = {"up": int(row[8]), "down": int(row[9]) + 1}
                conn.commit()
                return _ok(req, {"item_id": item_id, "feedback": feedback})

        if api == "CheckAvailability":
            item_id = data.get("item_id")
            qty = int(data.get("quantity", 0))
            with lock:
                row, err = _get_item_row(conn, item_id, req)
                if err:
                    return err
                available = int(row[6])
                return _ok(req, {"available": available, "ok": available >= qty})

        return _err(req, "UNIMPLEMENTED", f"unknown api {api}")

    return handle


def _status_from_resp(resp: Dict[str, Any]) -> pb.Status:
    if resp.get("ok"):
        return pb.Status(ok=True)
    err = resp.get("error") or {}
    return pb.Status(ok=False, error=pb.Error(code=str(err.get("code", "INTERNAL")), message=str(err.get("message", "request failed"))))


def _to_item_msg(item: Dict[str, Any]) -> pb.Item:
    feedback = item.get("feedback") or {}
    return pb.Item(
        item_id=str(item.get("item_id", "")),
        name=str(item.get("name", "")),
        category=int(item.get("category", 0)),
        keywords=list(item.get("keywords", []) or []),
        condition=str(item.get("condition", "")),
        price=float(item.get("price", 0.0)),
        quantity=int(item.get("quantity", 0)),
        seller_id=int(item.get("seller_id", 0)),
        feedback=pb.Feedback(up=int(feedback.get("up", 0)), down=int(feedback.get("down", 0))),
    )


class ProductDBServicer(pb_grpc.ProductDBServiceServicer):
    def __init__(self, state_path: str):
        self._handle = handle_request_factory(state_path)

    def _call(self, api: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._handle({"type": "Request", "request_id": None, "api": api, "data": data})

    def Ping(self, _request, _context):
        out = self._call("Ping", {})
        return pb.PingResponse(status=_status_from_resp(out), now=float((out.get("data") or {}).get("now", 0.0)))

    def RegisterItem(self, request, _context):
        out = self._call(
            "RegisterItem",
            {
                "name": request.name,
                "category": int(request.category),
                "keywords": list(request.keywords),
                "condition": request.condition,
                "price": float(request.price),
                "quantity": int(request.quantity),
                "seller_id": int(request.seller_id),
            },
        )
        return pb.RegisterItemResponse(status=_status_from_resp(out), item_id=str((out.get("data") or {}).get("item_id", "")))

    def ChangeItemPrice(self, request, _context):
        out = self._call("ChangeItemPrice", {"item_id": request.item_id, "price": float(request.price)})
        data = out.get("data") or {}
        return pb.ChangeItemPriceResponse(status=_status_from_resp(out), item_id=str(data.get("item_id", "")), price=float(data.get("price", 0.0)))

    def UpdateUnitsForSale(self, request, _context):
        out = self._call(
            "UpdateUnitsForSale",
            {"item_id": request.item_id, "quantity_delta": int(request.quantity_delta)},
        )
        data = out.get("data") or {}
        return pb.UpdateUnitsForSaleResponse(status=_status_from_resp(out), item_id=str(data.get("item_id", "")), quantity=int(data.get("quantity", 0)))

    def DisplayItemsForSale(self, request, _context):
        out = self._call("DisplayItemsForSale", {"seller_id": int(request.seller_id)})
        items = []
        if out.get("ok"):
            items = [_to_item_msg(item) for item in ((out.get("data") or {}).get("items", []) or [])]
        return pb.DisplayItemsForSaleResponse(status=_status_from_resp(out), items=items)

    def SearchItems(self, request, _context):
        category = int(request.category) if request.include_category else None
        out = self._call(
            "SearchItems",
            {"category": category, "keywords": list(request.keywords)},
        )
        items = []
        if out.get("ok"):
            items = [_to_item_msg(item) for item in ((out.get("data") or {}).get("items", []) or [])]
        return pb.SearchItemsResponse(status=_status_from_resp(out), items=items)

    def GetItem(self, request, _context):
        out = self._call("GetItem", {"item_id": request.item_id})
        item = pb.Item()
        if out.get("ok"):
            item = _to_item_msg((out.get("data") or {}).get("item", {}))
        return pb.GetItemResponse(status=_status_from_resp(out), item=item)

    def ProvideFeedback(self, request, _context):
        out = self._call("ProvideFeedback", {"item_id": request.item_id, "vote": request.vote})
        data = out.get("data") or {}
        feedback = data.get("feedback") or {}
        return pb.ProvideFeedbackResponse(
            status=_status_from_resp(out),
            item_id=str(data.get("item_id", "")),
            feedback=pb.Feedback(up=int(feedback.get("up", 0)), down=int(feedback.get("down", 0))),
        )

    def CheckAvailability(self, request, _context):
        out = self._call("CheckAvailability", {"item_id": request.item_id, "quantity": int(request.quantity)})
        data = out.get("data") or {}
        return pb.CheckAvailabilityResponse(
            status=_status_from_resp(out),
            available=int(data.get("available", 0)),
            ok=bool(data.get("ok", False)),
        )


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6002)
    parser.add_argument("--state", default="db_product/state.db")
    args = parser.parse_args()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=32))
    pb_grpc.add_ProductDBServiceServicer_to_server(ProductDBServicer(args.state), server)
    server.add_insecure_port(f"{args.host}:{args.port}")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    main()
