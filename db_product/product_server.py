import os
import json
import sqlite3
import sys
import threading
import time
from concurrent import futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import grpc
except ModuleNotFoundError as exc:
    raise RuntimeError("grpcio is required. Install dependencies from requirements.txt.") from exc

try:
    from pysyncobj import SyncObj, SyncObjConf, replicated
except ModuleNotFoundError as exc:
    raise RuntimeError("pysyncobj is required. Install it before running the replicated product database.") from exc

try:
    from common.grpc_gen import marketplace_pb2 as pb
    from common.grpc_gen import marketplace_pb2_grpc as pb_grpc
except ModuleNotFoundError as exc:
    raise RuntimeError(
        "Generated protobuf modules are missing. Run scripts/gen_protos.sh after installing grpcio-tools."
    ) from exc

MAX_KEYWORDS = 5
MAX_KEYWORD_LEN = 8
RAFT_SYNC_TIMEOUT_SEC = 10.0


def _ok(req: Dict[str, Any], data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "type": "Response",
        "request_id": req.get("request_id"),
        "ok": True,
        "error": None,
        "data": data,
    }


def _err(req: Dict[str, Any], code: str, message: str) -> Dict[str, Any]:
    return {
        "type": "Response",
        "request_id": req.get("request_id"),
        "ok": False,
        "error": {"code": code, "message": message},
        "data": None,
    }


def _status_from_resp(resp: Dict[str, Any]) -> pb.Status:
    if resp.get("ok"):
        return pb.Status(ok=True)
    err = resp.get("error") or {}
    return pb.Status(
        ok=False,
        error=pb.Error(
            code=str(err.get("code", "INTERNAL")),
            message=str(err.get("message", "request failed")),
        ),
    )


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


def _assign_item_id(conn: sqlite3.Connection, category: int) -> Tuple[str, int]:
    cur = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM items WHERE category = ?", (category,))
    next_seq = int(cur.fetchone()[0]) + 1
    return f"{category}:{next_seq}", next_seq


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


def _keyword_score(item_keywords: List[str], query_keywords: List[str]) -> int:
    if not query_keywords:
        return 0
    item_set = {k.lower() for k in item_keywords}
    return sum(1 for k in query_keywords if k.lower() in item_set)


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


def _get_item_row(conn: sqlite3.Connection, item_id: str, req: Dict[str, Any]):
    cur = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,))
    row = cur.fetchone()
    if not row:
        return None, _err(req, "NOT_FOUND", "item not found")
    return row, None


def _parse_members(members_arg: str) -> List[str]:
    members = [part.strip() for part in members_arg.split(",") if part.strip()]
    if len(members) != 5:
        raise ValueError("--members must contain exactly 5 entries")
    return members


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


