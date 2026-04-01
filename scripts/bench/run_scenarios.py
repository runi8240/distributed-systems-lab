import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from common.cli import ReplicaHttpClient, _with_query

HTTP_TIMEOUT = 10.0
HEALTH_TIMEOUT = 45.0
MAX_USER_NAME_LEN = 32
DEFAULT_PASSWORD = "pass"
DEFAULT_CARD_NUMBER = "4111111111111111"
DEFAULT_EXPIRATION = "12/2030"
DEFAULT_SECURITY_CODE = "123"


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


def _http_request(
    client: ReplicaHttpClient,
    method: str,
    path: str,
    data: Dict[str, Any] | None = None,
) -> Tuple[Dict[str, Any], Tuple[str, int]]:
    _status, resp, replica = client.request(method, path, data)
    return resp, replica


def _assert_ok(resp: Dict[str, Any], context: str) -> Dict[str, Any]:
    if not resp.get("ok"):
        raise RuntimeError(f"{context} failed: {resp}")
    return resp.get("data") or {}


def _wait_for_frontend(client: ReplicaHttpClient, label: str, timeout_sec: float) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            resp, replica = _http_request(client, "GET", "/health")
            if resp.get("ok"):
                print(f"{label} frontend healthy via {replica[0]}:{replica[1]}")
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{label} frontend did not become healthy before timeout")


def _run_shell_hook(command: str | None, label: str, settle_sec: float) -> None:
    if not command:
        return
    print(f"running hook [{label}]: {command}")
    subprocess.run(command, shell=True, check=True, text=True)
    if settle_sec > 0:
        time.sleep(settle_sec)


def _make_user_name(unique_prefix: str, role: str, idx: int) -> str:
    digest = hashlib.sha1(unique_prefix.encode("utf-8")).hexdigest()[:6]
    base = f"{role}_{digest}_{idx}"
    return base[:MAX_USER_NAME_LEN]


def _item_name(scope: str, idx: int, iteration: int) -> str:
    digest = hashlib.sha1(scope.encode("utf-8")).hexdigest()[:8]
    return f"item_{digest}_{idx}_{iteration}"


class BenchEnv:
    def __init__(
        self,
        buyer_replicas: Sequence[Tuple[str, int]],
        seller_replicas: Sequence[Tuple[str, int]],
        category: int,
        timeout: float,
    ):
        retries = max(8, len(buyer_replicas) * 2, len(seller_replicas) * 2)
        self.buyer_client = ReplicaHttpClient(buyer_replicas, retries=retries, timeout=timeout)
        self.seller_client = ReplicaHttpClient(seller_replicas, retries=retries, timeout=timeout)
        self.category = int(category)


def _create_seller(env: BenchEnv, name: str, password: str = DEFAULT_PASSWORD) -> int:
    resp, _ = _http_request(env.seller_client, "POST", "/seller/accounts", {"name": name, "password": password})
    return int(_assert_ok(resp, "CreateAccount(seller)")["seller_id"])


def _seller_login(env: BenchEnv, name: str, password: str = DEFAULT_PASSWORD) -> str:
    resp, _ = _http_request(env.seller_client, "POST", "/seller/login", {"name": name, "password": password})
    return str(_assert_ok(resp, "Login(seller)")["session_id"])


def _seller_logout(env: BenchEnv, session_id: str) -> None:
    resp, _ = _http_request(env.seller_client, "POST", "/seller/logout", {"session_id": session_id})
    _assert_ok(resp, "Logout(seller)")


def _register_item(
    env: BenchEnv,
    seller_session: str,
    name: str,
    *,
    quantity: int = 100000,
    price: float = 10.0,
    keywords: Sequence[str] | None = None,
) -> str:
    payload = {
        "session_id": seller_session,
        "name": name,
        "category": env.category,
        "keywords": list(keywords or ["bench", "pa3"]),
        "condition": "new",
        "price": price,
        "quantity": quantity,
    }
    resp, _ = _http_request(env.seller_client, "POST", "/seller/items", payload)
    return str(_assert_ok(resp, "RegisterItemForSale")["item_id"])


