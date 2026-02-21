import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from typing import Any, Dict, Tuple


def _http_json(method: str, url: str, payload: Dict[str, Any] | None = None, timeout: float = 10.0) -> Tuple[int, Dict[str, Any]]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return int(resp.getcode()), json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return int(exc.code), json.loads(body) if body else {}


def _must_ok(status: int, resp: Dict[str, Any], step: str) -> Dict[str, Any]:
    if not (200 <= status < 300 and resp.get("ok")):
        raise RuntimeError(f"{step} failed: status={status} resp={resp}")
    return resp.get("data") or {}


def run_purchase_trials(host: str, buyer_port: int, seller_port: int, attempts: int) -> None:
    base_buyer = f"http://{host}:{buyer_port}"
    base_seller = f"http://{host}:{seller_port}"
    suffix = uuid.uuid4().hex[:8]
    seller_name = f"seller_{suffix}"
    buyer_name = f"buyer_{suffix}"
    password = "pass1"

    # Create/login seller and register one item with enough stock for all attempts.
    status, resp = _http_json("POST", f"{base_seller}/seller/accounts", {"name": seller_name, "password": password})
    _must_ok(status, resp, "create seller")
    status, resp = _http_json("POST", f"{base_seller}/seller/login", {"name": seller_name, "password": password})
    seller_session = _must_ok(status, resp, "login seller").get("session_id")
    status, resp = _http_json(
        "POST",
        f"{base_seller}/seller/items",
        {
            "session_id": seller_session,
            "name": "load_test_item",
            "category": 9,
            "keywords": ["load"],
            "condition": "new",
            "price": 1.0,
            "quantity": attempts * 2,
        },
    )
    item_id = _must_ok(status, resp, "register item").get("item_id")

    # Create/login buyer.
    status, resp = _http_json("POST", f"{base_buyer}/buyer/accounts", {"name": buyer_name, "password": password})
    _must_ok(status, resp, "create buyer")
    status, resp = _http_json("POST", f"{base_buyer}/buyer/login", {"name": buyer_name, "password": password})
    buyer_session = _must_ok(status, resp, "login buyer").get("session_id")

    success = 0
    failure = 0
    failure_codes: Counter[str] = Counter()
    t0 = time.time()

    for _ in range(attempts):
        # Keep each attempt independent.
        _http_json("DELETE", f"{base_buyer}/buyer/cart", {"session_id": buyer_session})

        status, resp = _http_json(
            "POST",
            f"{base_buyer}/buyer/cart/items",
            {"session_id": buyer_session, "item_id": item_id, "quantity": 1},
        )
        _must_ok(status, resp, "add item to cart")

        status, resp = _http_json(
            "POST",
            f"{base_buyer}/buyer/purchases",
            {
                "session_id": buyer_session,
                "user_name": buyer_name,
                "credit_card_number": "4111111111111111",
                "expiration_date": "12/2030",
                "security_code": "123",
            },
        )

        if 200 <= status < 300 and resp.get("ok"):
            success += 1
        else:
            failure += 1
            code = ((resp.get("error") or {}).get("code")) or f"HTTP_{status}"
            failure_codes[str(code)] += 1

    elapsed = time.time() - t0
    print(f"Attempts: {attempts}")
    print(f"Succeeded: {success}")
    print(f"Failed: {failure}")
    print(f"Elapsed seconds: {elapsed:.2f}")
    if failure_codes:
        print("Failure code breakdown:")
        for code, count in sorted(failure_codes.items()):
            print(f"  {code}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AddItemToCart + MakePurchase repeatedly and print success/failure stats.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--buyer-port", type=int, default=6003)
    parser.add_argument("--seller-port", type=int, default=6004)
    parser.add_argument("--attempts", type=int, default=100)
    args = parser.parse_args()

    run_purchase_trials(args.host, args.buyer_port, args.seller_port, args.attempts)


if __name__ == "__main__":
    main()
