import argparse
import os
import sys
import time
from typing import Callable, List, Sequence, Tuple

import grpc

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from common.grpc_gen import marketplace_pb2 as pb
from common.grpc_gen import marketplace_pb2_grpc as pb_grpc


WRITE_RETRY_CODES = {"NOT_LEADER", "RAFT_NOT_READY", "UNAVAILABLE"}


def _status_ok(status: pb.Status) -> bool:
    return bool(status.ok)


def _status_message(status: pb.Status) -> str:
    if status.ok:
        return "ok"
    if status.error.message:
        return f"{status.error.code}: {status.error.message}"
    return status.error.code or "unknown error"


def _mk_stubs(ports: Sequence[int]) -> List[Tuple[grpc.Channel, pb_grpc.ProductDBServiceStub]]:
    out: List[Tuple[grpc.Channel, pb_grpc.ProductDBServiceStub]] = []
    for port in ports:
        channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        out.append((channel, pb_grpc.ProductDBServiceStub(channel)))
    return out


def _await_health(stubs: Sequence[Tuple[grpc.Channel, pb_grpc.ProductDBServiceStub]], timeout_sec: float) -> None:
    deadline = time.time() + timeout_sec
    remaining = set(range(len(stubs)))
    while remaining and time.time() < deadline:
        for idx in list(remaining):
            _channel, stub = stubs[idx]
            try:
                resp = stub.Ping(pb.Empty(), timeout=1.0)
                if _status_ok(resp.status):
                    remaining.remove(idx)
            except grpc.RpcError:
                pass
        if remaining:
            time.sleep(0.5)
    if remaining:
        raise RuntimeError(f"product DB replicas did not become healthy: {sorted(remaining)}")


def _write_via_leader(
    stubs: Sequence[Tuple[grpc.Channel, pb_grpc.ProductDBServiceStub]],
    invoke: Callable[[pb_grpc.ProductDBServiceStub], object],
    status_of: Callable[[object], pb.Status],
    timeout_sec: float = 20.0,
):
    deadline = time.time() + timeout_sec
    last_error: str | None = None

    while time.time() < deadline:
        for idx, (_channel, stub) in enumerate(stubs):
            try:
                resp = invoke(stub)
            except grpc.RpcError as exc:
                last_error = f"replica {idx} rpc error: {exc}"
                continue

            status = status_of(resp)
            if _status_ok(status):
                return resp

            code = str(status.error.code)
            message = _status_message(status)
            last_error = f"replica {idx}: {message}"
            if code in WRITE_RETRY_CODES:
                continue
            raise RuntimeError(f"write failed permanently on replica {idx}: {message}")

        time.sleep(0.5)

    raise RuntimeError(f"could not find writable product DB leader before timeout; last error: {last_error}")


def _register_item_check(
    stubs: Sequence[Tuple[grpc.Channel, pb_grpc.ProductDBServiceStub]]
) -> Tuple[str, int]:
    seller_id = 9001
    item_name = f"raft_book_{int(time.time())}"
    request = pb.RegisterItemRequest(
        name=item_name,
        category=1,
        keywords=["book", "raft"],
        condition="new",
        price=12.5,
        quantity=7,
        seller_id=seller_id,
    )
    resp = _write_via_leader(stubs, lambda stub: stub.RegisterItem(request, timeout=5.0), lambda out: out.status)
    item_id = str(resp.item_id)
    if not item_id:
        raise RuntimeError("RegisterItem succeeded but returned an empty item_id")

    time.sleep(1.0)

    for idx, (_channel, stub) in enumerate(stubs):
        item_resp = stub.GetItem(pb.GetItemRequest(item_id=item_id), timeout=5.0)
        if not _status_ok(item_resp.status):
            raise RuntimeError(f"Replica {idx} GetItem failed after RegisterItem: {_status_message(item_resp.status)}")
        item = item_resp.item
        if str(item.name) != item_name:
            raise RuntimeError(f"Replica {idx} has wrong item name: expected {item_name}, got {item.name}")
        if int(item.quantity) != 7 or abs(float(item.price) - 12.5) > 1e-9:
            raise RuntimeError(f"Replica {idx} has wrong item state after RegisterItem")

    print(f"register replication ok: item_id={item_id} visible on all replicas")
    return item_id, seller_id