def _create_buyer(env: BenchEnv, name: str, password: str = DEFAULT_PASSWORD) -> int:
    resp, _ = _http_request(env.buyer_client, "POST", "/buyer/accounts", {"name": name, "password": password})
    return int(_assert_ok(resp, "CreateAccount(buyer)")["buyer_id"])


def _buyer_login(env: BenchEnv, name: str, password: str = DEFAULT_PASSWORD) -> str:
    resp, _ = _http_request(env.buyer_client, "POST", "/buyer/login", {"name": name, "password": password})
    return str(_assert_ok(resp, "Login(buyer)")["session_id"])


def _buyer_logout(env: BenchEnv, session_id: str) -> None:
    resp, _ = _http_request(env.buyer_client, "POST", "/buyer/logout", {"session_id": session_id})
    _assert_ok(resp, "Logout(buyer)")


def _add_to_cart(env: BenchEnv, session_id: str, item_id: str, quantity: int) -> None:
    resp, _ = _http_request(
        env.buyer_client,
        "POST",
        "/buyer/cart/items",
        {"session_id": session_id, "item_id": item_id, "quantity": quantity},
    )
    _assert_ok(resp, "AddItemToCart")


def _remove_from_cart(env: BenchEnv, session_id: str, item_id: str, quantity: int) -> None:
    resp, _ = _http_request(
        env.buyer_client,
        "DELETE",
        f"/buyer/cart/items/{item_id}",
        {"session_id": session_id, "quantity": quantity},
    )
    _assert_ok(resp, "RemoveItemFromCart")


def _clear_cart(env: BenchEnv, session_id: str) -> None:
    resp, _ = _http_request(env.buyer_client, "DELETE", "/buyer/cart", {"session_id": session_id})
    _assert_ok(resp, "ClearCart")


def _save_cart(env: BenchEnv, session_id: str) -> None:
    resp, _ = _http_request(env.buyer_client, "POST", "/buyer/cart/save", {"session_id": session_id})
    _assert_ok(resp, "SaveCart")


def _purchase_until_success(env: BenchEnv, buyer_name: str, session_id: str, max_attempts: int = 10) -> None:
    payload = {
        "session_id": session_id,
        "user_name": buyer_name,
        "credit_card_number": DEFAULT_CARD_NUMBER,
        "expiration_date": DEFAULT_EXPIRATION,
        "security_code": DEFAULT_SECURITY_CODE,
    }
    last_resp: Dict[str, Any] | None = None
    for _ in range(max_attempts):
        resp, _ = _http_request(env.buyer_client, "POST", "/buyer/purchases", payload)
        if resp.get("ok"):
            return
        last_resp = resp
        err = resp.get("error") or {}
        if str(err.get("code")) != "PAYMENT_DECLINED":
            raise RuntimeError(f"MakePurchase setup failed: {resp}")
        time.sleep(0.05)
    raise RuntimeError(f"MakePurchase setup declined too many times: {last_resp}")


def _wait_until_item_visible(env: BenchEnv, item_id: str, timeout_sec: float = 10.0) -> None:
    deadline = time.time() + timeout_sec
    last_resp: Dict[str, Any] | None = None
    while time.time() < deadline:
        resp, _ = _http_request(env.buyer_client, "GET", f"/buyer/items/{item_id}")
        if resp.get("ok"):
            return
        last_resp = resp
        time.sleep(0.2)
    raise RuntimeError(f"item {item_id} did not become visible before timeout: {last_resp}")


def _make_seller_and_item(
    env: BenchEnv,
    scope: str,
    idx: int,
    *,
    quantity: int = 100000,
) -> Dict[str, Any]:
    seller_name = _make_user_name(f"{scope}_seller", "seller", idx)
    seller_id = _create_seller(env, seller_name)
    seller_session = _seller_login(env, seller_name)
    item_id = _register_item(
        env,
        seller_session,
        _item_name(scope, idx, 0),
        quantity=quantity,
        keywords=["bench", scope[:8]],
    )
    return {
        "seller_name": seller_name,
        "seller_id": seller_id,
        "seller_session": seller_session,
        "item_id": item_id,
    }


