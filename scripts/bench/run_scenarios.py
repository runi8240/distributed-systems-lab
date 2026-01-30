import argparse
import os
import socket
import statistics
import sys
import threading
import time
from typing import List, Tuple

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from common.tcp_client import tcp_request
from common.protocol import recv_msg, send_msg

CONNECT_TIMEOUT = 5
RETRY_ATTEMPTS = 3
RETRY_SLEEP_SEC = 0.05


def _request(host, port, api, data=None, request_id="1"):
    return tcp_request(
        host,
        port,
        {
            "type": "Request",
            "request_id": request_id,
            "api": api,
            "data": data or {},
        },
    )


def _request_on_socket(sock, api, data=None, request_id="1"):
    send_msg(
        sock,
        {
            "type": "Request",
            "request_id": request_id,
            "api": api,
            "data": data or {},
        },
    )
    return recv_msg(sock)


def _send_with_retries(host, port, sock, api, data):
    last_exc = None
    for _ in range(RETRY_ATTEMPTS):
        try:
            return _request_on_socket(sock, api, data), sock
        except ConnectionError as exc:
            last_exc = exc
            try:
                sock.close()
            except Exception:
                pass
            time.sleep(RETRY_SLEEP_SEC)
            sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
    raise last_exc


def _assert_ok(resp, ctx):
    if not resp.get("ok"):
        raise RuntimeError(f"{ctx} failed: {resp}")


def _create_seller(seller_host, seller_port, idx: int):
    name = f"seller{idx}"
    resp = _request(seller_host, seller_port, "CreateAccount", {"name": name, "password": "pass"})
    _assert_ok(resp, "CreateAccount")
    login = _request(seller_host, seller_port, "Login", {"name": name, "password": "pass"})
    _assert_ok(login, "Login")
    session_id = login["data"]["session_id"]
    reg = _request(
        seller_host,
        seller_port,
        "RegisterItemForSale",
        {
            "session_id": session_id,
            "name": f"Book {idx}",
            "category": 1,
            "keywords": ["book"],
            "condition": "new",
            "price": 10.0,
            "quantity": 100,
        },
    )
    _assert_ok(reg, "RegisterItemForSale")
    return session_id, reg["data"]["item_id"]


def _create_buyer(buyer_host, buyer_port, idx: int):
    name = f"buyer{idx}"
    resp = _request(buyer_host, buyer_port, "CreateAccount", {"name": name, "password": "pass"})
    _assert_ok(resp, "CreateAccount")
    login = _request(buyer_host, buyer_port, "Login", {"name": name, "password": "pass"})
    _assert_ok(login, "Login")
    return login["data"]["session_id"]


def _setup_sellers(seller_host, seller_port, count: int):
    sessions: List[Tuple[str, str]] = []
    sock = socket.create_connection((seller_host, seller_port), timeout=CONNECT_TIMEOUT)
    try:
        for i in range(count):
            name = f"seller{i}"
            resp, sock = _send_with_retries(
                seller_host, seller_port, sock, "CreateAccount", {"name": name, "password": "pass"}
            )
            _assert_ok(resp, "CreateAccount")
            login, sock = _send_with_retries(
                seller_host, seller_port, sock, "Login", {"name": name, "password": "pass"}
            )
            _assert_ok(login, "Login")
            session_id = login["data"]["session_id"]
            reg, sock = _send_with_retries(
                seller_host,
                seller_port,
                sock,
                "RegisterItemForSale",
                {
                    "session_id": session_id,
                    "name": f"Book {i}",
                    "category": 1,
                    "keywords": ["book"],
                    "condition": "new",
                    "price": 10.0,
                    "quantity": 100,
                },
            )
            _assert_ok(reg, "RegisterItemForSale")
            sessions.append((session_id, reg["data"]["item_id"]))
    finally:
        sock.close()
    return sessions


def _setup_buyers(buyer_host, buyer_port, count: int):
    # Buyers don't need sessions for SearchItemsForSale; keep this optional.
    sessions = []
    sock = socket.create_connection((buyer_host, buyer_port), timeout=CONNECT_TIMEOUT)
    try:
        for i in range(count):
            name = f"buyer{i}"
            resp, sock = _send_with_retries(
                buyer_host, buyer_port, sock, "CreateAccount", {"name": name, "password": "pass"}
            )
            _assert_ok(resp, "CreateAccount")
            login, sock = _send_with_retries(
                buyer_host, buyer_port, sock, "Login", {"name": name, "password": "pass"}
            )
            _assert_ok(login, "Login")
            sessions.append(login["data"]["session_id"])
    finally:
        sock.close()
    return sessions