class ReplicatedProductDB(SyncObj):
    def __init__(self, self_addr: str, partner_addrs: List[str], state_path: str):
        os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)

        self._self_addr = self_addr
        self._state_path = state_path
        self._db_lock = threading.Lock()
        self._conn = sqlite3.connect(state_path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        _init_db(self._conn)

        conf = SyncObjConf(
            dynamicMembershipChange=False,
            fullDumpFile=f"{state_path}.raft",
        )
        super().__init__(self_addr, partner_addrs, conf=conf)

    def close(self) -> None:
        with self._db_lock:
            self._conn.close()

    def ping(self) -> Dict[str, Any]:
        return _ok({"request_id": None}, {"now": time.time()})

    def get_item(self, item_id: str) -> Dict[str, Any]:
        req = {"request_id": None}
        with self._db_lock:
            row, err = _get_item_row(self._conn, item_id, req)
            if err:
                return err
            return _ok(req, {"item": _row_to_item(row, _item_keywords(self._conn, item_id))})

    def display_items_for_sale(self, seller_id: int) -> Dict[str, Any]:
        req = {"request_id": None}
        with self._db_lock:
            cur = self._conn.execute("SELECT * FROM items WHERE seller_id = ?", (int(seller_id),))
            items = [_row_to_item(row, _item_keywords(self._conn, row[0])) for row in cur.fetchall()]
            return _ok(req, {"items": items})

    def search_items(self, category: Optional[int], keywords: List[str]) -> Dict[str, Any]:
        req = {"request_id": None}
        if not keywords:
            return _err(req, "INVALID_ARGUMENT", "keywords required")
        kw_err = _validate_keywords(keywords)
        if kw_err:
            return _err(req, "INVALID_ARGUMENT", kw_err)

        with self._db_lock:
            if category is None:
                cur = self._conn.execute("SELECT * FROM items WHERE quantity > 0")
            else:
                cur = self._conn.execute(
                    "SELECT * FROM items WHERE quantity > 0 AND category = ?",
                    (int(category),),
                )
            matches = []
            for row in cur.fetchall():
                kws = _item_keywords(self._conn, row[0])
                score = _keyword_score(kws, keywords)
                if score == 0:
                    continue
                matches.append((score, _row_to_item(row, kws)))
            matches.sort(key=lambda item: item[0], reverse=True)
            return _ok(req, {"items": [item for _, item in matches]})

    def check_availability(self, item_id: str, quantity: int) -> Dict[str, Any]:
        req = {"request_id": None}
        with self._db_lock:
            row, err = _get_item_row(self._conn, item_id, req)
            if err:
                return err
            available = int(row[6])
            return _ok(req, {"available": available, "ok": available >= int(quantity)})

    @replicated
    def register_item(self, name: str, category: int, keywords: List[str], condition: str, price: float, quantity: int, seller_id: int) -> Dict[str, Any]:
        req = {"request_id": None}
        kw_err = _validate_keywords(keywords)
        if kw_err:
            return _err(req, "INVALID_ARGUMENT", kw_err)

        with self._db_lock:
            item_id, seq = _assign_item_id(self._conn, int(category))
            self._conn.execute(
                """
                INSERT INTO items(item_id, name, category, seq, condition, price, quantity, seller_id, feedback_up, feedback_down)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
                """,
                (item_id, name, int(category), seq, condition, float(price), int(quantity), int(seller_id)),
            )
            for keyword in keywords:
                self._conn.execute(
                    "INSERT OR IGNORE INTO item_keywords(item_id, keyword) VALUES (?, ?)",
                    (item_id, keyword),
                )
            self._conn.commit()
            return _ok(req, {"item_id": item_id})

    @replicated
    def change_item_price(self, item_id: str, price: float) -> Dict[str, Any]:
        req = {"request_id": None}
        with self._db_lock:
            row, err = _get_item_row(self._conn, item_id, req)
            if err:
                return err
            self._conn.execute("UPDATE items SET price = ? WHERE item_id = ?", (float(price), item_id))
            self._conn.commit()
            return _ok(req, {"item_id": item_id, "price": float(price)})

    @replicated
    def update_units_for_sale(self, item_id: str, quantity_delta: int) -> Dict[str, Any]:
        req = {"request_id": None}
        with self._db_lock:
            row, err = _get_item_row(self._conn, item_id, req)
            if err:
                return err
            new_qty = int(row[6]) + int(quantity_delta)
            if new_qty < 0:
                return _err(req, "INVALID_ARGUMENT", "quantity cannot be negative")
            self._conn.execute("UPDATE items SET quantity = ? WHERE item_id = ?", (new_qty, item_id))
            self._conn.commit()
            return _ok(req, {"item_id": item_id, "quantity": new_qty})

    @replicated
    def provide_feedback(self, item_id: str, vote: str) -> Dict[str, Any]:
        req = {"request_id": None}
        if vote not in ("up", "down"):
            return _err(req, "INVALID_ARGUMENT", "vote must be up or down")

        with self._db_lock:
            row, err = _get_item_row(self._conn, item_id, req)
            if err:
                return err
            if vote == "up":
                self._conn.execute("UPDATE items SET feedback_up = feedback_up + 1 WHERE item_id = ?", (item_id,))
                feedback = {"up": int(row[8]) + 1, "down": int(row[9])}
            else:
                self._conn.execute("UPDATE items SET feedback_down = feedback_down + 1 WHERE item_id = ?", (item_id,))
                feedback = {"up": int(row[8]), "down": int(row[9]) + 1}
            self._conn.commit()
            return _ok(req, {"item_id": item_id, "feedback": feedback})


class ProductDBServicer(pb_grpc.ProductDBServiceServicer):
    def __init__(self, replicated_db: ReplicatedProductDB):
        self._db = replicated_db

    def _leader_error(self, req: Dict[str, Any]) -> Dict[str, Any]:
        if not self._db.isReady():
            return _err(req, "RAFT_NOT_READY", "raft node is not ready")
        if self._db._isLeader():
            return _ok(req)

        leader = self._db._getLeader()
        leader_text = str(leader) if leader is not None else ""
        if leader_text:
            return _err(req, "NOT_LEADER", f"current leader is {leader_text}")
        return _err(req, "NOT_LEADER", "no raft leader is currently known")

    def _require_leader_for_write(self, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        status = self._leader_error(req)
        if status.get("ok"):
            return None
        return status

    def Ping(self, _request, _context):
        out = self._db.ping()
        return pb.PingResponse(status=_status_from_resp(out), now=float((out.get("data") or {}).get("now", 0.0)))

    def RegisterItem(self, request, _context):
        req = {"request_id": None}
        if None in (request.name, request.category, request.condition, request.price, request.quantity, request.seller_id):
            out = _err(req, "INVALID_ARGUMENT", "missing required item fields")
        else:
            out = self._require_leader_for_write(req)
            if out is None:
                out = self._db.register_item(
                    request.name,
                    int(request.category),
                    list(request.keywords),
                    request.condition,
                    float(request.price),
                    int(request.quantity),
                    int(request.seller_id),
                    sync=True,
                    timeout=RAFT_SYNC_TIMEOUT_SEC,
                )
        return pb.RegisterItemResponse(status=_status_from_resp(out), item_id=str((out.get("data") or {}).get("item_id", "")))

    def ChangeItemPrice(self, request, _context):
        req = {"request_id": None}
        out = self._require_leader_for_write(req)
        if out is None:
            out = self._db.change_item_price(
                request.item_id,
                float(request.price),
                sync=True,
                timeout=RAFT_SYNC_TIMEOUT_SEC,
            )
        data = out.get("data") or {}
        return pb.ChangeItemPriceResponse(status=_status_from_resp(out), item_id=str(data.get("item_id", "")), price=float(data.get("price", 0.0)))

    def UpdateUnitsForSale(self, request, _context):
        req = {"request_id": None}
        out = self._require_leader_for_write(req)
        if out is None:
            out = self._db.update_units_for_sale(
                request.item_id,
                int(request.quantity_delta),
                sync=True,
                timeout=RAFT_SYNC_TIMEOUT_SEC,
            )
        data = out.get("data") or {}
        return pb.UpdateUnitsForSaleResponse(status=_status_from_resp(out), item_id=str(data.get("item_id", "")), quantity=int(data.get("quantity", 0)))

    def DisplayItemsForSale(self, request, _context):
        out = self._db.display_items_for_sale(int(request.seller_id))
        items = []
        if out.get("ok"):
            items = [_to_item_msg(item) for item in ((out.get("data") or {}).get("items", []) or [])]
        return pb.DisplayItemsForSaleResponse(status=_status_from_resp(out), items=items)

    def SearchItems(self, request, _context):
        category = int(request.category) if request.include_category else None
        out = self._db.search_items(category, list(request.keywords))
        items = []
        if out.get("ok"):
            items = [_to_item_msg(item) for item in ((out.get("data") or {}).get("items", []) or [])]
        return pb.SearchItemsResponse(status=_status_from_resp(out), items=items)

    def GetItem(self, request, _context):
        out = self._db.get_item(request.item_id)
        item = pb.Item()
        if out.get("ok"):
            item = _to_item_msg((out.get("data") or {}).get("item", {}))
        return pb.GetItemResponse(status=_status_from_resp(out), item=item)

    def ProvideFeedback(self, request, _context):
        req = {"request_id": None}
        out = self._require_leader_for_write(req)
        if out is None:
            out = self._db.provide_feedback(
                request.item_id,
                request.vote,
                sync=True,
                timeout=RAFT_SYNC_TIMEOUT_SEC,
            )
        data = out.get("data") or {}
        feedback = data.get("feedback") or {}
        return pb.ProvideFeedbackResponse(
            status=_status_from_resp(out),
            item_id=str(data.get("item_id", "")),
            feedback=pb.Feedback(up=int(feedback.get("up", 0)), down=int(feedback.get("down", 0))),
        )

    def CheckAvailability(self, request, _context):
        out = self._db.check_availability(request.item_id, int(request.quantity))
        data = out.get("data") or {}
        return pb.CheckAvailabilityResponse(
            status=_status_from_resp(out),
            available=int(data.get("available", 0)),
            ok=bool(data.get("ok", False)),
        )


class _StatusHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, replicated_db: ReplicatedProductDB, node_id: int, grpc_addr: str, **kwargs):
        self._replicated_db = replicated_db
        self._node_id = int(node_id)
        self._grpc_addr = grpc_addr
        super().__init__(*args, **kwargs)

    def log_message(self, _format: str, *_args) -> None:
        return

    def do_GET(self) -> None:
        if self.path not in ("/health", "/raft-status"):
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": false, "error": "not found"}')
            return

        leader = self._replicated_db._getLeader()
        payload = {
            "ok": True,
            "node_id": self._node_id,
            "grpc_addr": self._grpc_addr,
            "raft_addr": self._replicated_db._self_addr,
            "is_ready": bool(self._replicated_db.isReady()),
            "is_leader": bool(self._replicated_db._isLeader()),
            "leader_addr": str(leader) if leader is not None else "",
        }
        body = (json.dumps(payload) + "\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_status_server(replicated_db: ReplicatedProductDB, node_id: int, host: str, grpc_port: int, status_port: int):
    bind_host = host if host != "0.0.0.0" else "0.0.0.0"
    grpc_addr = f"{host}:{grpc_port}"

    def _handler(*args, **kwargs):
        return _StatusHandler(
            *args,
            replicated_db=replicated_db,
            node_id=node_id,
            grpc_addr=grpc_addr,
            **kwargs,
        )

    status_server = ThreadingHTTPServer((bind_host, status_port), _handler)
    thread = threading.Thread(target=status_server.serve_forever, daemon=True)
    thread.start()
    return status_server


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6002)
    parser.add_argument("--status-port", type=int, default=0)
    parser.add_argument("--state", default="/app/data/product.db")
    parser.add_argument("--node-id", type=int, required=True)
    parser.add_argument(
        "--members",
        required=True,
        help="Comma-separated list of 5 Raft member addresses as HOST:PORT ordered by node id",
    )
    args = parser.parse_args()

    members = _parse_members(args.members)
    if args.node_id < 0 or args.node_id >= len(members):
        raise ValueError("--node-id is out of range for the provided --members list")

    self_addr = members[args.node_id]
    partner_addrs = [addr for idx, addr in enumerate(members) if idx != args.node_id]
    replicated_db = ReplicatedProductDB(self_addr, partner_addrs, args.state)
    status_port = int(args.status_port) if int(args.status_port) > 0 else int(args.port) + 3000

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=32))
    pb_grpc.add_ProductDBServiceServicer_to_server(ProductDBServicer(replicated_db), server)
    server.add_insecure_port(f"{args.host}:{args.port}")
    status_server = _start_status_server(replicated_db, args.node_id, args.host, int(args.port), status_port)
    server.start()

    try:
        server.wait_for_termination()
    finally:
        status_server.shutdown()
        replicated_db.close()


if __name__ == "__main__":
    main()
