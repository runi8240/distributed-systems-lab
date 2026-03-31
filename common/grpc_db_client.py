import os
import re
import sys
from typing import Any, Dict, List, Sequence, Tuple

try:
    import grpc
except ModuleNotFoundError as exc:
    raise RuntimeError("grpcio is required. Install dependencies from requirements.txt.") from exc

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from common.grpc_gen import marketplace_pb2 as pb
    from common.grpc_gen import marketplace_pb2_grpc as pb_grpc
except ModuleNotFoundError as exc:
    raise RuntimeError(
        "Generated protobuf modules are missing. Run scripts/gen_protos.sh after installing grpcio-tools."
    ) from exc


def _status_to_error(status: pb.Status):
    if status.ok:
        return None
    return {
        "code": status.error.code or "INTERNAL",
        "message": status.error.message or "request failed",
    }


def _rpc_error_response(request_id: Any, exc: grpc.RpcError) -> Dict[str, Any]:
    return {
        "type": "Response",
        "request_id": request_id,
        "ok": False,
        "error": {
            "code": "UNAVAILABLE",
            "message": exc.details() or str(exc),
        },
        "data": None,
    }


def _resp(request_id: Any, status: pb.Status, data: Dict[str, Any] | None = None) -> Dict[str, Any]:
    err = _status_to_error(status)
    return {
        "type": "Response",
        "request_id": request_id,
        "ok": bool(status.ok),
        "error": err,
        "data": data if status.ok else None,
    }


def _item_to_dict(item: pb.Item) -> Dict[str, Any]:
    return {
        "item_id": item.item_id,
        "name": item.name,
        "category": int(item.category),
        "keywords": list(item.keywords),
        "condition": item.condition,
        "price": float(item.price),
        "quantity": int(item.quantity),
        "seller_id": int(item.seller_id),
        "feedback": {
            "up": int(item.feedback.up),
            "down": int(item.feedback.down),
        },
    }


def _parse_leader_from_message(message: str) -> Tuple[str, int] | None:
    match = re.search(r"([A-Za-z0-9_.-]+:\d+)", message or "")
    if not match:
        return None
    host, port = match.group(1).rsplit(":", 1)
    try:
        return host, int(port)
    except ValueError:
        return None


class _ReplicaClientBase:
    def __init__(self, endpoints: Sequence[Tuple[str, int]]):
        if not endpoints:
            raise ValueError("at least one endpoint is required")
        self._endpoints: List[Tuple[str, int]] = [(str(host), int(port)) for host, port in endpoints]
        self._last_known = self._endpoints[0]

    def _ordered_endpoints(self) -> List[Tuple[str, int]]:
        ordered: List[Tuple[str, int]] = []
        if self._last_known in self._endpoints:
            ordered.append(self._last_known)
        for endpoint in self._endpoints:
            if endpoint not in ordered:
                ordered.append(endpoint)
        return ordered

    def _record_success(self, endpoint: Tuple[str, int]) -> None:
        if endpoint not in self._endpoints:
            self._endpoints.append(endpoint)
        self._last_known = endpoint


