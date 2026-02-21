import argparse
import hashlib
import json
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

HTTP_TIMEOUT = 10.0
RETRY_ATTEMPTS = 3
RETRY_SLEEP_SEC = 0.05
MAX_USER_NAME_LEN = 32


def _http_json(
    host: str,
    port: int,
    method: str,
    path: str,
    data: Dict[str, Any] | None = None,
    timeout: float = HTTP_TIMEOUT,
) -> Tuple[int, Dict[str, Any]]:
    url = f"http://{host}:{port}{path}"
    body = None
    headers = {"Accept": "application/json"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return int(resp.getcode()), json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        if raw:
            try:
                return int(exc.code), json.loads(raw)
            except json.JSONDecodeError:
                return int(exc.code), {"ok": False, "error": {"code": "HTTP_ERROR", "message": raw}, "data": None}
        return int(exc.code), {"ok": False, "error": {"code": "HTTP_ERROR", "message": str(exc)}, "data": None}


def _with_query(path: str, params: Dict[str, Any]) -> str:
    pairs = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                pairs.append((key, str(item)))
        else:
            pairs.append((key, str(value)))
    query = urllib.parse.urlencode(pairs, doseq=True)
    return f"{path}?{query}" if query else path


def _assert_ok(resp: Dict[str, Any], ctx: str) -> None:
    if not resp.get("ok"):
        raise RuntimeError(f"{ctx} failed: {resp}")


def _make_user_name(unique_prefix: str, role: str, idx: int) -> str:
    digest = hashlib.sha1(unique_prefix.encode("utf-8")).hexdigest()[:6]
    base = f"{role}_{digest}_{idx}"
    return base[:MAX_USER_NAME_LEN]


def _request_with_retries(
    host: str,
    port: int,
    method: str,
    path: str,
    data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            status, resp = _http_json(host, port, method, path, data)
            if status >= 500:
                raise RuntimeError(f"server returned {status}: {resp}")
            return resp
        except Exception as exc:  # pragma: no cover
            last_exc = exc
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_SLEEP_SEC)
    if last_exc is None:
        raise RuntimeError("request failed without exception")
    raise last_exc


def _check_health(host: str, port: int, service_name: str) -> None:
    resp = _request_with_retries(host, port, "GET", "/health")
    _assert_ok(resp, f"{service_name} health")


def _seller_setup(seller_host: str, seller_port: int, idx: int, unique_prefix: str, category: int) -> Tuple[str, str]:
    seller_name = _make_user_name(unique_prefix, "seller", idx)
    password = "pass"

    create = _request_with_retries(
        seller_host,
        seller_port,
        "POST",
        "/seller/accounts",
        {"name": seller_name, "password": password},
    )
    _assert_ok(create, "CreateAccount(seller)")

    login = _request_with_retries(
        seller_host,
        seller_port,
        "POST",
        "/seller/login",
        {"name": seller_name, "password": password},
    )
    _assert_ok(login, "Login(seller)")
    session_id = (login.get("data") or {}).get("session_id")
    if not session_id:
        raise RuntimeError("seller login did not return session_id")

    register = _request_with_retries(
        seller_host,
        seller_port,
        "POST",
        "/seller/items",
        {
            "session_id": session_id,
            "name": f"{unique_prefix}_book_{idx}",
            "category": category,
            "keywords": ["book", unique_prefix[:8]],
            "condition": "new",
            "price": 10.0,
            "quantity": 5000,
        },
    )
    _assert_ok(register, "RegisterItemForSale")
    item_id = (register.get("data") or {}).get("item_id")
    if not item_id:
        raise RuntimeError("RegisterItemForSale did not return item_id")

    return str(session_id), str(item_id)


def _buyer_setup(buyer_host: str, buyer_port: int, idx: int, unique_prefix: str) -> str:
    buyer_name = _make_user_name(unique_prefix, "buyer", idx)
    password = "pass"

    create = _request_with_retries(
        buyer_host,
        buyer_port,
        "POST",
        "/buyer/accounts",
        {"name": buyer_name, "password": password},
    )
    _assert_ok(create, "CreateAccount(buyer)")

    login = _request_with_retries(
        buyer_host,
        buyer_port,
        "POST",
        "/buyer/login",
        {"name": buyer_name, "password": password},
    )
    _assert_ok(login, "Login(buyer)")

    session_id = (login.get("data") or {}).get("session_id")
    if not session_id:
        raise RuntimeError("buyer login did not return session_id")
    return str(session_id)


def _setup_users_and_inventory(
    buyer_host: str,
    buyer_port: int,
    seller_host: str,
    seller_port: int,
    buyers: int,
    sellers: int,
    category: int,
    unique_prefix: str,
) -> Tuple[List[str], List[Tuple[str, str]]]:
    seller_sessions: List[Tuple[str, str]] = []
    buyer_sessions: List[str] = []

    for i in range(sellers):
        seller_sessions.append(_seller_setup(seller_host, seller_port, i, unique_prefix, category))

    for i in range(buyers):
        buyer_sessions.append(_buyer_setup(buyer_host, buyer_port, i, unique_prefix))

    return buyer_sessions, seller_sessions


def _buyer_worker(
    host: str,
    port: int,
    ops: int,
    barrier: threading.Barrier,
    timings: List[float],
    timings_lock: threading.Lock,
    keyword: str,
    category: int,
    errors: List[str],
    error_lock: threading.Lock,
) -> None:
    try:
        path = _with_query("/buyer/items/search", {"keyword": ["book", keyword], "category": category})
        barrier.wait()
        local_timings: List[float] = []
        for _ in range(ops):
            start = time.perf_counter()
            resp = _request_with_retries(host, port, "GET", path)
            end = time.perf_counter()
            _assert_ok(resp, "SearchItemsForSale")
            local_timings.append(end - start)

        with timings_lock:
            timings.extend(local_timings)
    except Exception as exc:
        with error_lock:
            errors.append(f"buyer worker failed: {exc}")


def _seller_worker(
    host: str,
    port: int,
    ops: int,
    barrier: threading.Barrier,
    timings: List[float],
    timings_lock: threading.Lock,
    session_id: str,
    errors: List[str],
    error_lock: threading.Lock,
) -> None:
    try:
        path = _with_query("/seller/items", {"session_id": session_id})
        barrier.wait()
        local_timings: List[float] = []
        for _ in range(ops):
            start = time.perf_counter()
            resp = _request_with_retries(host, port, "GET", path)
            end = time.perf_counter()
            _assert_ok(resp, "DisplayItemsForSale")
            local_timings.append(end - start)

        with timings_lock:
            timings.extend(local_timings)
    except Exception as exc:
        with error_lock:
            errors.append(f"seller worker failed: {exc}")


def _run_once(
    buyer_host: str,
    buyer_port: int,
    seller_host: str,
    seller_port: int,
    buyer_sessions: List[str],
    seller_sessions: List[Tuple[str, str]],
    ops_per_client: int,
    category: int,
    keyword: str,
) -> Tuple[float, float, int]:
    total_clients = len(buyer_sessions) + len(seller_sessions)
    if total_clients <= 0:
        raise RuntimeError("run requires at least one client")

    barrier = threading.Barrier(total_clients)
    timings: List[float] = []
    timings_lock = threading.Lock()
    errors: List[str] = []
    error_lock = threading.Lock()

    threads: List[threading.Thread] = []
    for _ in buyer_sessions:
        t = threading.Thread(
            target=_buyer_worker,
            args=(
                buyer_host,
                buyer_port,
                ops_per_client,
                barrier,
                timings,
                timings_lock,
                keyword,
                category,
                errors,
                error_lock,
            ),
            daemon=True,
        )
        threads.append(t)

    for session_id, _item_id in seller_sessions:
        t = threading.Thread(
            target=_seller_worker,
            args=(
                seller_host,
                seller_port,
                ops_per_client,
                barrier,
                timings,
                timings_lock,
                session_id,
                errors,
                error_lock,
            ),
            daemon=True,
        )
        threads.append(t)

    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    end = time.perf_counter()
    if errors:
        raise RuntimeError("; ".join(errors[:3]))

    total_ops = ops_per_client * total_clients
    avg_resp = statistics.mean(timings) if timings else 0.0
    throughput = total_ops / (end - start) if end > start else 0.0
    return avg_resp, throughput, total_ops


def _run_scenario(
    name: str,
    buyer_host: str,
    buyer_port: int,
    seller_host: str,
    seller_port: int,
    buyers: int,
    sellers: int,
    runs: int,
    ops_per_client: int,
    category: int,
    unique_prefix: str,
) -> Dict[str, Any]:
    keyword = unique_prefix[:8]
    buyer_sessions, seller_sessions = _setup_users_and_inventory(
        buyer_host,
        buyer_port,
        seller_host,
        seller_port,
        buyers,
        sellers,
        category,
        unique_prefix,
    )

    run_results = []
    avg_responses = []
    throughputs = []

    for run_idx in range(1, runs + 1):
        avg_resp, throughput, total_ops = _run_once(
            buyer_host,
            buyer_port,
            seller_host,
            seller_port,
            buyer_sessions,
            seller_sessions,
            ops_per_client,
            category,
            keyword,
        )
        run_results.append(
            {
                "run": run_idx,
                "avg_response_time_sec": avg_resp,
                "throughput_ops_per_sec": throughput,
                "total_ops": total_ops,
            }
        )
        avg_responses.append(avg_resp)
        throughputs.append(throughput)

    return {
        "name": name,
        "buyers": buyers,
        "sellers": sellers,
        "clients": buyers + sellers,
        "runs": runs,
        "ops_per_client": ops_per_client,
        "avg_response_time_sec": statistics.mean(avg_responses),
        "avg_throughput_ops_per_sec": statistics.mean(throughputs),
        "run_results": run_results,
        "seller_api_measured": "DisplayItemsForSale",
        "buyer_api_measured": "SearchItemsForSale",
    }


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_markdown(path: Path, payload: Dict[str, Any], buyer_host: str, seller_host: str) -> None:
    lines = []
    lines.append("## PA2 Experiment Setup")
    lines.append("")
    lines.append(f"- Date (UTC): {payload['metadata']['timestamp_utc']}")
    lines.append(f"- Buyer frontend endpoint: http://{buyer_host}:{payload['metadata']['buyer_port']}")
    lines.append(f"- Seller frontend endpoint: http://{seller_host}:{payload['metadata']['seller_port']}")
    lines.append("- Architecture: 4 server components on separate VMs (db_customer, db_product, server_buyer, server_seller)")
    lines.append(
        f"- Method: {payload['metadata']['runs']} runs/scenario, {payload['metadata']['ops_per_client']} operations/client/run"
    )
    lines.append("- APIs used for load generation: Buyer=SearchItemsForSale, Seller=DisplayItemsForSale")
    lines.append("")
    lines.append("## PA2 Performance Results")
    lines.append("")
    lines.append("| Scenario | Buyers + Sellers | Clients | Avg Response Time (s) | Avg Throughput (ops/s) |")
    lines.append("|---|---:|---:|---:|---:|")

    for s in payload["scenarios"]:
        lines.append(
            f"| {s['name']} | {s['buyers']} + {s['sellers']} | {s['clients']} | "
            f"{s['avg_response_time_sec']:.6f} | {s['avg_throughput_ops_per_sec']:.2f} |"
        )

    lines.append("")
    lines.append("## Analysis Notes")
    lines.append("")
    lines.append("- Describe how latency changes from scenario 1 -> 2 -> 3 and where queueing starts to dominate.")
    lines.append("- Explain throughput scaling limits (network RTT, Python/Flask server concurrency, DB contention, VM CPU saturation).")
    lines.append("- Compare this PA2 table against your PA1 table and explain deltas due to REST+gRPC+cross-VM communication overhead.")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--buyer-host", required=True)
    parser.add_argument("--buyer-port", type=int, default=6003)
    parser.add_argument("--seller-host", required=True)
    parser.add_argument("--seller-port", type=int, default=6004)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--ops-per-client", type=int, default=1000)
    parser.add_argument("--category", type=int, default=1)
    parser.add_argument(
        "--scenario",
        action="append",
        type=int,
        choices=[1, 2, 3],
        help="Run specific scenario(s). Repeatable. Default: all scenarios.",
    )
    parser.add_argument(
        "--output-dir",
        default="scripts/bench/results",
        help="Directory for JSON and Markdown outputs.",
    )
    args = parser.parse_args()

    _check_health(args.buyer_host, args.buyer_port, "buyer frontend")
    _check_health(args.seller_host, args.seller_port, "seller frontend")

    scenarios = [
        ("scenario_1", 1, 1),
        ("scenario_2", 10, 10),
        ("scenario_3", 100, 100),
    ]
    selected = {f"scenario_{i}" for i in args.scenario} if args.scenario else None

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    all_results = []

    for name, buyers, sellers in scenarios:
        if selected and name not in selected:
            continue

        unique_prefix = f"{name}_{ts}"
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
            unique_prefix,
        )
        all_results.append(result)

        print(
            f"{result['name']}: avg_response_time={result['avg_response_time_sec']:.6f}s "
            f"avg_throughput={result['avg_throughput_ops_per_sec']:.2f} ops/s "
            f"(buyers={buyers} sellers={sellers} runs={args.runs} ops_per_client={args.ops_per_client})"
        )

    payload = {
        "metadata": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "buyer_host": args.buyer_host,
            "buyer_port": args.buyer_port,
            "seller_host": args.seller_host,
            "seller_port": args.seller_port,
            "runs": args.runs,
            "ops_per_client": args.ops_per_client,
            "category": args.category,
        },
        "scenarios": all_results,
    }

    output_dir = Path(args.output_dir)
    json_path = output_dir / f"pa2_metrics_{ts}.json"
    md_path = output_dir / f"pa2_metrics_{ts}.md"
    _write_json(json_path, payload)
    _write_markdown(md_path, payload, args.buyer_host, args.seller_host)

    print(f"Saved JSON results: {json_path}")
    print(f"Saved Markdown report snippet: {md_path}")


if __name__ == "__main__":
    main()
