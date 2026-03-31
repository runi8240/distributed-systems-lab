import os
import sqlite3
import sys
import threading
import time
import uuid
from concurrent import futures
from typing import Any, Dict, Optional, Tuple

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
    from common.rotating_sequencer import RotatingSequencerAtomicBroadcast
except ModuleNotFoundError as exc:
    raise RuntimeError(
        "Required modules are missing. Ensure protobufs are generated and the rotating sequencer exists."
    ) from exc

MAX_NAME_LEN = 32
SESSION_TIMEOUT_SEC = 5 * 60

RequestId = Tuple[int, int]

WRITE_APIS = {
    "CreateBuyer",
    "CreateSeller",
    "Login",
    "Logout",
    "UpdateCart",
    "ClearCart",
    "SaveCart",
    "ClearAndSaveCart",
}


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


def _new_session(conn: sqlite3.Connection, role: str, user_id: int, session_id: str, timestamp: float) -> str:
    conn.execute(
        "INSERT INTO sessions(session_id, role, user_id, last_active, cart_initialized) VALUES (?, ?, ?, ?, 0)",
        (session_id, role, user_id, float(timestamp)),
    )
    return session_id


def _get_user_row(conn: sqlite3.Connection, table: str, user_id: Any, req: Dict[str, Any], not_found_message: str):
    cur = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (int(user_id),))
    row = cur.fetchone()
    if not row:
        return None, _err(req, "NOT_FOUND", not_found_message)
    return row, None


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS buyers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            password TEXT,
            purchases_count INTEGER DEFAULT 0,
            cart_saved INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sellers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            password TEXT,
            feedback_up INTEGER DEFAULT 0,
            feedback_down INTEGER DEFAULT 0,
            items_sold INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            role TEXT,
            user_id INTEGER,
            last_active REAL,
            cart_initialized INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cart_items (
            buyer_id INTEGER,
            item_id TEXT,
            quantity INTEGER,
            PRIMARY KEY (buyer_id, item_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_cart_items (
            session_id TEXT,
            item_id TEXT,
            quantity INTEGER,
            PRIMARY KEY (session_id, item_id)
        )
        """
    )
    buyer_cols = [r[1] for r in conn.execute("PRAGMA table_info(buyers)").fetchall()]
    if "cart_saved" not in buyer_cols:
        conn.execute("ALTER TABLE buyers ADD COLUMN cart_saved INTEGER DEFAULT 0")
    session_cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
    if "cart_initialized" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN cart_initialized INTEGER DEFAULT 0")
    conn.commit()


def _parse_members(members_arg: str) -> Dict[int, Tuple[str, int]]:
    raw_members = [part.strip() for part in members_arg.split(",") if part.strip()]
    if len(raw_members) != 5:
        raise ValueError("--members must contain exactly 5 entries")

    members: Dict[int, Tuple[str, int]] = {}
    for idx, raw in enumerate(raw_members):
        if ":" not in raw:
            raise ValueError(f"invalid member entry: {raw}")
        host, port_str = raw.rsplit(":", 1)
        members[idx] = (host.strip(), int(port_str))
    return members


class ReplicatedCustomerDB:
    def __init__(self, state_path: str, node_id: int, members: Dict[int, Tuple[str, int]]):
        os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(state_path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        _init_db(self._conn)

        self._db_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending_requests: Dict[RequestId, Dict[str, Any]] = {}
        self._completed_results: Dict[RequestId, Dict[str, Any]] = {}

        self._sequencer = RotatingSequencerAtomicBroadcast(
            node_id=node_id,
            members=members,
            on_deliver=self._on_deliver,
            bind_address=("0.0.0.0", members[node_id][1]),
        )

    def close(self) -> None:
        self._sequencer.close()
        self._conn.close()

    def handle(self, req: Dict[str, Any]) -> Dict[str, Any]:
        api = req.get("api")
        data = req.get("data") or {}

        if api == "Ping":
            return _ok(req, {"now": time.time()})

        if api in WRITE_APIS:
            return self._handle_write(req, api, data)

        return self._handle_read(req, api, data)

    def _handle_write(self, req: Dict[str, Any], api: str, data: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"op": api, "args": data}
        event = threading.Event()
        broadcast_request_id = self._sequencer.broadcast(payload)

        with self._pending_lock:
            completed = self._completed_results.pop(broadcast_request_id, None)
            self._pending_requests[broadcast_request_id] = {
                "event": event,
                "result": completed,
            }
            if completed is not None:
                event.set()

        event.wait()

        with self._pending_lock:
            pending = self._pending_requests.pop(broadcast_request_id, None)

        if not pending or pending.get("result") is None:
            return _err(req, "INTERNAL", "replicated write completed without a stored result")

        result = dict(pending["result"])
        result["request_id"] = req.get("request_id")
        return result

    def _handle_read(self, req: Dict[str, Any], api: str, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._db_lock:
            return self._execute_local(api, data, req)

    def _on_deliver(self, payload: Any, metadata: Dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return

        op = str(payload.get("op", ""))
        args = payload.get("args") or {}
        request_id_raw = metadata.get("request_id")
        request_id = self._normalize_request_id(request_id_raw)
        if request_id is None:
            return

        with self._db_lock:
            req = {"type": "Request", "request_id": None, "api": op, "data": args}
            result = self._execute_local(op, args, req)

        with self._pending_lock:
            pending = self._pending_requests.get(request_id)
            if pending is not None:
                pending["result"] = result
                pending["event"].set()
            else:
                self._completed_results[request_id] = result

    def _normalize_request_id(self, raw: Any) -> Optional[RequestId]:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return None
        try:
            return int(raw[0]), int(raw[1])
        except (TypeError, ValueError):
            return None

    def _validate_buyer_session(self, session_id: str, buyer_id: int, req: Dict[str, Any]):
        if not session_id:
            return None, _err(req, "INVALID_ARGUMENT", "session_id required")
        cur = self._conn.execute(
            "SELECT role, user_id, cart_initialized FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cur.fetchone()
        if not row:
            return None, _err(req, "NOT_LOGGED_IN", "invalid session")
        role, user_id, cart_initialized = row
        if role != "buyer" or int(user_id) != int(buyer_id):
            return None, _err(req, "NOT_AUTHORIZED", "session does not match buyer")
        return int(cart_initialized or 0), None

    def _ensure_session_cart_initialized(self, session_id: str, buyer_id: int, req: Dict[str, Any]):
        cart_initialized, err = self._validate_buyer_session(session_id, buyer_id, req)
        if err:
            return err
        if cart_initialized == 1:
            return None
        self._conn.execute("DELETE FROM session_cart_items WHERE session_id = ?", (session_id,))
        self._conn.execute(
            """
            INSERT INTO session_cart_items(session_id, item_id, quantity)
            SELECT ?, item_id, quantity FROM cart_items WHERE buyer_id = ?
            """,
            (session_id, int(buyer_id)),
        )
        self._conn.execute("UPDATE sessions SET cart_initialized = 1 WHERE session_id = ?", (session_id,))
        return None

    def _execute_local(self, api: str, data: Dict[str, Any], req: Dict[str, Any]) -> Dict[str, Any]:
        if api == "CreateBuyer":
            name = data.get("name")
            password = data.get("password")
            if not name or not password:
                return _err(req, "INVALID_ARGUMENT", "name and password required")
            if len(name) > MAX_NAME_LEN:
                return _err(req, "INVALID_ARGUMENT", f"name must be at most {MAX_NAME_LEN} characters")
            cur = self._conn.execute(
                "INSERT INTO buyers(name, password, purchases_count) VALUES (?, ?, 0)",
                (name, password),
            )
            self._conn.commit()
            return _ok(req, {"buyer_id": int(cur.lastrowid)})

        if api == "CreateSeller":
            name = data.get("name")
            password = data.get("password")
            if not name or not password:
                return _err(req, "INVALID_ARGUMENT", "name and password required")
            if len(name) > MAX_NAME_LEN:
                return _err(req, "INVALID_ARGUMENT", f"name must be at most {MAX_NAME_LEN} characters")
            cur = self._conn.execute(
                "INSERT INTO sellers(name, password, feedback_up, feedback_down, items_sold) VALUES (?, ?, 0, 0, 0)",
                (name, password),
            )
            self._conn.commit()
            return _ok(req, {"seller_id": int(cur.lastrowid)})

        if api == "Login":
            role = data.get("role")
            name = data.get("name")
            password = data.get("password")
            session_id = str(data.get("session_id", ""))
            timestamp = data.get("timestamp")
            if role not in ("buyer", "seller"):
                return _err(req, "INVALID_ARGUMENT", "role must be buyer or seller")
            if not session_id:
                return _err(req, "INVALID_ARGUMENT", "session_id required")
            if timestamp is None:
                return _err(req, "INVALID_ARGUMENT", "timestamp required")
            try:
                timestamp_value = float(timestamp)
            except (TypeError, ValueError):
                return _err(req, "INVALID_ARGUMENT", "timestamp must be numeric")
            table = "buyers" if role == "buyer" else "sellers"
            cur = self._conn.execute(
                f"SELECT id, password FROM {table} WHERE name = ? ORDER BY id LIMIT 1",
                (name,),
            )
            row = cur.fetchone()
            if not row or row[1] != password:
                return _err(req, "AUTH_FAILED", "invalid credentials")
            user_id = int(row[0])
            session_id = _new_session(self._conn, role, user_id, session_id, timestamp_value)
            self._conn.commit()
            return _ok(req, {"session_id": session_id, "user_id": user_id, "role": role})

        if api == "Logout":
            session_id = data.get("session_id")
            if not session_id:
                return _err(req, "INVALID_ARGUMENT", "session_id required")
            self._conn.execute("DELETE FROM session_cart_items WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            self._conn.commit()
            return _ok(req, {"logged_out": True})

        if api == "ValidateSession":
            session_id = data.get("session_id")
            if not session_id:
                return _err(req, "INVALID_ARGUMENT", "session_id required")
            cur = self._conn.execute(
                "SELECT role, user_id, last_active FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cur.fetchone()
            if not row:
                return _err(req, "NOT_LOGGED_IN", "invalid session")
            role, user_id, last_active = row
            now = time.time()
            if now - float(last_active) > SESSION_TIMEOUT_SEC:
                self._conn.execute("DELETE FROM session_cart_items WHERE session_id = ?", (session_id,))
                self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
                self._conn.commit()
                return _err(req, "SESSION_TIMEOUT", "session expired")
            return _ok(req, {"role": role, "user_id": int(user_id)})

        if api == "GetSellerRating":
            row, err = _get_user_row(self._conn, "sellers", data.get("seller_id"), req, "seller not found")
            if err:
                return err
            return _ok(
                req,
                {
                    "seller_id": int(row[0]),
                    "feedback": {"up": int(row[3]), "down": int(row[4])},
                },
            )

        if api == "GetBuyerPurchases":
            row, err = _get_user_row(self._conn, "buyers", data.get("buyer_id"), req, "buyer not found")
            if err:
                return err
            return _ok(req, {"buyer_id": int(row[0]), "purchases_count": int(row[3])})

        if api == "GetCart":
            session_id = data.get("session_id")
            row, err = _get_user_row(self._conn, "buyers", data.get("buyer_id"), req, "buyer not found")
            if err:
                return err
            buyer_id = int(row[0])
            if session_id:
                cart_initialized, err = self._validate_buyer_session(session_id, buyer_id, req)
                if err:
                    return err
                if cart_initialized == 1:
                    cur = self._conn.execute(
                        "SELECT item_id, quantity FROM session_cart_items WHERE session_id = ?",
                        (session_id,),
                    )
                else:
                    cur = self._conn.execute(
                        "SELECT item_id, quantity FROM cart_items WHERE buyer_id = ?",
                        (buyer_id,),
                    )
            else:
                cur = self._conn.execute(
                    "SELECT item_id, quantity FROM cart_items WHERE buyer_id = ?",
                    (buyer_id,),
                )
            cart = {item_id: int(qty) for item_id, qty in cur.fetchall()}
            return _ok(req, {"cart": cart})

        if api == "UpdateCart":
            item_id = data.get("item_id")
            delta = int(data.get("quantity_delta", 0))
            session_id = data.get("session_id")
            if not item_id or delta == 0:
                return _err(req, "INVALID_ARGUMENT", "item_id and quantity_delta required")
            row, err = _get_user_row(self._conn, "buyers", data.get("buyer_id"), req, "buyer not found")
            if err:
                return err
            buyer_id = int(row[0])
            if session_id:
                err = self._ensure_session_cart_initialized(session_id, buyer_id, req)
                if err:
                    return err
                cur = self._conn.execute(
                    "SELECT quantity FROM session_cart_items WHERE session_id = ? AND item_id = ?",
                    (session_id, item_id),
                )
            else:
                cur = self._conn.execute(
                    "SELECT quantity FROM cart_items WHERE buyer_id = ? AND item_id = ?",
                    (buyer_id, item_id),
                )
            existing = cur.fetchone()
            cur_qty = int(existing[0]) if existing else 0
            new_qty = cur_qty + delta
            if new_qty < 0:
                return _err(req, "INVALID_ARGUMENT", "cart quantity cannot be negative")
            if new_qty == 0:
                if session_id:
                    self._conn.execute(
                        "DELETE FROM session_cart_items WHERE session_id = ? AND item_id = ?",
                        (session_id, item_id),
                    )
                else:
                    self._conn.execute(
                        "DELETE FROM cart_items WHERE buyer_id = ? AND item_id = ?",
                        (buyer_id, item_id),
                    )
            else:
                if session_id:
                    self._conn.execute(
                        """
                        INSERT INTO session_cart_items(session_id, item_id, quantity)
                        VALUES (?, ?, ?)
                        ON CONFLICT(session_id, item_id) DO UPDATE SET quantity = excluded.quantity
                        """,
                        (session_id, item_id, new_qty),
                    )
                else:
                    self._conn.execute(
                        """
                        INSERT INTO cart_items(buyer_id, item_id, quantity)
                        VALUES (?, ?, ?)
                        ON CONFLICT(buyer_id, item_id) DO UPDATE SET quantity = excluded.quantity
                        """,
                        (buyer_id, item_id, new_qty),
                    )
            self._conn.commit()
            return _ok(req, {"item_id": item_id, "quantity": max(new_qty, 0)})

        if api == "ClearCart":
            session_id = data.get("session_id")
            row, err = _get_user_row(self._conn, "buyers", data.get("buyer_id"), req, "buyer not found")
            if err:
                return err
            buyer_id = int(row[0])
            if session_id:
                err = self._ensure_session_cart_initialized(session_id, buyer_id, req)
                if err:
                    return err
                self._conn.execute("DELETE FROM session_cart_items WHERE session_id = ?", (session_id,))
            else:
                self._conn.execute("DELETE FROM cart_items WHERE buyer_id = ?", (buyer_id,))
            self._conn.commit()
            return _ok(req, {"cleared": True})

        if api == "SaveCart":
            buyer_id = data.get("buyer_id")
            session_id = data.get("session_id")
            row, err = _get_user_row(self._conn, "buyers", buyer_id, req, "buyer not found")
            if err:
                return err
            buyer_id = int(row[0])
            if session_id:
                cart_initialized, err = self._validate_buyer_session(session_id, buyer_id, req)
                if err:
                    return err
                if cart_initialized == 1:
                    self._conn.execute("DELETE FROM cart_items WHERE buyer_id = ?", (buyer_id,))
                    self._conn.execute(
                        """
                        INSERT INTO cart_items(buyer_id, item_id, quantity)
                        SELECT ?, item_id, quantity FROM session_cart_items WHERE session_id = ?
                        """,
                        (buyer_id, session_id),
                    )
            self._conn.commit()
            return _ok(req, {"saved": True})

        if api == "ClearAndSaveCart":
            buyer_id = data.get("buyer_id")
            session_id = data.get("session_id")
            row, err = _get_user_row(self._conn, "buyers", buyer_id, req, "buyer not found")
            if err:
                return err
            buyer_id = int(row[0])
            self._conn.execute("DELETE FROM cart_items WHERE buyer_id = ?", (buyer_id,))
            if session_id:
                cart_initialized, err = self._validate_buyer_session(session_id, buyer_id, req)
                if err:
                    return err
                if cart_initialized == 1:
                    self._conn.execute("DELETE FROM session_cart_items WHERE session_id = ?", (session_id,))
                else:
                    self._conn.execute("DELETE FROM session_cart_items WHERE session_id = ?", (session_id,))
                    self._conn.execute("UPDATE sessions SET cart_initialized = 1 WHERE session_id = ?", (session_id,))
            self._conn.commit()
            return _ok(req, {"cleared": True})

        return _err(req, "UNIMPLEMENTED", f"unknown api {api}")


class CustomerDBServicer(pb_grpc.CustomerDBServiceServicer):
    def __init__(self, replicated_db: ReplicatedCustomerDB):
        self._db = replicated_db

    def _call(self, api: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._db.handle({"type": "Request", "request_id": None, "api": api, "data": data})

    @staticmethod
    def _metadata_value(context, key: str) -> str:
        for k, v in context.invocation_metadata():
            if k == key:
                return str(v)
        return ""

    def Ping(self, _request, _context):
        out = self._call("Ping", {})
        return pb.PingResponse(status=_status_from_resp(out), now=float((out.get("data") or {}).get("now", 0.0)))

    def CreateBuyer(self, request, _context):
        out = self._call("CreateBuyer", {"name": request.name, "password": request.password})
        return pb.CreateBuyerResponse(status=_status_from_resp(out), buyer_id=int((out.get("data") or {}).get("buyer_id", 0)))

    def CreateSeller(self, request, _context):
        out = self._call("CreateSeller", {"name": request.name, "password": request.password})
        return pb.CreateSellerResponse(status=_status_from_resp(out), seller_id=int((out.get("data") or {}).get("seller_id", 0)))

    def Login(self, request, _context):
        session_id = str(uuid.uuid4())
        now = time.time()
        out = self._call(
            "Login",
            {
                "role": request.role,
                "name": request.name,
                "password": request.password,
                "session_id": session_id,
                "timestamp": now,
            },
        )
        data = out.get("data") or {}
        return pb.LoginResponse(
            status=_status_from_resp(out),
            session_id=str(data.get("session_id", "")),
            user_id=int(data.get("user_id", 0)),
            role=str(data.get("role", "")),
        )

    def Logout(self, request, _context):
        out = self._call("Logout", {"session_id": request.session_id})
        return pb.LogoutResponse(
            status=_status_from_resp(out),
            logged_out=bool((out.get("data") or {}).get("logged_out", False)),
        )

    def ValidateSession(self, request, _context):
        out = self._call("ValidateSession", {"session_id": request.session_id})
        data = out.get("data") or {}
        return pb.ValidateSessionResponse(
            status=_status_from_resp(out),
            role=str(data.get("role", "")),
            user_id=int(data.get("user_id", 0)),
        )

    def GetSellerRating(self, request, _context):
        out = self._call("GetSellerRating", {"seller_id": int(request.seller_id)})
        data = out.get("data") or {}
        feedback = data.get("feedback") or {}
        return pb.GetSellerRatingResponse(
            status=_status_from_resp(out),
            seller_id=int(data.get("seller_id", 0)),
            feedback=pb.Feedback(up=int(feedback.get("up", 0)), down=int(feedback.get("down", 0))),
        )

    def GetBuyerPurchases(self, request, _context):
        out = self._call("GetBuyerPurchases", {"buyer_id": int(request.buyer_id)})
        data = out.get("data") or {}
        return pb.GetBuyerPurchasesResponse(
            status=_status_from_resp(out),
            buyer_id=int(data.get("buyer_id", 0)),
            purchases_count=int(data.get("purchases_count", 0)),
        )

    def GetCart(self, request, _context):
        session_id = self._metadata_value(_context, "session-id")
        out = self._call("GetCart", {"buyer_id": int(request.buyer_id), "session_id": session_id})
        cart = []
        if out.get("ok"):
            for item_id, qty in ((out.get("data") or {}).get("cart", {})).items():
                cart.append(pb.CartEntry(item_id=str(item_id), quantity=int(qty)))
        return pb.GetCartResponse(status=_status_from_resp(out), cart=cart)

    def UpdateCart(self, request, _context):
        session_id = self._metadata_value(_context, "session-id")
        out = self._call(
            "UpdateCart",
            {
                "buyer_id": int(request.buyer_id),
                "item_id": request.item_id,
                "quantity_delta": int(request.quantity_delta),
                "session_id": session_id,
            },
        )
        data = out.get("data") or {}
        return pb.UpdateCartResponse(
            status=_status_from_resp(out),
            item_id=str(data.get("item_id", "")),
            quantity=int(data.get("quantity", 0)),
        )

    def ClearCart(self, request, _context):
        session_id = self._metadata_value(_context, "session-id")
        out = self._call("ClearCart", {"buyer_id": int(request.buyer_id), "session_id": session_id})
        return pb.ClearCartResponse(
            status=_status_from_resp(out),
            cleared=bool((out.get("data") or {}).get("cleared", False)),
        )

    def SaveCart(self, request, _context):
        session_id = self._metadata_value(_context, "session-id")
        out = self._call("SaveCart", {"buyer_id": int(request.buyer_id), "session_id": session_id})
        return pb.SaveCartResponse(
            status=_status_from_resp(out),
            saved=bool((out.get("data") or {}).get("saved", False)),
        )

    def ClearAndSaveCart(self, request, _context):
        session_id = self._metadata_value(_context, "session-id")
        out = self._call("ClearAndSaveCart", {"buyer_id": int(request.buyer_id), "session_id": session_id})
        return pb.ClearAndSaveCartResponse(
            status=_status_from_resp(out),
            cleared=bool((out.get("data") or {}).get("cleared", False)),
        )


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6001)
    parser.add_argument("--state", default="/app/data/customer.db")
    parser.add_argument("--node-id", type=int, required=True)
    parser.add_argument(
        "--members",
        required=True,
        help="Comma-separated list of 5 sequencer members as IP:UDP_PORT ordered by node id",
    )
    args = parser.parse_args()

    if args.node_id < 0 or args.node_id > 4:
        raise ValueError("--node-id must be between 0 and 4")

    members = _parse_members(args.members)
    replicated_db = ReplicatedCustomerDB(args.state, args.node_id, members)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=32))
    pb_grpc.add_CustomerDBServiceServicer_to_server(CustomerDBServicer(replicated_db), server)
    server.add_insecure_port(f"{args.host}:{args.port}")
    server.start()

    try:
        server.wait_for_termination()
    finally:
        replicated_db.close()


if __name__ == "__main__":
    main()
