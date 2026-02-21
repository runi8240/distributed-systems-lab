import os
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from flask import Flask, jsonify, request
from zeep import Client as SoapClient
from zeep.exceptions import Error as ZeepError

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


def _error(code: str, message: str) -> Dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _valid_luhn(card_digits: str) -> bool:
    total = 0
    parity = len(card_digits) % 2
    for idx, ch in enumerate(card_digits):
        d = int(ch)
        if idx % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _validate_payment_info(user_name: str, card_number: str, exp_date: str, security_code: str) -> str | None:
    if not user_name.strip():
        return "user name is required"

    digits = re.sub(r"[\s-]", "", card_number)
    if not digits.isdigit() or not (13 <= len(digits) <= 19):
        return "credit card number must contain 13-19 digits"
    if not _valid_luhn(digits):
        return "invalid credit card number"

    if not security_code.isdigit() or len(security_code) not in (3, 4):
        return "security code must be 3 or 4 digits"

    exp = exp_date.strip()
    m = re.match(r"^(\d{2})/(\d{2}|\d{4})$", exp)
    if not m:
        return "expiration date must be MM/YY or MM/YYYY"
    month = int(m.group(1))
    year = int(m.group(2))
    if year < 100:
        year += 2000
    if month < 1 or month > 12:
        return "expiration month must be between 01 and 12"
    now = datetime.now(timezone.utc)
    if (year, month) < (now.year, now.month):
        return "credit card is expired"
    return None