def _quantity_and_feedback_check(
    stubs: Sequence[Tuple[grpc.Channel, pb_grpc.ProductDBServiceStub]],
    item_id: str,
) -> None:
    update_resp = _write_via_leader(
        stubs,
        lambda stub: stub.UpdateUnitsForSale(
            pb.UpdateUnitsForSaleRequest(item_id=item_id, quantity_delta=5),
            timeout=5.0,
        ),
        lambda out: out.status,
    )
    if int(update_resp.quantity) != 12:
        raise RuntimeError(f"unexpected quantity after leader update: {update_resp.quantity}")

    feedback_resp = _write_via_leader(
        stubs,
        lambda stub: stub.ProvideFeedback(
            pb.ProvideFeedbackRequest(item_id=item_id, vote="up"),
            timeout=5.0,
        ),
        lambda out: out.status,
    )
    if int(feedback_resp.feedback.up) != 1:
        raise RuntimeError("unexpected feedback count after ProvideFeedback")

    time.sleep(1.0)

    for idx, (_channel, stub) in enumerate(stubs):
        item_resp = stub.GetItem(pb.GetItemRequest(item_id=item_id), timeout=5.0)
        if not _status_ok(item_resp.status):
            raise RuntimeError(f"Replica {idx} GetItem failed after updates: {_status_message(item_resp.status)}")
        item = item_resp.item
        if int(item.quantity) != 12:
            raise RuntimeError(f"Replica {idx} quantity mismatch after UpdateUnitsForSale: {item.quantity}")
        if int(item.feedback.up) != 1 or int(item.feedback.down) != 0:
            raise RuntimeError(
                f"Replica {idx} feedback mismatch after ProvideFeedback: up={item.feedback.up} down={item.feedback.down}"
            )

        avail = stub.CheckAvailability(pb.CheckAvailabilityRequest(item_id=item_id, quantity=12), timeout=5.0)
        if not _status_ok(avail.status) or not bool(avail.ok) or int(avail.available) != 12:
            raise RuntimeError(f"Replica {idx} CheckAvailability mismatch after quantity update")

    print("quantity and feedback replication ok: all replicas converged")


def _search_and_display_check(
    stubs: Sequence[Tuple[grpc.Channel, pb_grpc.ProductDBServiceStub]],
    item_id: str,
    seller_id: int,
) -> None:
    for idx, (_channel, stub) in enumerate(stubs):
        display = stub.DisplayItemsForSale(pb.DisplayItemsForSaleRequest(seller_id=seller_id), timeout=5.0)
        if not _status_ok(display.status):
            raise RuntimeError(f"Replica {idx} DisplayItemsForSale failed: {_status_message(display.status)}")
        display_ids = {str(item.item_id) for item in display.items}
        if item_id not in display_ids:
            raise RuntimeError(f"Replica {idx} seller listing does not contain {item_id}")

        search = stub.SearchItems(
            pb.SearchItemsRequest(category=1, include_category=True, keywords=["raft"]),
            timeout=5.0,
        )
        if not _status_ok(search.status):
            raise RuntimeError(f"Replica {idx} SearchItems failed: {_status_message(search.status)}")
        search_ids = {str(item.item_id) for item in search.items}
        if item_id not in search_ids:
            raise RuntimeError(f"Replica {idx} search results do not contain {item_id}")

    print("search/display replication ok: reads are consistent across replicas")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ports",
        default="6201,6202,6203,6204,6205",
        help="Comma-separated list of exposed gRPC ports for the 5 product DB replicas",
    )
    parser.add_argument("--health-timeout", type=float, default=30.0)
    args = parser.parse_args()

    ports = [int(part.strip()) for part in args.ports.split(",") if part.strip()]
    if len(ports) != 5:
        raise ValueError("--ports must contain exactly 5 entries")

    stubs = _mk_stubs(ports)
    try:
        _await_health(stubs, args.health_timeout)
        item_id, seller_id = _register_item_check(stubs)
        _quantity_and_feedback_check(stubs, item_id)
        _search_and_display_check(stubs, item_id, seller_id)
    finally:
        for channel, _stub in stubs:
            channel.close()

    print("product DB replication checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
