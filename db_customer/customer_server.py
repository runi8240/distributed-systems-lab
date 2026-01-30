import os
import sqlite3
import sys
import threading
import time
import uuid
from typing import Any, Dict

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from common.tcp_server import run_server

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
        "INSERT INTO sessions(session_id, role, user_id, last_active) VALUES (?, ?, ?, ?)",
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
            purchases_count INTEGER DEFAULT 0
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
            last_active REAL
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
    conn.commit()


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
            with lock:
                row, err = _get_user_row(conn, "buyers", data.get("buyer_id"), req, "buyer not found")
                if err:
                    return err
                buyer_id = int(row[0])
                cur = conn.execute(
                    "SELECT item_id, quantity FROM cart_items WHERE buyer_id = ?",
                    (buyer_id,),
                )
                cart = {item_id: int(qty) for item_id, qty in cur.fetchall()}
                return _ok(req, {"cart": cart})

        if api == "UpdateCart":
            item_id = data.get("item_id")
            delta = int(data.get("quantity_delta", 0))
            if not item_id or delta == 0:
                return _err(req, "INVALID_ARGUMENT", "item_id and quantity_delta required")
            with lock:
                row, err = _get_user_row(conn, "buyers", data.get("buyer_id"), req, "buyer not found")
                if err:
                    return err
                buyer_id = int(row[0])
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
                    conn.execute(
                        "DELETE FROM cart_items WHERE buyer_id = ? AND item_id = ?",
                        (buyer_id, item_id),
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
            with lock:
                row, err = _get_user_row(conn, "buyers", data.get("buyer_id"), req, "buyer not found")
                if err:
                    return err
                buyer_id = int(row[0])
                conn.execute("DELETE FROM cart_items WHERE buyer_id = ?", (buyer_id,))
                conn.commit()
                return _ok(req, {"cleared": True})

        return _err(req, "UNIMPLEMENTED", f"unknown api {api}")

    return handle


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6001)
    parser.add_argument("--state", default="db_customer/state.db")
    args = parser.parse_args()

    handler = handle_request_factory(args.state)
    run_server(args.host, args.port, handler)


if __name__ == "__main__":
    main()