def _make_buyer(
    env: BenchEnv,
    scope: str,
    idx: int,
) -> Dict[str, Any]:
    buyer_name = _make_user_name(f"{scope}_buyer", "buyer", idx)
    buyer_id = _create_buyer(env, buyer_name)
    buyer_session = _buyer_login(env, buyer_name)
    return {
        "buyer_name": buyer_name,
        "buyer_id": buyer_id,
        "buyer_session": buyer_session,
    }


def _validate_ok(resp: Dict[str, Any], _state: Dict[str, Any], _iteration: int) -> str:
    _assert_ok(resp, "operation")
    return "ok"


def _validate_purchase(resp: Dict[str, Any], _state: Dict[str, Any], _iteration: int) -> str:
    if resp.get("ok"):
        return "ok"
    err = resp.get("error") or {}
    code = str(err.get("code", "UNKNOWN"))
    if code == "PAYMENT_DECLINED":
        return code
    raise RuntimeError(f"MakePurchase failed unexpectedly: {resp}")


class OperationSpec:
    def __init__(
        self,
        role: str,
        name: str,
        setup_client: Callable[[BenchEnv, str, int, int], Dict[str, Any]],
        perform: Callable[[BenchEnv, Dict[str, Any], int], Tuple[Dict[str, Any], Tuple[str, int]]],
        *,
        before_each: Callable[[BenchEnv, Dict[str, Any], int], None] | None = None,
        validate: Callable[[Dict[str, Any], Dict[str, Any], int], str] = _validate_ok,
    ):
        self.role = role
        self.name = name
        self.setup_client = setup_client
        self.perform = perform
        self.before_each = before_each
        self.validate = validate