def create_app(customer_host: str, customer_port: int, product_host: str, product_port: int, soap_wsdl: str) -> Flask:
    app = Flask(__name__)
    customer_db = CustomerDBClient(customer_host, customer_port)
    product_db = ProductDBClient(product_host, product_port)
    soap_client = SoapClient(soap_wsdl)

    def request_id() -> str:
        return request.headers.get("X-Request-Id", str(uuid.uuid4()))

    def require_session(session_id: str, req_id: str):
        if not session_id:
            return None, {"ok": False, "error": {"code": "NOT_LOGGED_IN", "message": "session_id required"}}
        sess = customer_db.call("ValidateSession", {"session_id": session_id}, req_id)
        if not sess.get("ok"):
            return None, sess
        if (sess.get("data") or {}).get("role") != "buyer":
            return None, {"ok": False, "error": {"code": "NOT_AUTHORIZED", "message": "buyer session required"}}
        return sess.get("data") or {}, None

    @app.get("/health")
    def health():
        return jsonify({"ok": True}), 200

    @app.post("/buyer/accounts")
    def create_account():
        payload = request.get_json(silent=True) or {}
        resp = customer_db.call(
            "CreateBuyer",
            {"name": payload.get("name", ""), "password": payload.get("password", "")},
            request_id(),
        )
        return _resp_to_http(resp)

    @app.post("/buyer/login")
    def login():
        payload = request.get_json(silent=True) or {}
        resp = customer_db.call(
            "Login",
            {"role": "buyer", "name": payload.get("name", ""), "password": payload.get("password", "")},
            request_id(),
        )
        return _resp_to_http(resp)

    @app.post("/buyer/logout")
    def logout():
        payload = request.get_json(silent=True) or {}
        resp = customer_db.call("Logout", {"session_id": payload.get("session_id", "")}, request_id())
        return _resp_to_http(resp)

    @app.get("/buyer/items/search")
    def search_items():
        category = request.args.get("category")
        keywords = request.args.getlist("keyword")
        payload: Dict[str, Any] = {"keywords": keywords}
        if category is not None and category != "":
            payload["category"] = int(category)
        resp = product_db.call("SearchItems", payload, request_id())
        return _resp_to_http(resp)

    @app.get("/buyer/items/<item_id>")
    def get_item(item_id: str):
        resp = product_db.call("GetItem", {"item_id": item_id}, request_id())
        return _resp_to_http(resp)

    @app.get("/buyer/cart")
    def display_cart():
        session_id = request.args.get("session_id", "")
        req_id = request_id()
        sess_data, err = require_session(session_id, req_id)
        if err:
            return _resp_to_http(err)
        resp = customer_db.call(
            "GetCart",
            {"buyer_id": int(sess_data["user_id"]), "session_id": session_id},
            req_id,
        )
        return _resp_to_http(resp)

    @app.post("/buyer/cart/items")
    def add_item_to_cart():
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id", ""))
        item_id = str(payload.get("item_id", ""))
        quantity = int(payload.get("quantity", 0))
        if not item_id or quantity <= 0:
            return _resp_to_http({"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": "item_id and positive quantity required"}})
        req_id = request_id()
        sess_data, err = require_session(session_id, req_id)
        if err:
            return _resp_to_http(err)
        avail = product_db.call("CheckAvailability", {"item_id": item_id, "quantity": quantity}, req_id)
        if not avail.get("ok"):
            return _resp_to_http(avail)
        if not (avail.get("data") or {}).get("ok"):
            return _resp_to_http({"ok": False, "error": {"code": "OUT_OF_STOCK", "message": "requested quantity not available"}})
        resp = customer_db.call(
            "UpdateCart",
            {"buyer_id": int(sess_data["user_id"]), "item_id": item_id, "quantity_delta": quantity, "session_id": session_id},
            req_id,
        )
        return _resp_to_http(resp)

    @app.delete("/buyer/cart/items/<item_id>")
    def remove_item_from_cart(item_id: str):
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id", ""))
        quantity = int(payload.get("quantity", 0))
        if quantity <= 0:
            return _resp_to_http({"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": "positive quantity required"}})
        req_id = request_id()
        sess_data, err = require_session(session_id, req_id)
        if err:
            return _resp_to_http(err)
        resp = customer_db.call(
            "UpdateCart",
            {"buyer_id": int(sess_data["user_id"]), "item_id": item_id, "quantity_delta": -quantity, "session_id": session_id},
            req_id,
        )
        return _resp_to_http(resp)

    @app.delete("/buyer/cart")
    def clear_cart():
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id", ""))
        req_id = request_id()
        sess_data, err = require_session(session_id, req_id)
        if err:
            return _resp_to_http(err)
        resp = customer_db.call(
            "ClearCart",
            {"buyer_id": int(sess_data["user_id"]), "session_id": session_id},
            req_id,
        )
        return _resp_to_http(resp)

    @app.post("/buyer/cart/save")
    def save_cart():
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id", ""))
        req_id = request_id()
        sess_data, err = require_session(session_id, req_id)
        if err:
            return _resp_to_http(err)
        resp = customer_db.call(
            "SaveCart",
            {"buyer_id": int(sess_data["user_id"]), "session_id": session_id},
            req_id,
        )
        return _resp_to_http(resp)

    @app.get("/buyer/purchases")
    def get_purchases():
        session_id = request.args.get("session_id", "")
        req_id = request_id()
        sess_data, err = require_session(session_id, req_id)
        if err:
            return _resp_to_http(err)
        resp = customer_db.call("GetBuyerPurchases", {"buyer_id": int(sess_data["user_id"])}, req_id)
        return _resp_to_http(resp)

    @app.post("/buyer/purchases")
    def make_purchase():
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id", ""))
        user_name = str(payload.get("user_name", ""))
        credit_card_number = str(payload.get("credit_card_number", ""))
        expiration_date = str(payload.get("expiration_date", ""))
        security_code = str(payload.get("security_code", ""))
        req_id = request_id()

        sess_data, err = require_session(session_id, req_id)
        if err:
            return _resp_to_http(err)
        buyer_id = int(sess_data["user_id"])

        val_err = _validate_payment_info(user_name, credit_card_number, expiration_date, security_code)
        if val_err:
            return _resp_to_http(_error("INVALID_PAYMENT_INFO", val_err))

        cart_resp = customer_db.call("GetCart", {"buyer_id": buyer_id, "session_id": session_id}, req_id)
        if not cart_resp.get("ok"):
            return _resp_to_http(cart_resp)
        cart = (cart_resp.get("data") or {}).get("cart", {})
        if not cart:
            return _resp_to_http(_error("CART_EMPTY", "cart is empty"))

        for item_id, qty in cart.items():
            avail = product_db.call("CheckAvailability", {"item_id": str(item_id), "quantity": int(qty)}, req_id)
            if not avail.get("ok"):
                return _resp_to_http(avail)
            if not (avail.get("data") or {}).get("ok"):
                available_units = int((avail.get("data") or {}).get("available", 0))
                return _resp_to_http(
                    _error("OUT_OF_STOCK", f"item {item_id} has only {available_units} units available")
                )

        try:
            bank_result = soap_client.service.process_transaction(
                user_name,
                credit_card_number,
                expiration_date,
                security_code,
            )
        except ZeepError as exc:
            return _resp_to_http(_error("BANK_UNAVAILABLE", f"financial transaction service error: {exc}"))
        except Exception as exc:  # pragma: no cover
            return _resp_to_http(_error("BANK_UNAVAILABLE", f"unexpected bank service error: {exc}"))

        if str(bank_result).strip().lower() != "yes":
            return _resp_to_http(_error("PAYMENT_DECLINED", "bank declined the transaction"))

        updated_items: list[tuple[str, int]] = []
        for item_id, qty in cart.items():
            update = product_db.call(
                "UpdateUnitsForSale",
                {"item_id": str(item_id), "quantity_delta": -int(qty)},
                req_id,
            )
            if not update.get("ok"):
                for rollback_item, rollback_qty in updated_items:
                    product_db.call(
                        "UpdateUnitsForSale",
                        {"item_id": rollback_item, "quantity_delta": int(rollback_qty)},
                        req_id,
                    )
                return _resp_to_http(
                    _error("PURCHASE_FAILED", f"failed to update inventory for item {item_id}; transaction rolled back")
                )
            updated_items.append((str(item_id), int(qty)))

        warnings = []
        clear_resp = customer_db.call("ClearCart", {"buyer_id": buyer_id, "session_id": session_id}, req_id)
        if not clear_resp.get("ok"):
            warnings.append("purchase completed, but failed to clear session cart")
        else:
            save_resp = customer_db.call("SaveCart", {"buyer_id": buyer_id, "session_id": session_id}, req_id)
            if not save_resp.get("ok"):
                warnings.append("purchase completed, but failed to persist cleared cart")

        return _resp_to_http(
            {
                "ok": True,
                "data": {
                    "result": "Yes",
                    "purchased_items": [{"item_id": item_id, "quantity": qty} for item_id, qty in cart.items()],
                    "warnings": warnings,
                },
            }
        )

    @app.post("/buyer/feedback")
    def provide_feedback():
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id", ""))
        req_id = request_id()
        _, err = require_session(session_id, req_id)
        if err:
            return _resp_to_http(err)
        resp = product_db.call(
            "ProvideFeedback",
            {"item_id": payload.get("item_id", ""), "vote": payload.get("vote", "")},
            req_id,
        )
        return _resp_to_http(resp)

    @app.get("/buyer/sellers/<int:seller_id>/rating")
    def get_seller_rating(seller_id: int):
        resp = customer_db.call("GetSellerRating", {"seller_id": seller_id}, request_id())
        return _resp_to_http(resp)

    return app


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6003)
    parser.add_argument("--customer-host", default="127.0.0.1")
    parser.add_argument("--customer-port", type=int, default=6001)
    parser.add_argument("--product-host", default="127.0.0.1")
    parser.add_argument("--product-port", type=int, default=6002)
    parser.add_argument(
        "--soap-wsdl",
        default=os.environ.get("FINANCIAL_SOAP_WSDL", "http://soap_financial:8008/?wsdl"),
    )
    args = parser.parse_args()

    app = create_app(args.customer_host, args.customer_port, args.product_host, args.product_port, args.soap_wsdl)
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
