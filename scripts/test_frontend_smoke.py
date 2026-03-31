import argparse
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from common.cli import ReplicaHttpClient, _with_query


def _assert_ok(resp: Dict[str, Any], context: str) -> Dict[str, Any]:
    if not resp.get("ok"):
        raise RuntimeError(f"{context} failed: {resp}")
    return resp.get("data") or {}


def _wait_for_frontends(client: ReplicaHttpClient, label: str, timeout_sec: float) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            _status, resp, replica = client.request("GET", "/health")
            if resp.get("ok"):
                print(f"{label} frontend healthy via {replica[0]}:{replica[1]}")
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{label} frontend replicas did not become healthy before timeout")


def _parse_replicas(value: str) -> List[Tuple[str, int]]:
    replicas: List[Tuple[str, int]] = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        host, port = raw.rsplit(":", 1)
        replicas.append((host, int(port)))
    if not replicas:
        raise ValueError("at least one replica is required")
    return replicas


def _request(client: ReplicaHttpClient, method: str, path: str, data: Dict[str, Any] | None = None) -> Tuple[Dict[str, Any], Tuple[str, int]]:
    _status, resp, replica = client.request(method, path, data)
    return resp, replica


def _stop_service(compose_file: str, service: str) -> None:
    subprocess.run(
        ["docker", "compose", "-f", compose_file, "stop", service],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _start_service(compose_file: str, service: str) -> None:
    subprocess.run(
        ["docker", "compose", "-f", compose_file, "up", "-d", service],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _seller_flow(client: ReplicaHttpClient, seller_name: str, password: str) -> Tuple[int, str, str]:
    resp, replica = _request(client, "POST", "/seller/accounts", {"name": seller_name, "password": password})
    seller_id = int(_assert_ok(resp, "Create seller account")["seller_id"])
    print(f"seller create ok via {replica[0]}:{replica[1]}")

    resp, replica = _request(client, "POST", "/seller/login", {"name": seller_name, "password": password})
    seller_data = _assert_ok(resp, "Seller login")
    session_id = str(seller_data["session_id"])
    print(f"seller login ok via {replica[0]}:{replica[1]}")

    payload = {
        "session_id": session_id,
        "name": f"book_{int(time.time())}",
        "category": 1,
        "keywords": ["book", "raft"],
        "condition": "new",
        "price": 14.0,
        "quantity": 4,
    }
    resp, replica = _request(client, "POST", "/seller/items", payload)
    item_id = str(_assert_ok(resp, "Register seller item")["item_id"])
    print(f"seller register item ok via {replica[0]}:{replica[1]} item_id={item_id}")
    return seller_id, session_id, item_id


def _buyer_flow(client: ReplicaHttpClient, buyer_name: str, password: str, item_id: str, seller_id: int) -> str:
    resp, replica = _request(client, "POST", "/buyer/accounts", {"name": buyer_name, "password": password})
    _assert_ok(resp, "Create buyer account")
    print(f"buyer create ok via {replica[0]}:{replica[1]}")

    resp, replica = _request(client, "POST", "/buyer/login", {"name": buyer_name, "password": password})
    buyer_data = _assert_ok(resp, "Buyer login")
    session_id = str(buyer_data["session_id"])
    print(f"buyer login ok via {replica[0]}:{replica[1]}")

    search_path = _with_query("/buyer/items/search", {"keyword": ["raft"], "category": 1})
    resp, replica = _request(client, "GET", search_path)
    items = list(_assert_ok(resp, "Buyer search").get("items", []))
    if item_id not in {str(item.get("item_id")) for item in items}:
        raise RuntimeError(f"Buyer search did not return expected item {item_id}")
    print(f"buyer search ok via {replica[0]}:{replica[1]}")

    resp, replica = _request(
        client,
        "POST",
        "/buyer/cart/items",
        {"session_id": session_id, "item_id": item_id, "quantity": 1},
    )
    _assert_ok(resp, "Add item to cart")
    print(f"buyer add-to-cart ok via {replica[0]}:{replica[1]}")

    resp, replica = _request(client, "POST", "/buyer/cart/save", {"session_id": session_id})
    _assert_ok(resp, "Save cart")
    print(f"buyer save-cart ok via {replica[0]}:{replica[1]}")

    cart_path = _with_query("/buyer/cart", {"session_id": session_id})
    resp, replica = _request(client, "GET", cart_path)
    cart_data = _assert_ok(resp, "Display cart")
    if int(cart_data.get("cart", {}).get(item_id, 0)) != 1:
        raise RuntimeError(f"Buyer cart did not contain expected quantity for {item_id}: {cart_data}")
    print(f"buyer cart display ok via {replica[0]}:{replica[1]}")

    rating_resp, replica = _request(client, "GET", f"/buyer/sellers/{seller_id}/rating")
    _assert_ok(rating_resp, "Get seller rating")
    print(f"buyer seller-rating ok via {replica[0]}:{replica[1]}")

    purchase_payload = {
        "session_id": session_id,
        "user_name": buyer_name,
        "credit_card_number": "4111111111111111",
        "expiration_date": "12/2030",
        "security_code": "123",
    }
    for attempt in range(1, 6):
        resp, replica = _request(client, "POST", "/buyer/purchases", purchase_payload)
        if resp.get("ok"):
            print(f"buyer purchase ok via {replica[0]}:{replica[1]} on attempt {attempt}")
            break
        err = resp.get("error") or {}
        if str(err.get("code")) == "PAYMENT_DECLINED":
            print(f"buyer purchase declined via {replica[0]}:{replica[1]} on attempt {attempt}, retrying")
            time.sleep(0.5)
            continue
        raise RuntimeError(f"Purchase failed permanently: {resp}")
    else:
        raise RuntimeError("Purchase was declined too many times by the SOAP service")

    return session_id


def _buyer_failover_check(
    client: ReplicaHttpClient,
    session_id: str,
    compose_file: str,
    service_to_stop: str,
    service_port: int,
) -> None:
    print(f"stopping frontend replica {service_to_stop} to verify client failover")
    _stop_service(compose_file, service_to_stop)
    try:
        client._last_known = 0
        purchases_path = _with_query("/buyer/purchases", {"session_id": session_id})
        resp, replica = _request(client, "GET", purchases_path)
        _assert_ok(resp, "Buyer failover purchases read")
        if replica[1] == service_port:
            raise RuntimeError(f"Failover check still used stopped replica {replica[0]}:{replica[1]}")
        print(f"buyer failover ok: request succeeded via {replica[0]}:{replica[1]}")
    finally:
        print(f"restarting frontend replica {service_to_stop}")
        _start_service(compose_file, service_to_stop)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--buyer-replicas",
        default="127.0.0.1:6301,127.0.0.1:6302,127.0.0.1:6303,127.0.0.1:6304",
    )
    parser.add_argument(
        "--seller-replicas",
        default="127.0.0.1:6401,127.0.0.1:6402,127.0.0.1:6403,127.0.0.1:6404",
    )
    parser.add_argument("--health-timeout", type=float, default=45.0)
    parser.add_argument("--compose-file", default="docker-compose.yml")
    parser.add_argument("--skip-failover", action="store_true")
    args = parser.parse_args()

    buyer_client = ReplicaHttpClient(_parse_replicas(args.buyer_replicas), retries=3, timeout=10.0)
    seller_client = ReplicaHttpClient(_parse_replicas(args.seller_replicas), retries=3, timeout=10.0)

    _wait_for_frontends(buyer_client, "buyer", args.health_timeout)
    _wait_for_frontends(seller_client, "seller", args.health_timeout)

    suffix = str(int(time.time()))
    seller_name = f"seller_{suffix}"
    buyer_name = f"buyer_{suffix}"
    password = "pass"

    seller_id, _seller_session, item_id = _seller_flow(seller_client, seller_name, password)
    buyer_session = _buyer_flow(buyer_client, buyer_name, password, item_id, seller_id)

    if not args.skip_failover:
        _buyer_failover_check(
            buyer_client,
            buyer_session,
            args.compose_file,
            service_to_stop="server_buyer_1",
            service_port=6301,
        )

    print("frontend smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