def _seller_operations() -> List[OperationSpec]:
    def setup_create(_env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        return {"scope": scope, "idx": idx}

    def perform_create(env: BenchEnv, state: Dict[str, Any], iteration: int):
        name = _make_user_name(f"{state['scope']}_{iteration}", "seller", state["idx"])
        return _http_request(env.seller_client, "POST", "/seller/accounts", {"name": name, "password": DEFAULT_PASSWORD})

    def setup_login(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        seller_name = _make_user_name(scope, "seller", idx)
        _create_seller(env, seller_name)
        return {"seller_name": seller_name}

    def perform_login(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        return _http_request(env.seller_client, "POST", "/seller/login", {"name": state["seller_name"], "password": DEFAULT_PASSWORD})

    def setup_logout(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        seller_name = _make_user_name(scope, "seller", idx)
        _create_seller(env, seller_name)
        return {"seller_name": seller_name, "session_id": ""}

    def before_logout(env: BenchEnv, state: Dict[str, Any], _iteration: int) -> None:
        state["session_id"] = _seller_login(env, state["seller_name"])

    def perform_logout(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        return _http_request(env.seller_client, "POST", "/seller/logout", {"session_id": state["session_id"]})

    def setup_register(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        seller_name = _make_user_name(scope, "seller", idx)
        _create_seller(env, seller_name)
        return {"session_id": _seller_login(env, seller_name), "scope": scope, "idx": idx}

    def perform_register(env: BenchEnv, state: Dict[str, Any], iteration: int):
        payload = {
            "session_id": state["session_id"],
            "name": _item_name(state["scope"], state["idx"], iteration),
            "category": env.category,
            "keywords": ["bench", state["scope"][:8]],
            "condition": "new",
            "price": 9.99,
            "quantity": 1,
        }
        return _http_request(env.seller_client, "POST", "/seller/items", payload)

    def setup_display(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        seller = _make_seller_and_item(env, scope, idx)
        return {"session_id": seller["seller_session"]}

    def perform_display(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        path = _with_query("/seller/items", {"session_id": state["session_id"]})
        return _http_request(env.seller_client, "GET", path)

    return [
        OperationSpec("seller", "CreateAccount", setup_create, perform_create),
        OperationSpec("seller", "Login", setup_login, perform_login),
        OperationSpec("seller", "Logout", setup_logout, perform_logout, before_each=before_logout),
        OperationSpec("seller", "RegisterItemForSale", setup_register, perform_register),
        OperationSpec("seller", "DisplayItemsForSale", setup_display, perform_display),
    ]


def _buyer_operations() -> List[OperationSpec]:
    def setup_create(_env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        return {"scope": scope, "idx": idx}

    def perform_create(env: BenchEnv, state: Dict[str, Any], iteration: int):
        name = _make_user_name(f"{state['scope']}_{iteration}", "buyer", state["idx"])
        return _http_request(env.buyer_client, "POST", "/buyer/accounts", {"name": name, "password": DEFAULT_PASSWORD})

    def setup_login(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        buyer_name = _make_user_name(scope, "buyer", idx)
        _create_buyer(env, buyer_name)
        return {"buyer_name": buyer_name}

    def perform_login(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        return _http_request(env.buyer_client, "POST", "/buyer/login", {"name": state["buyer_name"], "password": DEFAULT_PASSWORD})

    def setup_logout(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        buyer_name = _make_user_name(scope, "buyer", idx)
        _create_buyer(env, buyer_name)
        return {"buyer_name": buyer_name, "session_id": ""}

    def before_logout(env: BenchEnv, state: Dict[str, Any], _iteration: int) -> None:
        state["session_id"] = _buyer_login(env, state["buyer_name"])

    def perform_logout(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        return _http_request(env.buyer_client, "POST", "/buyer/logout", {"session_id": state["session_id"]})

    def setup_search(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        seller = _make_seller_and_item(env, scope, idx)
        _wait_until_item_visible(env, seller["item_id"])
        return {"keyword": scope[:8], "category": env.category, "item_id": seller["item_id"]}

    def perform_search(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        path = _with_query("/buyer/items/search", {"keyword": ["bench", state["keyword"]], "category": state["category"]})
        return _http_request(env.buyer_client, "GET", path)

    def setup_get_item(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        seller = _make_seller_and_item(env, scope, idx)
        _wait_until_item_visible(env, seller["item_id"])
        return {"item_id": seller["item_id"]}

    def perform_get_item(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        return _http_request(env.buyer_client, "GET", f"/buyer/items/{state['item_id']}")

    def setup_add(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        seller = _make_seller_and_item(env, scope, idx)
        _wait_until_item_visible(env, seller["item_id"])
        buyer = _make_buyer(env, scope, idx)
        return {"item_id": seller["item_id"], "session_id": buyer["buyer_session"]}

    def perform_add(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        return _http_request(
            env.buyer_client,
            "POST",
            "/buyer/cart/items",
            {"session_id": state["session_id"], "item_id": state["item_id"], "quantity": 1},
        )

    def setup_remove(env: BenchEnv, scope: str, idx: int, ops: int) -> Dict[str, Any]:
        seller = _make_seller_and_item(env, scope, idx, quantity=max(ops + 10, 1000))
        _wait_until_item_visible(env, seller["item_id"])
        buyer = _make_buyer(env, scope, idx)
        _add_to_cart(env, buyer["buyer_session"], seller["item_id"], ops)
        return {"item_id": seller["item_id"], "session_id": buyer["buyer_session"]}

    def perform_remove(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        return _http_request(
            env.buyer_client,
            "DELETE",
            f"/buyer/cart/items/{state['item_id']}",
            {"session_id": state["session_id"], "quantity": 1},
        )

    def setup_clear(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        seller = _make_seller_and_item(env, scope, idx)
        _wait_until_item_visible(env, seller["item_id"])
        buyer = _make_buyer(env, scope, idx)
        return {"item_id": seller["item_id"], "session_id": buyer["buyer_session"]}

    def before_clear(env: BenchEnv, state: Dict[str, Any], _iteration: int) -> None:
        _clear_cart(env, state["session_id"])
        _add_to_cart(env, state["session_id"], state["item_id"], 1)

    def perform_clear(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        return _http_request(env.buyer_client, "DELETE", "/buyer/cart", {"session_id": state["session_id"]})

    def setup_save(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        seller = _make_seller_and_item(env, scope, idx)
        _wait_until_item_visible(env, seller["item_id"])
        buyer = _make_buyer(env, scope, idx)
        _add_to_cart(env, buyer["buyer_session"], seller["item_id"], 1)
        return {"session_id": buyer["buyer_session"]}

    def perform_save(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        return _http_request(env.buyer_client, "POST", "/buyer/cart/save", {"session_id": state["session_id"]})

    def setup_display(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        seller = _make_seller_and_item(env, scope, idx)
        _wait_until_item_visible(env, seller["item_id"])
        buyer = _make_buyer(env, scope, idx)
        _add_to_cart(env, buyer["buyer_session"], seller["item_id"], 1)
        _save_cart(env, buyer["buyer_session"])
        return {"session_id": buyer["buyer_session"]}

    def perform_display(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        path = _with_query("/buyer/cart", {"session_id": state["session_id"]})
        return _http_request(env.buyer_client, "GET", path)

    def setup_purchases(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        seller = _make_seller_and_item(env, scope, idx, quantity=2000)
        _wait_until_item_visible(env, seller["item_id"])
        buyer = _make_buyer(env, scope, idx)
        _add_to_cart(env, buyer["buyer_session"], seller["item_id"], 1)
        _save_cart(env, buyer["buyer_session"])
        _purchase_until_success(env, buyer["buyer_name"], buyer["buyer_session"])
        return {"session_id": buyer["buyer_session"]}

    def perform_purchases(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        path = _with_query("/buyer/purchases", {"session_id": state["session_id"]})
        return _http_request(env.buyer_client, "GET", path)

    def setup_purchase(env: BenchEnv, scope: str, idx: int, ops: int) -> Dict[str, Any]:
        seller = _make_seller_and_item(env, scope, idx, quantity=max(ops + 100, 2000))
        _wait_until_item_visible(env, seller["item_id"])
        buyer = _make_buyer(env, scope, idx)
        return {
            "session_id": buyer["buyer_session"],
            "buyer_name": buyer["buyer_name"],
            "item_id": seller["item_id"],
        }

    def before_purchase(env: BenchEnv, state: Dict[str, Any], _iteration: int) -> None:
        _clear_cart(env, state["session_id"])
        _add_to_cart(env, state["session_id"], state["item_id"], 1)
        _save_cart(env, state["session_id"])

    def perform_purchase(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        payload = {
            "session_id": state["session_id"],
            "user_name": state["buyer_name"],
            "credit_card_number": DEFAULT_CARD_NUMBER,
            "expiration_date": DEFAULT_EXPIRATION,
            "security_code": DEFAULT_SECURITY_CODE,
        }
        return _http_request(env.buyer_client, "POST", "/buyer/purchases", payload)

    def setup_feedback(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        seller = _make_seller_and_item(env, scope, idx, quantity=1000)
        _wait_until_item_visible(env, seller["item_id"])
        buyer = _make_buyer(env, scope, idx)
        return {"session_id": buyer["buyer_session"], "item_id": seller["item_id"]}

    def perform_feedback(env: BenchEnv, state: Dict[str, Any], iteration: int):
        vote = "up" if iteration % 2 == 0 else "down"
        payload = {"session_id": state["session_id"], "item_id": state["item_id"], "vote": vote}
        return _http_request(env.buyer_client, "POST", "/buyer/feedback", payload)

    def setup_rating(env: BenchEnv, scope: str, idx: int, _ops: int) -> Dict[str, Any]:
        seller = _make_seller_and_item(env, scope, idx)
        _wait_until_item_visible(env, seller["item_id"])
        return {"seller_id": seller["seller_id"]}

    def perform_rating(env: BenchEnv, state: Dict[str, Any], _iteration: int):
        return _http_request(env.buyer_client, "GET", f"/buyer/sellers/{state['seller_id']}/rating")

    return [
        OperationSpec("buyer", "CreateAccount", setup_create, perform_create),
        OperationSpec("buyer", "Login", setup_login, perform_login),
        OperationSpec("buyer", "Logout", setup_logout, perform_logout, before_each=before_logout),
        OperationSpec("buyer", "SearchItemsForSale", setup_search, perform_search),
        OperationSpec("buyer", "GetItem", setup_get_item, perform_get_item),
        OperationSpec("buyer", "AddItemToCart", setup_add, perform_add),
        OperationSpec("buyer", "RemoveItemFromCart", setup_remove, perform_remove),
        OperationSpec("buyer", "SaveCart", setup_save, perform_save),
        OperationSpec("buyer", "ClearCart", setup_clear, perform_clear, before_each=before_clear),
        OperationSpec("buyer", "DisplayCart", setup_display, perform_display),
        OperationSpec("buyer", "GetBuyerPurchases", setup_purchases, perform_purchases),
        OperationSpec("buyer", "MakePurchase", setup_purchase, perform_purchase, before_each=before_purchase, validate=_validate_purchase),
        OperationSpec("buyer", "ProvideFeedback", setup_feedback, perform_feedback),
        OperationSpec("buyer", "GetSellerRating", setup_rating, perform_rating),
    ]


def _worker(
    env: BenchEnv,
    spec: OperationSpec,
    state: Dict[str, Any],
    ops_per_client: int,
    barrier: threading.Barrier,
    timings: List[float],
    timings_lock: threading.Lock,
    outcomes: Counter,
    outcomes_lock: threading.Lock,
    errors: List[str],
    errors_lock: threading.Lock,
) -> None:
    try:
        local_timings: List[float] = []
        local_outcomes: Counter = Counter()
        barrier.wait()
        for iteration in range(ops_per_client):
            if spec.before_each is not None:
                spec.before_each(env, state, iteration)
            start = time.perf_counter()
            resp, _replica = spec.perform(env, state, iteration)
            end = time.perf_counter()
            outcome = spec.validate(resp, state, iteration)
            local_outcomes[outcome] += 1
            local_timings.append(end - start)
        with timings_lock:
            timings.extend(local_timings)
        with outcomes_lock:
            outcomes.update(local_outcomes)
    except Exception as exc:
        with errors_lock:
            errors.append(f"{spec.role}.{spec.name} worker failed: {exc}")


def _run_operation_once(
    env: BenchEnv,
    spec: OperationSpec,
    client_count: int,
    ops_per_client: int,
    run_scope: str,
) -> Dict[str, Any]:
    if client_count <= 0:
        raise RuntimeError("client_count must be positive")

    barrier = threading.Barrier(client_count)
    timings: List[float] = []
    timings_lock = threading.Lock()
    outcomes: Counter = Counter()
    outcomes_lock = threading.Lock()
    errors: List[str] = []
    errors_lock = threading.Lock()
    threads: List[threading.Thread] = []

    for client_idx in range(client_count):
        state = spec.setup_client(env, f"{run_scope}_{spec.role}_{spec.name}", client_idx, ops_per_client)
        thread = threading.Thread(
            target=_worker,
            args=(
                env,
                spec,
                state,
                ops_per_client,
                barrier,
                timings,
                timings_lock,
                outcomes,
                outcomes_lock,
                errors,
                errors_lock,
            ),
            daemon=True,
        )
        threads.append(thread)

    start = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    end = time.perf_counter()

    if errors:
        raise RuntimeError("; ".join(errors[:3]))

    total_ops = client_count * ops_per_client
    success_ops = int(outcomes.get("ok", 0))
    return {
        "avg_response_time_sec": statistics.mean(timings) if timings else 0.0,
        "throughput_ops_per_sec": total_ops / (end - start) if end > start else 0.0,
        "total_ops": total_ops,
        "successful_ops": success_ops,
        "outcomes": dict(outcomes),
    }


def _run_operation(
    env: BenchEnv,
    spec: OperationSpec,
    scenario_name: str,
    client_count: int,
    runs: int,
    ops_per_client: int,
    ts: str,
) -> Dict[str, Any]:
    run_results: List[Dict[str, Any]] = []
    avg_responses: List[float] = []
    throughputs: List[float] = []

    for run_idx in range(1, runs + 1):
        result = _run_operation_once(
            env,
            spec,
            client_count,
            ops_per_client,
            f"{scenario_name}_{ts}_run{run_idx}",
        )
        result["run"] = run_idx
        run_results.append(result)
        avg_responses.append(float(result["avg_response_time_sec"]))
        throughputs.append(float(result["throughput_ops_per_sec"]))

    all_outcomes = Counter()
    for item in run_results:
        all_outcomes.update(item["outcomes"])

    total_ops = client_count * ops_per_client * runs
    success_ops = sum(int(item["successful_ops"]) for item in run_results)
    return {
        "role": spec.role,
        "operation": spec.name,
        "scenario": scenario_name,
        "clients": client_count,
        "runs": runs,
        "ops_per_client": ops_per_client,
        "avg_response_time_sec": statistics.mean(avg_responses),
        "avg_throughput_ops_per_sec": statistics.mean(throughputs),
        "success_rate": (success_ops / total_ops) if total_ops else 0.0,
        "outcomes": dict(all_outcomes),
        "run_results": run_results,
    }


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_markdown(path: Path, payload: Dict[str, Any]) -> None:
    lines: List[str] = []
    lines.append("## PA3 Experiment Setup")
    lines.append("")
    lines.append(f"- Date (UTC): {payload['metadata']['timestamp_utc']}")
    lines.append(f"- Buyer frontend replicas: {', '.join(payload['metadata']['buyer_replicas'])}")
    lines.append(f"- Seller frontend replicas: {', '.join(payload['metadata']['seller_replicas'])}")
    lines.append(f"- Method: {payload['metadata']['runs']} runs per operation, {payload['metadata']['ops_per_client']} operations/client/run")
    lines.append(f"- Failure modes measured: {', '.join(payload['metadata']['failure_modes'])}")
    lines.append("")
    lines.append("## PA3 Performance Results")
    lines.append("")
    lines.append("| Failure Mode | Scenario | Role | Operation | Clients | Avg Response Time (s) | Avg Throughput (ops/s) | Success Rate |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|")

    for mode in payload["results"]:
        for row in mode["operations"]:
            lines.append(
                f"| {mode['failure_mode']} | {row['scenario']} | {row['role']} | {row['operation']} | {row['clients']} | "
                f"{row['avg_response_time_sec']:.6f} | {row['avg_throughput_ops_per_sec']:.2f} | {row['success_rate']:.3f} |"
            )

    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- `MakePurchase` counts `PAYMENT_DECLINED` as a completed API response; inspect JSON outcomes for exact decline counts.")
    lines.append("- Failure-mode hooks are user-provided shell commands so the same benchmark can drive local Docker or GCP deployments.")
    lines.append("- For scenario 1, each measured operation runs with one relevant client (buyer or seller).")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--buyer-replicas", default="")
    parser.add_argument("--seller-replicas", default="")
    parser.add_argument("--buyer-host", default="")
    parser.add_argument("--buyer-port", type=int, default=6003)
    parser.add_argument("--seller-host", default="")
    parser.add_argument("--seller-port", type=int, default=6004)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--ops-per-client", type=int, default=1000)
    parser.add_argument("--category", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=HTTP_TIMEOUT)
    parser.add_argument("--health-timeout", type=float, default=HEALTH_TIMEOUT)
    parser.add_argument("--settle-sec", type=float, default=5.0)
    parser.add_argument(
        "--scenario",
        action="append",
        type=int,
        choices=[1, 2, 3],
        help="Run specific scenario(s). Repeatable. Default: all scenarios.",
    )
    parser.add_argument(
        "--failure-mode",
        action="append",
        choices=["normal", "frontend_failover", "product_follower_fail", "product_leader_fail"],
        help="Failure mode(s) to run. Default: normal only.",
    )
    parser.add_argument("--frontend-failure-start-cmd", default="")
    parser.add_argument("--frontend-failure-stop-cmd", default="")
    parser.add_argument("--product-follower-failure-start-cmd", default="")
    parser.add_argument("--product-follower-failure-stop-cmd", default="")
    parser.add_argument("--product-leader-failure-start-cmd", default="")
    parser.add_argument("--product-leader-failure-stop-cmd", default="")
    parser.add_argument(
        "--output-dir",
        default="scripts/bench/results",
        help="Directory for JSON and Markdown outputs.",
    )
    args = parser.parse_args()

    if args.buyer_replicas:
        buyer_replicas = _parse_replicas(args.buyer_replicas)
    elif args.buyer_host:
        buyer_replicas = [(args.buyer_host, args.buyer_port)]
    else:
        raise ValueError("buyer frontend endpoints are required")

    if args.seller_replicas:
        seller_replicas = _parse_replicas(args.seller_replicas)
    elif args.seller_host:
        seller_replicas = [(args.seller_host, args.seller_port)]
    else:
        raise ValueError("seller frontend endpoints are required")

    env = BenchEnv(buyer_replicas, seller_replicas, args.category, args.timeout)
    _wait_for_frontend(env.buyer_client, "buyer", args.health_timeout)
    _wait_for_frontend(env.seller_client, "seller", args.health_timeout)

    scenarios = [
        ("scenario_1", 1, 1),
        ("scenario_2", 10, 10),
        ("scenario_3", 100, 100),
    ]
    selected_scenarios = {f"scenario_{i}" for i in args.scenario} if args.scenario else None

    failure_modes = args.failure_mode or ["normal"]
    failure_hooks = {
        "normal": {"start": "", "stop": ""},
        "frontend_failover": {"start": args.frontend_failure_start_cmd, "stop": args.frontend_failure_stop_cmd},
        "product_follower_fail": {"start": args.product_follower_failure_start_cmd, "stop": args.product_follower_failure_stop_cmd},
        "product_leader_fail": {"start": args.product_leader_failure_start_cmd, "stop": args.product_leader_failure_stop_cmd},
    }

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    operations = _seller_operations() + _buyer_operations()
    all_results: List[Dict[str, Any]] = []

    for failure_mode in failure_modes:
        hooks = failure_hooks[failure_mode]
        if failure_mode != "normal" and not hooks["start"]:
            raise ValueError(f"{failure_mode} selected, but no start hook was provided")

        _run_shell_hook(hooks["start"], f"{failure_mode}:start", args.settle_sec)
        try:
            _wait_for_frontend(env.buyer_client, "buyer", args.health_timeout)
            _wait_for_frontend(env.seller_client, "seller", args.health_timeout)

            mode_results: List[Dict[str, Any]] = []
            for scenario_name, buyers, sellers in scenarios:
                if selected_scenarios and scenario_name not in selected_scenarios:
                    continue
                for spec in operations:
                    client_count = buyers if spec.role == "buyer" else sellers
                    result = _run_operation(
                        env,
                        spec,
                        scenario_name,
                        client_count,
                        args.runs,
                        args.ops_per_client,
                        ts,
                    )
                    mode_results.append(result)
                    print(
                        f"{failure_mode} {scenario_name} {spec.role}.{spec.name}: "
                        f"avg_response_time={result['avg_response_time_sec']:.6f}s "
                        f"avg_throughput={result['avg_throughput_ops_per_sec']:.2f} ops/s "
                        f"success_rate={result['success_rate']:.3f}"
                    )
            all_results.append({"failure_mode": failure_mode, "operations": mode_results})
        finally:
            _run_shell_hook(hooks["stop"], f"{failure_mode}:stop", args.settle_sec)

    payload = {
        "metadata": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "buyer_replicas": [f"{host}:{port}" for host, port in buyer_replicas],
            "seller_replicas": [f"{host}:{port}" for host, port in seller_replicas],
            "runs": args.runs,
            "ops_per_client": args.ops_per_client,
            "category": args.category,
            "failure_modes": failure_modes,
        },
        "results": all_results,
    }

    output_dir = Path(args.output_dir)
    json_path = output_dir / f"pa3_metrics_{ts}.json"
    md_path = output_dir / f"pa3_metrics_{ts}.md"
    _write_json(json_path, payload)
    _write_markdown(md_path, payload)

    print(f"Saved JSON results: {json_path}")
    print(f"Saved Markdown report snippet: {md_path}")


if __name__ == "__main__":
    main()
