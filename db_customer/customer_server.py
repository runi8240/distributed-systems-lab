import os
import sqlite3
import sys
import threading
import time
import uuid
from concurrent import futures
from typing import Any, Dict

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

MAX_NAME_LEN = 32

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


def _new_session(conn: sqlite3.Connection, role: str, user_id: int):
    session_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO sessions(session_id, role, user_id, last_active, cart_initialized) VALUES (?, ?, ?, ?, 0)",
        (session_id, role, user_id, time.time()),
    )
    return session_id


def _get_user_row(conn: sqlite3.Connection, table: str, user_id, req, not_found_message: str):
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
    # Backfill older DBs created before `cart_saved` existed.
    buyer_cols = [r[1] for r in conn.execute("PRAGMA table_info(buyers)").fetchall()]
    if "cart_saved" not in buyer_cols:
        conn.execute("ALTER TABLE buyers ADD COLUMN cart_saved INTEGER DEFAULT 0")
    session_cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
    if "cart_initialized" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN cart_initialized INTEGER DEFAULT 0")
    conn.commit()


def handle_request_factory(state_path: str):
    conn = sqlite3.connect(state_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _init_db(conn)
    lock = threading.Lock()

    def _validate_buyer_session(session_id: str, buyer_id: int, req):
        if not session_id:
            return None, _err(req, "INVALID_ARGUMENT", "session_id required")
        cur = conn.execute(
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

    def _ensure_session_cart_initialized(session_id: str, buyer_id: int, req):
        cart_initialized, err = _validate_buyer_session(session_id, buyer_id, req)
        if err:
            return err
        if cart_initialized == 1:
            return None
        conn.execute("DELETE FROM session_cart_items WHERE session_id = ?", (session_id,))
        conn.execute(
            """
            INSERT INTO session_cart_items(session_id, item_id, quantity)
            SELECT ?, item_id, quantity FROM cart_items WHERE buyer_id = ?
            """,
            (session_id, int(buyer_id)),
        )
        conn.execute("UPDATE sessions SET cart_initialized = 1 WHERE session_id = ?", (session_id,))
        return None

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
            if len(name) > MAX_NAME_LEN:
                return _err(req, "INVALID_ARGUMENT", f"name must be at most {MAX_NAME_LEN} characters")
            with lock:
                cur = conn.execute(
                    "INSERT INTO buyers(name, password, purchases_count) VALUES (?, ?, 0)",
                    (name, password),
                )
                conn.commit()
                return _ok(req, {"buyer_id": int(cur.lastrowid)})

        if api == "CreateSeller":
            name = data.get("name")
            password = data.get("password")
            if not name or not password:
                return _err(req, "INVALID_ARGUMENT", "name and password required")
            if len(name) > MAX_NAME_LEN:
                return _err(req, "INVALID_ARGUMENT", f"name must be at most {MAX_NAME_LEN} characters")
            with lock:
                cur = conn.execute(
                    "INSERT INTO sellers(name, password, feedback_up, feedback_down, items_sold) VALUES (?, ?, 0, 0, 0)",
                    (name, password),
                )
                conn.commit()
                return _ok(req, {"seller_id": int(cur.lastrowid)})

        if api == "Login":
            role = data.get("role")
            name = data.get("name")
            password = data.get("password")
            if role not in ("buyer", "seller"):
                return _err(req, "INVALID_ARGUMENT", "role must be buyer or seller")
            table = "buyers" if role == "buyer" else "sellers"
            with lock:
                cur = conn.execute(
                    f"SELECT id, password FROM {table} WHERE name = ? ORDER BY id LIMIT 1",
                    (name,),
                )
                row = cur.fetchone()
                if not row or row[1] != password:
                    return _err(req, "AUTH_FAILED", "invalid credentials")
                user_id = int(row[0])
                session_id = _new_session(conn, role, user_id)
                conn.commit()
                return _ok(req, {"session_id": session_id, "user_id": user_id, "role": role})

        if api == "Logout":
            session_id = data.get("session_id")
            if not session_id:
                return _err(req, "INVALID_ARGUMENT", "session_id required")
            with lock:
                conn.execute("DELETE FROM session_cart_items WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
                conn.commit()
                return _ok(req, {"logged_out": True})

        if api == "ValidateSession":
            session_id = data.get("session_id")
            if not session_id:
                return _err(req, "INVALID_ARGUMENT", "session_id required")
            with lock:
                cur = conn.execute(
                    "SELECT role, user_id, last_active FROM sessions WHERE session_id = ?",
                    (session_id,),
                )
                row = cur.fetchone()
                if not row:
                    return _err(req, "NOT_LOGGED_IN", "invalid session")
                role, user_id, last_active = row
                now = time.time()
                if now - float(last_active) > SESSION_TIMEOUT_SEC:
                    conn.execute("DELETE FROM session_cart_items WHERE session_id = ?", (session_id,))
                    conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
                    conn.commit()
                    return _err(req, "SESSION_TIMEOUT", "session expired")
                conn.execute(
                    "UPDATE sessions SET last_active = ? WHERE session_id = ?",
                    (now, session_id),
                )
                conn.commit()
                return _ok(req, {"role": role, "user_id": int(user_id)})

        if api == "GetSellerRating":
            with lock:
                row, err = _get_user_row(conn, "sellers", data.get("seller_id"), req, "seller not found")
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
            with lock:
                row, err = _get_user_row(conn, "buyers", data.get("buyer_id"), req, "buyer not found")
                if err:
                    return err
                return _ok(req, {"buyer_id": int(row[0]), "purchases_count": int(row[3])})

        if api == "GetCart":
            session_id = data.get("session_id")
            with lock:
                row, err = _get_user_row(conn, "buyers", data.get("buyer_id"), req, "buyer not found")
                if err:
                    return err
                buyer_id = int(row[0])
                if session_id:
                    cart_initialized, err = _validate_buyer_session(session_id, buyer_id, req)
                    if err:
                        return err
                    if cart_initialized == 1:
                        cur = conn.execute(
                            "SELECT item_id, quantity FROM session_cart_items WHERE session_id = ?",
                            (session_id,),
                        )
                    else:
                        cur = conn.execute(
                            "SELECT item_id, quantity FROM cart_items WHERE buyer_id = ?",
                            (buyer_id,),
                        )
                else:
                    cur = conn.execute(
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
            with lock:
                row, err = _get_user_row(conn, "buyers", data.get("buyer_id"), req, "buyer not found")
                if err:
                    return err
                buyer_id = int(row[0])
                if session_id:
                    err = _ensure_session_cart_initialized(session_id, buyer_id, req)
                    if err:
                        return err
                    cur = conn.execute(
                        "SELECT quantity FROM session_cart_items WHERE session_id = ? AND item_id = ?",
                        (session_id, item_id),
                    )
                else:
                    cur = conn.execute(
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
                        conn.execute(
                            "DELETE FROM session_cart_items WHERE session_id = ? AND item_id = ?",
                            (session_id, item_id),
                        )
                    else:
                        conn.execute(
                            "DELETE FROM cart_items WHERE buyer_id = ? AND item_id = ?",
                            (buyer_id, item_id),
                        )
                else:
                    if session_id:
                        conn.execute(
                            """
                            INSERT INTO session_cart_items(session_id, item_id, quantity)
                            VALUES (?, ?, ?)
                            ON CONFLICT(session_id, item_id) DO UPDATE SET quantity = excluded.quantity
                            """,
                            (session_id, item_id, new_qty),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO cart_items(buyer_id, item_id, quantity)
                            VALUES (?, ?, ?)
                            ON CONFLICT(buyer_id, item_id) DO UPDATE SET quantity = excluded.quantity
                            """,
                            (buyer_id, item_id, new_qty),
                        )
                conn.commit()
                return _ok(req, {"item_id": item_id, "quantity": max(new_qty, 0)})

        if api == "ClearCart":
            session_id = data.get("session_id")
            with lock:
                row, err = _get_user_row(conn, "buyers", data.get("buyer_id"), req, "buyer not found")
                if err:
                    return err
                buyer_id = int(row[0])
                if session_id:
                    err = _ensure_session_cart_initialized(session_id, buyer_id, req)
                    if err:
                        return err
                    conn.execute("DELETE FROM session_cart_items WHERE session_id = ?", (session_id,))
                else:
                    conn.execute("DELETE FROM cart_items WHERE buyer_id = ?", (buyer_id,))
                conn.commit()
                return _ok(req, {"cleared": True})

        if api == "SaveCart":
            buyer_id = data.get("buyer_id")
            session_id = data.get("session_id")
            with lock:
                row, err = _get_user_row(conn, "buyers", buyer_id, req, "buyer not found")
                if err:
                    return err
                buyer_id = int(row[0])
                if session_id:
                    cart_initialized, err = _validate_buyer_session(session_id, buyer_id, req)
                    if err:
                        return err
                    if cart_initialized == 1:
                        conn.execute("DELETE FROM cart_items WHERE buyer_id = ?", (buyer_id,))
                        conn.execute(
                            """
                            INSERT INTO cart_items(buyer_id, item_id, quantity)
                            SELECT ?, item_id, quantity FROM session_cart_items WHERE session_id = ?
                            """,
                            (buyer_id, session_id),
                        )
                conn.commit()
                return _ok(req, {"saved": True})

        return _err(req, "UNIMPLEMENTED", f"unknown api {api}")

    return handle


def _status_from_resp(resp: Dict[str, Any]) -> pb.Status:
    if resp.get("ok"):
        return pb.Status(ok=True)
    err = resp.get("error") or {}
    return pb.Status(ok=False, error=pb.Error(code=str(err.get("code", "INTERNAL")), message=str(err.get("message", "request failed"))))


class CustomerDBServicer(pb_grpc.CustomerDBServiceServicer):
    def __init__(self, state_path: str):
        self._handle = handle_request_factory(state_path)

    def _call(self, api: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._handle({"type": "Request", "request_id": None, "api": api, "data": data})

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
        out = self._call("Login", {"role": request.role, "name": request.name, "password": request.password})
        data = out.get("data") or {}
        return pb.LoginResponse(
            status=_status_from_resp(out),
            session_id=str(data.get("session_id", "")),
            user_id=int(data.get("user_id", 0)),
            role=str(data.get("role", "")),
        )

    def Logout(self, request, _context):
        out = self._call("Logout", {"session_id": request.session_id})
        return pb.LogoutResponse(status=_status_from_resp(out), logged_out=bool((out.get("data") or {}).get("logged_out", False)))

    def ValidateSession(self, request, _context):
        out = self._call("ValidateSession", {"session_id": request.session_id})
        data = out.get("data") or {}
        return pb.ValidateSessionResponse(status=_status_from_resp(out), role=str(data.get("role", "")), user_id=int(data.get("user_id", 0)))

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
        return pb.UpdateCartResponse(status=_status_from_resp(out), item_id=str(data.get("item_id", "")), quantity=int(data.get("quantity", 0)))

    def ClearCart(self, request, _context):
        session_id = self._metadata_value(_context, "session-id")
        out = self._call("ClearCart", {"buyer_id": int(request.buyer_id), "session_id": session_id})
        return pb.ClearCartResponse(status=_status_from_resp(out), cleared=bool((out.get("data") or {}).get("cleared", False)))

    def SaveCart(self, request, _context):
        session_id = self._metadata_value(_context, "session-id")
        out = self._call("SaveCart", {"buyer_id": int(request.buyer_id), "session_id": session_id})
        return pb.SaveCartResponse(status=_status_from_resp(out), saved=bool((out.get("data") or {}).get("saved", False)))


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6001)
    parser.add_argument("--state", default="db_customer/state.db")
    args = parser.parse_args()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=32))
    pb_grpc.add_CustomerDBServiceServicer_to_server(CustomerDBServicer(args.state), server)
    server.add_insecure_port(f"{args.host}:{args.port}")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    main()