class CustomerDBClient(_ReplicaClientBase):
    def __init__(self, host: str, port: int, members: Sequence[Tuple[str, int]] | None = None):
        endpoints = list(members) if members is not None else [(host, port)]
        super().__init__(endpoints)
        self._stubs: Dict[Tuple[str, int], pb_grpc.CustomerDBServiceStub] = {}

    def _stub_for(self, endpoint: Tuple[str, int]) -> pb_grpc.CustomerDBServiceStub:
        stub = self._stubs.get(endpoint)
        if stub is None:
            channel = grpc.insecure_channel(f"{endpoint[0]}:{endpoint[1]}")
            stub = pb_grpc.CustomerDBServiceStub(channel)
            self._stubs[endpoint] = stub
        return stub

    def _call_once(self, endpoint: Tuple[str, int], api: str, data: Dict[str, Any], request_id: Any) -> Dict[str, Any]:
        stub = self._stub_for(endpoint)

        try:
            if api == "Ping":
                out = stub.Ping(pb.Empty(), timeout=2.0)
                return _resp(request_id, out.status, {"now": float(out.now)})
            if api == "CreateBuyer":
                out = stub.CreateBuyer(pb.CreateBuyerRequest(name=data.get("name", ""), password=data.get("password", "")), timeout=3.0)
                return _resp(request_id, out.status, {"buyer_id": int(out.buyer_id)})
            if api == "CreateSeller":
                out = stub.CreateSeller(pb.CreateSellerRequest(name=data.get("name", ""), password=data.get("password", "")), timeout=3.0)
                return _resp(request_id, out.status, {"seller_id": int(out.seller_id)})
            if api == "Login":
                out = stub.Login(
                    pb.LoginRequest(
                        role=data.get("role", ""),
                        name=data.get("name", ""),
                        password=data.get("password", ""),
                    ),
                    timeout=3.0,
                )
                return _resp(
                    request_id,
                    out.status,
                    {
                        "session_id": out.session_id,
                        "user_id": int(out.user_id),
                        "role": out.role,
                    },
                )
            if api == "Logout":
                out = stub.Logout(pb.LogoutRequest(session_id=data.get("session_id", "")), timeout=3.0)
                return _resp(request_id, out.status, {"logged_out": bool(out.logged_out)})
            if api == "ValidateSession":
                out = stub.ValidateSession(pb.ValidateSessionRequest(session_id=data.get("session_id", "")), timeout=3.0)
                return _resp(request_id, out.status, {"role": out.role, "user_id": int(out.user_id)})
            if api == "GetSellerRating":
                out = stub.GetSellerRating(pb.GetSellerRatingRequest(seller_id=int(data.get("seller_id", 0))), timeout=3.0)
                return _resp(
                    request_id,
                    out.status,
                    {
                        "seller_id": int(out.seller_id),
                        "feedback": {
                            "up": int(out.feedback.up),
                            "down": int(out.feedback.down),
                        },
                    },
                )
            if api == "GetBuyerPurchases":
                out = stub.GetBuyerPurchases(pb.GetBuyerPurchasesRequest(buyer_id=int(data.get("buyer_id", 0))), timeout=3.0)
                return _resp(
                    request_id,
                    out.status,
                    {
                        "buyer_id": int(out.buyer_id),
                        "purchases_count": int(out.purchases_count),
                    },
                )
            if api == "GetCart":
                metadata = None
                if data.get("session_id"):
                    metadata = (("session-id", str(data.get("session_id"))),)
                out = stub.GetCart(
                    pb.GetCartRequest(
                        buyer_id=int(data.get("buyer_id", 0)),
                    ),
                    metadata=metadata,
                    timeout=3.0,
                )
                cart = {entry.item_id: int(entry.quantity) for entry in out.cart}
                return _resp(request_id, out.status, {"cart": cart})
            if api == "UpdateCart":
                metadata = None
                if data.get("session_id"):
                    metadata = (("session-id", str(data.get("session_id"))),)
                out = stub.UpdateCart(
                    pb.UpdateCartRequest(
                        buyer_id=int(data.get("buyer_id", 0)),
                        item_id=data.get("item_id", ""),
                        quantity_delta=int(data.get("quantity_delta", 0)),
                    ),
                    metadata=metadata,
                    timeout=3.0,
                )
                return _resp(request_id, out.status, {"item_id": out.item_id, "quantity": int(out.quantity)})
            if api == "ClearCart":
                metadata = None
                if data.get("session_id"):
                    metadata = (("session-id", str(data.get("session_id"))),)
                out = stub.ClearCart(
                    pb.ClearCartRequest(
                        buyer_id=int(data.get("buyer_id", 0)),
                    ),
                    metadata=metadata,
                    timeout=3.0,
                )
                return _resp(request_id, out.status, {"cleared": bool(out.cleared)})
            if api == "SaveCart":
                metadata = None
                if data.get("session_id"):
                    metadata = (("session-id", str(data.get("session_id"))),)
                out = stub.SaveCart(
                    pb.SaveCartRequest(
                        buyer_id=int(data.get("buyer_id", 0)),
                    ),
                    metadata=metadata,
                    timeout=3.0,
                )
                return _resp(request_id, out.status, {"saved": bool(out.saved)})
            if api == "ClearAndSaveCart":
                metadata = None
                if data.get("session_id"):
                    metadata = (("session-id", str(data.get("session_id"))),)
                out = stub.ClearAndSaveCart(
                    pb.ClearAndSaveCartRequest(
                        buyer_id=int(data.get("buyer_id", 0)),
                    ),
                    metadata=metadata,
                    timeout=3.0,
                )
                return _resp(request_id, out.status, {"cleared": bool(out.cleared)})
            return {
                "type": "Response",
                "request_id": request_id,
                "ok": False,
                "error": {"code": "UNIMPLEMENTED", "message": f"unknown customer api {api}"},
                "data": None,
            }
        except grpc.RpcError as exc:
            return _rpc_error_response(request_id, exc)

    def call(self, api: str, data: Dict[str, Any], request_id: Any) -> Dict[str, Any]:
        last_error: Dict[str, Any] | None = None
        for endpoint in self._ordered_endpoints():
            resp = self._call_once(endpoint, api, data, request_id)
            if resp.get("ok"):
                self._record_success(endpoint)
                return resp

            err = resp.get("error") or {}
            code = str(err.get("code", ""))
            if code == "UNAVAILABLE":
                last_error = resp
                continue

            self._record_success(endpoint)
            return resp

        return last_error or {
            "type": "Response",
            "request_id": request_id,
            "ok": False,
            "error": {"code": "UNAVAILABLE", "message": "no reachable customer DB replica"},
            "data": None,
        }


