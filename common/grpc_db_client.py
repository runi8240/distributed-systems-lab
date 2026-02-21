import os
import sys
from typing import Any, Dict

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


class CustomerDBClient:
    def __init__(self, host: str, port: int):
        self._channel = grpc.insecure_channel(f"{host}:{port}")
        self._stub = pb_grpc.CustomerDBServiceStub(self._channel)

    def call(self, api: str, data: Dict[str, Any], request_id: Any) -> Dict[str, Any]:
        try:
            if api == "Ping":
                out = self._stub.Ping(pb.Empty())
                return _resp(request_id, out.status, {"now": float(out.now)})
            if api == "CreateBuyer":
                out = self._stub.CreateBuyer(pb.CreateBuyerRequest(name=data.get("name", ""), password=data.get("password", "")))
                return _resp(request_id, out.status, {"buyer_id": int(out.buyer_id)})
            if api == "CreateSeller":
                out = self._stub.CreateSeller(pb.CreateSellerRequest(name=data.get("name", ""), password=data.get("password", "")))
                return _resp(request_id, out.status, {"seller_id": int(out.seller_id)})
            if api == "Login":
                out = self._stub.Login(
                    pb.LoginRequest(
                        role=data.get("role", ""),
                        name=data.get("name", ""),
                        password=data.get("password", ""),
                    )
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
                out = self._stub.Logout(pb.LogoutRequest(session_id=data.get("session_id", "")))
                return _resp(request_id, out.status, {"logged_out": bool(out.logged_out)})
            if api == "ValidateSession":
                out = self._stub.ValidateSession(pb.ValidateSessionRequest(session_id=data.get("session_id", "")))
                return _resp(request_id, out.status, {"role": out.role, "user_id": int(out.user_id)})
            if api == "GetSellerRating":
                out = self._stub.GetSellerRating(pb.GetSellerRatingRequest(seller_id=int(data.get("seller_id", 0))))
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
                out = self._stub.GetBuyerPurchases(pb.GetBuyerPurchasesRequest(buyer_id=int(data.get("buyer_id", 0))))
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
                out = self._stub.GetCart(
                    pb.GetCartRequest(
                        buyer_id=int(data.get("buyer_id", 0)),
                    ),
                    metadata=metadata,
                )
                cart = {entry.item_id: int(entry.quantity) for entry in out.cart}
                return _resp(request_id, out.status, {"cart": cart})
            if api == "UpdateCart":
                metadata = None
                if data.get("session_id"):
                    metadata = (("session-id", str(data.get("session_id"))),)
                out = self._stub.UpdateCart(
                    pb.UpdateCartRequest(
                        buyer_id=int(data.get("buyer_id", 0)),
                        item_id=data.get("item_id", ""),
                        quantity_delta=int(data.get("quantity_delta", 0)),
                    ),
                    metadata=metadata,
                )
                return _resp(request_id, out.status, {"item_id": out.item_id, "quantity": int(out.quantity)})
            if api == "ClearCart":
                metadata = None
                if data.get("session_id"):
                    metadata = (("session-id", str(data.get("session_id"))),)
                out = self._stub.ClearCart(
                    pb.ClearCartRequest(
                        buyer_id=int(data.get("buyer_id", 0)),
                    ),
                    metadata=metadata,
                )
                return _resp(request_id, out.status, {"cleared": bool(out.cleared)})
            if api == "SaveCart":
                metadata = None
                if data.get("session_id"):
                    metadata = (("session-id", str(data.get("session_id"))),)
                out = self._stub.SaveCart(
                    pb.SaveCartRequest(
                        buyer_id=int(data.get("buyer_id", 0)),
                    ),
                    metadata=metadata,
                )
                return _resp(request_id, out.status, {"saved": bool(out.saved)})
            return {
                "type": "Response",
                "request_id": request_id,
                "ok": False,
                "error": {"code": "UNIMPLEMENTED", "message": f"unknown customer api {api}"},
                "data": None,
            }
        except grpc.RpcError as exc:
            return _rpc_error_response(request_id, exc)


class ProductDBClient:
    def __init__(self, host: str, port: int):
        self._channel = grpc.insecure_channel(f"{host}:{port}")
        self._stub = pb_grpc.ProductDBServiceStub(self._channel)

    def call(self, api: str, data: Dict[str, Any], request_id: Any) -> Dict[str, Any]:
        try:
            if api == "Ping":
                out = self._stub.Ping(pb.Empty())
                return _resp(request_id, out.status, {"now": float(out.now)})
            if api == "RegisterItem":
                out = self._stub.RegisterItem(
                    pb.RegisterItemRequest(
                        name=data.get("name", ""),
                        category=int(data.get("category", 0)),
                        keywords=list(data.get("keywords", []) or []),
                        condition=data.get("condition", ""),
                        price=float(data.get("price", 0.0)),
                        quantity=int(data.get("quantity", 0)),
                        seller_id=int(data.get("seller_id", 0)),
                    )
                )
                return _resp(request_id, out.status, {"item_id": out.item_id})
            if api == "ChangeItemPrice":
                out = self._stub.ChangeItemPrice(
                    pb.ChangeItemPriceRequest(item_id=data.get("item_id", ""), price=float(data.get("price", 0.0)))
                )
                return _resp(request_id, out.status, {"item_id": out.item_id, "price": float(out.price)})
            if api == "UpdateUnitsForSale":
                out = self._stub.UpdateUnitsForSale(
                    pb.UpdateUnitsForSaleRequest(
                        item_id=data.get("item_id", ""),
                        quantity_delta=int(data.get("quantity_delta", 0)),
                    )
                )
                return _resp(request_id, out.status, {"item_id": out.item_id, "quantity": int(out.quantity)})
            if api == "DisplayItemsForSale":
                out = self._stub.DisplayItemsForSale(pb.DisplayItemsForSaleRequest(seller_id=int(data.get("seller_id", 0))))
                items = [_item_to_dict(item) for item in out.items]
                return _resp(request_id, out.status, {"items": items})
            if api == "SearchItems":
                include_category = data.get("category") is not None
                out = self._stub.SearchItems(
                    pb.SearchItemsRequest(
                        category=int(data.get("category", 0) or 0),
                        include_category=include_category,
                        keywords=list(data.get("keywords", []) or []),
                    )
                )
                items = [_item_to_dict(item) for item in out.items]
                return _resp(request_id, out.status, {"items": items})
            if api == "GetItem":
                out = self._stub.GetItem(pb.GetItemRequest(item_id=data.get("item_id", "")))
                return _resp(request_id, out.status, {"item": _item_to_dict(out.item)})
            if api == "ProvideFeedback":
                out = self._stub.ProvideFeedback(
                    pb.ProvideFeedbackRequest(item_id=data.get("item_id", ""), vote=data.get("vote", ""))
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
                out = self._stub.CheckAvailability(
                    pb.CheckAvailabilityRequest(item_id=data.get("item_id", ""), quantity=int(data.get("quantity", 0)))
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
