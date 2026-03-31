import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Sequence, Tuple


def _http_json(
    host: str,
    port: int,
    method: str,
    path: str,
    data: Dict[str, Any] | None = None,
    timeout: float = 10.0,
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


def _parse_replicas_arg(replicas: str) -> List[Tuple[str, int]]:
    endpoints: List[Tuple[str, int]] = []
    for raw in replicas.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if ":" not in raw:
            raise ValueError(f"invalid replica '{raw}', expected host:port")
        host, port_str = raw.rsplit(":", 1)
        endpoints.append((host.strip(), int(port_str)))
    if not endpoints:
        raise ValueError("at least one replica is required")
    return endpoints


class ReplicaHttpClient:
    def __init__(self, replicas: Sequence[Tuple[str, int]], *, retries: int = 3, timeout: float = 10.0):
        if not replicas:
            raise ValueError("at least one replica is required")
        self._replicas = [(str(host), int(port)) for host, port in replicas]
        self._last_known = 0
        self._retries = int(retries)
        self._timeout = float(timeout)

    def _ordered_replicas(self) -> List[Tuple[str, int]]:
        ordered: List[Tuple[str, int]] = []
        for offset in range(len(self._replicas)):
            ordered.append(self._replicas[(self._last_known + offset) % len(self._replicas)])
        return ordered

    def request(
        self,
        method: str,
        path: str,
        data: Dict[str, Any] | None = None,
    ) -> Tuple[int, Dict[str, Any], Tuple[str, int]]:
        last_exc: Exception | None = None
        attempts = 0

        while attempts < self._retries:
            for idx, replica in enumerate(self._ordered_replicas()):
                attempts += 1
                host, port = replica
                try:
                    status, resp = _http_json(host, port, method, path, data, timeout=self._timeout)
                    self._last_known = self._replicas.index(replica)
                    return status, resp, replica
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    last_exc = exc
                    print(f"request to replica {host}:{port} failed: {exc}")
                    self._last_known = (self._replicas.index(replica) + 1) % len(self._replicas)
                    if attempts >= self._retries:
                        break
            if attempts < self._retries:
                continue

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("request failed without an exception")


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


def _map_api(role: str, api: str, data: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any] | None]:
    if api == "Ping":
        return "GET", "/health", None

    if role == "seller":
        if api == "CreateAccount":
            return "POST", "/seller/accounts", {"name": data.get("name", ""), "password": data.get("password", "")}
        if api == "Login":
            return "POST", "/seller/login", {"name": data.get("name", ""), "password": data.get("password", "")}
        if api == "Logout":
            return "POST", "/seller/logout", {"session_id": data.get("session_id", "")}
        if api == "DisplayItemsForSale":
            return "GET", _with_query("/seller/items", {"session_id": data.get("session_id", "")}), None
        if api == "RegisterItemForSale":
            payload = {
                "session_id": data.get("session_id", ""),
                "name": data.get("name", ""),
                "category": data.get("category", 0),
                "keywords": list(data.get("keywords", []) or []),
                "condition": data.get("condition", ""),
                "price": data.get("price", 0.0),
                "quantity": data.get("quantity", 0),
            }
            return "POST", "/seller/items", payload
        raise ValueError(f"unsupported seller API: {api}")

    if role == "buyer":
        if api == "CreateAccount":
            return "POST", "/buyer/accounts", {"name": data.get("name", ""), "password": data.get("password", "")}
        if api == "Login":
            return "POST", "/buyer/login", {"name": data.get("name", ""), "password": data.get("password", "")}
        if api == "Logout":
            return "POST", "/buyer/logout", {"session_id": data.get("session_id", "")}
        if api == "SearchItemsForSale":
            keywords = data.get("keywords", []) or []
            params: Dict[str, Any] = {"keyword": keywords}
            if "category" in data:
                params["category"] = data.get("category")
            return "GET", _with_query("/buyer/items/search", params), None
        if api == "GetItem":
            return "GET", f"/buyer/items/{data.get('item_id', '')}", None
        if api == "AddItemToCart":
            return "POST", "/buyer/cart/items", {
                "session_id": data.get("session_id", ""),
                "item_id": data.get("item_id", ""),
                "quantity": data.get("quantity", 0),
            }
        if api == "RemoveItemFromCart":
            return "DELETE", f"/buyer/cart/items/{data.get('item_id', '')}", {
                "session_id": data.get("session_id", ""),
                "quantity": data.get("quantity", 0),
            }
        if api == "SaveCart":
            return "POST", "/buyer/cart/save", {"session_id": data.get("session_id", "")}
        if api == "ClearCart":
            return "DELETE", "/buyer/cart", {"session_id": data.get("session_id", "")}
        if api == "DisplayCart":
            return "GET", _with_query("/buyer/cart", {"session_id": data.get("session_id", "")}), None
        if api == "ProvideFeedback":
            return "POST", "/buyer/feedback", {
                "session_id": data.get("session_id", ""),
                "item_id": data.get("item_id", ""),
                "vote": data.get("vote", ""),
            }
        if api == "GetSellerRating":
            return "GET", f"/buyer/sellers/{int(data.get('seller_id', 0))}/rating", None
        if api == "GetBuyerPurchases":
            return "GET", _with_query("/buyer/purchases", {"session_id": data.get("session_id", "")}), None
        if api == "MakePurchase":
            return "POST", "/buyer/purchases", {
                "session_id": data.get("session_id", ""),
                "user_name": data.get("user_name", ""),
                "credit_card_number": data.get("credit_card_number", ""),
                "expiration_date": data.get("expiration_date", ""),
                "security_code": data.get("security_code", ""),
            }
        raise ValueError(f"unsupported buyer API: {api}")

    raise ValueError(f"unsupported role: {role}")