def _buyer_worker(host, port, ops, barrier, timings, category):
    barrier.wait()
    sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
    try:
        for _ in range(ops):
            start = time.perf_counter()
            try:
                resp, sock = _send_with_retries(
                    host,
                    port,
                    sock,
                    "SearchItemsForSale",
                    {"keywords": ["book"], "category": category},
                )
            except ConnectionError:
                resp, sock = _send_with_retries(
                    host,
                    port,
                    sock,
                    "SearchItemsForSale",
                    {"keywords": ["book"], "category": category},
                )
            end = time.perf_counter()
            _assert_ok(resp, "SearchItemsForSale")
            timings.append(end - start)
    finally:
        sock.close()


def _seller_worker(host, port, session_id, item_id, ops, barrier, timings):
    price = 10.0
    barrier.wait()
    sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
    try:
        for _ in range(ops):
            price = 11.0 if price == 10.0 else 10.0
            start = time.perf_counter()
            try:
                resp, sock = _send_with_retries(
                    host,
                    port,
                    sock,
                    "ChangeItemPrice",
                    {"session_id": session_id, "item_id": item_id, "price": price},
                )
            except ConnectionError:
                resp, sock = _send_with_retries(
                    host,
                    port,
                    sock,
                    "ChangeItemPrice",
                    {"session_id": session_id, "item_id": item_id, "price": price},
                )
            end = time.perf_counter()
            _assert_ok(resp, "ChangeItemPrice")
            timings.append(end - start)
    finally:
        sock.close()


def _run_once(buyer_host, buyer_port, seller_host, seller_port, seller_sessions, buyers, ops_per_client, category):

    total_clients = buyers + len(seller_sessions)
    barrier = threading.Barrier(total_clients)
    timings: List[float] = []

    threads = []
    for i in range(buyers):
        t = threading.Thread(
            target=_buyer_worker,
            args=(buyer_host, buyer_port, ops_per_client, barrier, timings, category),
            daemon=True,
        )
        threads.append(t)
    for i, (session_id, item_id) in enumerate(seller_sessions):
        t = threading.Thread(
            target=_seller_worker,
            args=(seller_host, seller_port, session_id, item_id, ops_per_client, barrier, timings),
            daemon=True,
        )
        threads.append(t)

    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    end = time.perf_counter()

    total_ops = ops_per_client * total_clients
    avg_resp = statistics.mean(timings) if timings else 0.0
    throughput = total_ops / (end - start) if end > start else 0.0
    return avg_resp, throughput


def _run_scenario(
    name,
    buyer_host,
    buyer_port,
    seller_host,
    seller_port,
    buyers,
    sellers,
    runs,
    ops_per_client,
    category,
):
    # Create sellers once per scenario to avoid exhausting local ports during setup.
    seller_sessions = _setup_sellers(seller_host, seller_port, sellers)
    avg_resps = []
    throughputs = []
    for _ in range(runs):
        avg_resp, throughput = _run_once(
            buyer_host,
            buyer_port,
            seller_host,
            seller_port,
            seller_sessions,
            buyers,
            ops_per_client,
            category,
        )
        avg_resps.append(avg_resp)
        throughputs.append(throughput)
    return {
        "name": name,
        "avg_response_time": statistics.mean(avg_resps),
        "avg_throughput": statistics.mean(throughputs),
        "runs": runs,
        "clients": buyers + sellers,
        "ops_per_client": ops_per_client,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--buyer-host", default="127.0.0.1")
    parser.add_argument("--buyer-port", type=int, default=6003)
    parser.add_argument("--seller-host", default="127.0.0.1")
    parser.add_argument("--seller-port", type=int, default=6004)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--ops-per-client", type=int, default=1000)
    parser.add_argument("--category", type=int, default=1)
    args = parser.parse_args()

    scenarios = [
        ("scenario_1", 1, 1),
        ("scenario_2", 10, 10),
        ("scenario_3", 100, 100),
    ]

    for name, buyers, sellers in scenarios:
        result = _run_scenario(
            name,
            args.buyer_host,
            args.buyer_port,
            args.seller_host,
            args.seller_port,
            buyers,
            sellers,
            args.runs,
            args.ops_per_client,
            args.category,
        )
        print(
            f"{result['name']}: avg_response_time={result['avg_response_time']:.6f}s "
            f"avg_throughput={result['avg_throughput']:.2f} ops/s "
            f"(clients={result['clients']} runs={result['runs']} ops_per_client={result['ops_per_client']})"
        )


if __name__ == "__main__":
    main()