class ProductDBClient(_ReplicaClientBase):
    WRITE_APIS = {"RegisterItem", "ChangeItemPrice", "UpdateUnitsForSale", "ProvideFeedback"}

    def __init__(self, host: str, port: int, members: Sequence[Tuple[str, int]] | None = None):
        endpoints = list(members) if members is not None else [(host, port)]
        super().__init__(endpoints)
        self._stubs: Dict[Tuple[str, int], pb_grpc.ProductDBServiceStub] = {}

    def _stub_for(self, endpoint: Tuple[str, int]) -> pb_grpc.ProductDBServiceStub:
        stub = self._stubs.get(endpoint)
        if stub is None:
            channel = grpc.insecure_channel(f"{endpoint[0]}:{endpoint[1]}")
            stub = pb_grpc.ProductDBServiceStub(channel)
            self._stubs[endpoint] = stub
        return stub

    def _record_leader(self, leader: Tuple[str, int]) -> None:
        self._record_success(leader)

    def _call_once(self, endpoint: Tuple[str, int], api: str, data: Dict[str, Any], request_id: Any) -> Dict[str, Any]:
        stub = self._stub_for(endpoint)

        try:
            if api == "Ping":
                out = stub.Ping(pb.Empty(), timeout=2.0)
                return _resp(request_id, out.status, {"now": float(out.now)})
            if api == "RegisterItem":
                out = stub.RegisterItem(
                    pb.RegisterItemRequest(
                        name=data.get("name", ""),
                        category=int(data.get("category", 0)),
                        keywords=list(data.get("keywords", []) or []),
                        condition=data.get("condition", ""),
                        price=float(data.get("price", 0.0)),
                        quantity=int(data.get("quantity", 0)),
                        seller_id=int(data.get("seller_id", 0)),
                    ),
                    timeout=3.0,
                )
                return _resp(request_id, out.status, {"item_id": out.item_id})
            if api == "ChangeItemPrice":
                out = stub.ChangeItemPrice(
                    pb.ChangeItemPriceRequest(item_id=data.get("item_id", ""), price=float(data.get("price", 0.0))),
                    timeout=3.0,
                )
                return _resp(request_id, out.status, {"item_id": out.item_id, "price": float(out.price)})
            if api == "UpdateUnitsForSale":
                out = stub.UpdateUnitsForSale(
                    pb.UpdateUnitsForSaleRequest(
                        item_id=data.get("item_id", ""),
                        quantity_delta=int(data.get("quantity_delta", 0)),
                    ),
                    timeout=3.0,
                )
                return _resp(request_id, out.status, {"item_id": out.item_id, "quantity": int(out.quantity)})
            if api == "DisplayItemsForSale":
                out = stub.DisplayItemsForSale(pb.DisplayItemsForSaleRequest(seller_id=int(data.get("seller_id", 0))), timeout=3.0)
                items = [_item_to_dict(item) for item in out.items]
                return _resp(request_id, out.status, {"items": items})
            if api == "SearchItems":
                include_category = data.get("category") is not None
                out = stub.SearchItems(
                    pb.SearchItemsRequest(
                        category=int(data.get("category", 0) or 0),
                        include_category=include_category,
                        keywords=list(data.get("keywords", []) or []),
                    ),
                    timeout=3.0,
                )
                items = [_item_to_dict(item) for item in out.items]
                return _resp(request_id, out.status, {"items": items})
            if api == "GetItem":
                out = stub.GetItem(pb.GetItemRequest(item_id=data.get("item_id", "")), timeout=3.0)
                return _resp(request_id, out.status, {"item": _item_to_dict(out.item)})
            if api == "ProvideFeedback":
                out = stub.ProvideFeedback(
                    pb.ProvideFeedbackRequest(item_id=data.get("item_id", ""), vote=data.get("vote", "")),
                    timeout=3.0,
                )
                return _resp(
                    request_id,
                    out.status,
                    {
                        "item_id": out.item_id,
                        "feedback": {
                            "up": int(out.feedback.up),
                            "down": int(out.feedback.down),
                        },
                    },
                )
            if api == "CheckAvailability":
                out = stub.CheckAvailability(
                    pb.CheckAvailabilityRequest(item_id=data.get("item_id", ""), quantity=int(data.get("quantity", 0))),
                    timeout=3.0,
                )
                return _resp(request_id, out.status, {"available": int(out.available), "ok": bool(out.ok)})
            return {
                "type": "Response",
                "request_id": request_id,
                "ok": False,
                "error": {"code": "UNIMPLEMENTED", "message": f"unknown product api {api}"},
                "data": None,
            }
        except grpc.RpcError as exc:
            return _rpc_error_response(request_id, exc)

    def call(self, api: str, data: Dict[str, Any], request_id: Any) -> Dict[str, Any]:
        is_write = api in self.WRITE_APIS
        endpoints = self._ordered_endpoints()
        last_error: Dict[str, Any] | None = None

        for endpoint in endpoints:
            resp = self._call_once(endpoint, api, data, request_id)

            if resp.get("ok"):
                self._record_leader(endpoint)
                return resp

            err = resp.get("error") or {}
            code = str(err.get("code", ""))
            message = str(err.get("message", ""))

            if code == "NOT_LEADER":
                leader = _parse_leader_from_message(message)
                if leader is not None:
                    self._record_leader(leader)
                    redirected = self._call_once(leader, api, data, request_id)
                    if redirected.get("ok"):
                        return redirected
                    last_error = redirected
                    continue
                last_error = resp
                continue

            if code in {"UNAVAILABLE", "RAFT_NOT_READY"}:
                last_error = resp
                continue

            if is_write:
                return resp

            self._record_leader(endpoint)
            return resp

        return last_error or {
            "type": "Response",
            "request_id": request_id,
            "ok": False,
            "error": {"code": "UNAVAILABLE", "message": "no reachable product DB replica"},
            "data": None,
        }