def repl(replicas: Sequence[Tuple[str, int]], role: str):
    session_id = None
    http_client = ReplicaHttpClient(replicas)

    replica_text = ", ".join(f"{host}:{port}" for host, port in replicas)
    print(f"Connected to frontend replicas [{replica_text}] as {role} client")
    print("Commands: help, create <name> <password>, login <name> <password>, logout, api <API> <json>, makepurchase, session <id>, exit")

    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line:
            continue
        if line in ("exit", "quit"):
            break
        if line == "help":
            print("create <name> <password>")
            print("login <name> <password>")
            print("logout")
            print("api <API> <json>")
            if role == "buyer":
                print("makepurchase  # prompts for card details and calls MakePurchase")
            print("session <id>")
            continue

        parts = line.split(" ", 2)
        cmd = parts[0]

        if cmd == "session" and len(parts) >= 2:
            session_id = parts[1]
            print(f"session_id set to {session_id}")
            continue

        if cmd == "create" and len(parts) >= 3:
            api = "CreateAccount"
            data = {"name": parts[1], "password": parts[2]}
        elif cmd == "login" and len(parts) >= 3:
            api = "Login"
            data = {"name": parts[1], "password": parts[2]}
        elif cmd == "logout":
            api = "Logout"
            data = {"session_id": session_id}
        elif cmd == "makepurchase" and role == "buyer":
            if not session_id:
                print("error: login first (missing session_id)")
                continue
            user_name = input("user name: ").strip()
            card_number = input("credit card number: ").strip()
            expiration_date = input("expiration date (MM/YY or MM/YYYY): ").strip()
            security_code = input("security code (CVV): ").strip()
            api = "MakePurchase"
            data = {
                "session_id": session_id,
                "user_name": user_name,
                "credit_card_number": card_number,
                "expiration_date": expiration_date,
                "security_code": security_code,
            }
        elif cmd == "api" and len(parts) >= 3:
            api = parts[1]
            try:
                data = json.loads(parts[2])
            except json.JSONDecodeError as exc:
                print(f"invalid json: {exc}")
                continue
        else:
            print("unknown command; type 'help'")
            continue

        if session_id and isinstance(data, dict) and "session_id" not in data:
            data["session_id"] = session_id

        try:
            method, path, body = _map_api(role, api, data)
        except ValueError as exc:
            print(f"error: {exc}")
            continue

        try:
            status, resp, replica = http_client.request(method, path, body)
        except Exception as exc:
            print(f"error: {exc}")
            continue

        print(json.dumps({"replica": f"{replica[0]}:{replica[1]}", "status": status, "response": resp}, indent=2))
        if api == "Login" and (resp or {}).get("ok"):
            session_id = (resp.get("data") or {}).get("session_id")
            print(f"session_id set to {session_id}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--replicas", default="")
    parser.add_argument("--role", required=True, choices=["buyer", "seller"])
    args = parser.parse_args()
    if args.replicas:
        replicas = _parse_replicas_arg(args.replicas)
    else:
        replicas = [(args.host, args.port)]
    repl(replicas, args.role)


if __name__ == "__main__":
    main()
