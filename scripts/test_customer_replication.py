import argparse
import os
import sys
import time
from typing import List, Sequence, Tuple

import grpc

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from common.grpc_gen import marketplace_pb2 as pb
from common.grpc_gen import marketplace_pb2_grpc as pb_grpc


def _status_ok(status: pb.Status) -> bool:
    return bool(status.ok)


def _status_message(status: pb.Status) -> str:
    if status.ok:
        return "ok"
    if status.error.message:
        return f"{status.error.code}: {status.error.message}"
    return status.error.code or "unknown error"


def _mk_stubs(ports: Sequence[int]) -> List[Tuple[grpc.Channel, pb_grpc.CustomerDBServiceStub]]:
    out: List[Tuple[grpc.Channel, pb_grpc.CustomerDBServiceStub]] = []
    for port in ports:
        channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        out.append((channel, pb_grpc.CustomerDBServiceStub(channel)))
    return out


def _await_health(stubs: Sequence[Tuple[grpc.Channel, pb_grpc.CustomerDBServiceStub]], timeout_sec: float) -> None:
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
        raise RuntimeError(f"customer DB replicas did not become healthy: {sorted(remaining)}")


def _login_replication_check(
    stubs: Sequence[Tuple[grpc.Channel, pb_grpc.CustomerDBServiceStub]]
) -> Tuple[int, str]:
    proposer_idx = 0
    proposer = stubs[proposer_idx][1]

    buyer_name = f"buyer_repl_{int(time.time())}"
    password = "pass"

    create = proposer.CreateBuyer(pb.CreateBuyerRequest(name=buyer_name, password=password), timeout=5.0)
    if not _status_ok(create.status):
        raise RuntimeError(f"CreateBuyer failed: {_status_message(create.status)}")
    buyer_id = int(create.buyer_id)

    login = proposer.Login(pb.LoginRequest(role="buyer", name=buyer_name, password=password), timeout=5.0)
    if not _status_ok(login.status):
        raise RuntimeError(f"Login failed: {_status_message(login.status)}")

    session_id = str(login.session_id)
    if not session_id:
        raise RuntimeError("Login returned an empty session_id")

    time.sleep(1.0)

    for idx, (_channel, stub) in enumerate(stubs):
        valid = stub.ValidateSession(pb.ValidateSessionRequest(session_id=session_id), timeout=5.0)
        if not _status_ok(valid.status):
            raise RuntimeError(
                f"Replica {idx} does not recognize replicated session {session_id}: {_status_message(valid.status)}"
            )
        if str(valid.role) != "buyer" or int(valid.user_id) != buyer_id:
            raise RuntimeError(f"Replica {idx} returned wrong session metadata for {session_id}")

    print(f"login replication ok: session_id={session_id} visible on all replicas")
    return buyer_id, session_id


def _cart_replication_check(
    stubs: Sequence[Tuple[grpc.Channel, pb_grpc.CustomerDBServiceStub]],
    buyer_id: int,
    session_id: str,
) -> None:
    proposer = stubs[0][1]

    update = proposer.UpdateCart(
        pb.UpdateCartRequest(buyer_id=buyer_id, item_id="demo-item", quantity_delta=3),
        metadata=(("session-id", session_id),),
        timeout=5.0,
    )
    if not _status_ok(update.status):
        raise RuntimeError(f"UpdateCart failed: {_status_message(update.status)}")

    save = proposer.SaveCart(
        pb.SaveCartRequest(buyer_id=buyer_id),
        metadata=(("session-id", session_id),),
        timeout=5.0,
    )
    if not _status_ok(save.status):
        raise RuntimeError(f"SaveCart failed: {_status_message(save.status)}")

    time.sleep(1.0)

    for idx, (_channel, stub) in enumerate(stubs):
        cart = stub.GetCart(
            pb.GetCartRequest(buyer_id=buyer_id),
            metadata=(("session-id", session_id),),
            timeout=5.0,
        )
        if not _status_ok(cart.status):
            raise RuntimeError(f"Replica {idx} GetCart failed: {_status_message(cart.status)}")
        actual = {str(entry.item_id): int(entry.quantity) for entry in cart.cart}
        expected = {"demo-item": 3}
        if actual != expected:
            raise RuntimeError(f"Replica {idx} cart mismatch: expected {expected}, got {actual}")

    print("cart replication ok: saved cart visible on all replicas")


def _logout_replication_check(
    stubs: Sequence[Tuple[grpc.Channel, pb_grpc.CustomerDBServiceStub]],
    session_id: str,
) -> None:
    proposer = stubs[0][1]
    logout = proposer.Logout(pb.LogoutRequest(session_id=session_id), timeout=5.0)
    if not _status_ok(logout.status):
        raise RuntimeError(f"Logout failed: {_status_message(logout.status)}")

    time.sleep(1.0)

    for idx, (_channel, stub) in enumerate(stubs):
        valid = stub.ValidateSession(pb.ValidateSessionRequest(session_id=session_id), timeout=5.0)
        if _status_ok(valid.status):
            raise RuntimeError(f"Replica {idx} still thinks session {session_id} is valid after Logout")
        if str(valid.status.error.code) not in {"NOT_LOGGED_IN", "SESSION_TIMEOUT"}:
            raise RuntimeError(
                f"Replica {idx} returned unexpected error after Logout: {_status_message(valid.status)}"
            )

    print("logout replication ok: session removed on all replicas")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ports",
        default="6101,6102,6103,6104,6105",
        help="Comma-separated list of exposed gRPC ports for the 5 customer DB replicas",
    )
    parser.add_argument("--health-timeout", type=float, default=30.0)
    args = parser.parse_args()

    ports = [int(part.strip()) for part in args.ports.split(",") if part.strip()]
    if len(ports) != 5:
        raise ValueError("--ports must contain exactly 5 entries")

    stubs = _mk_stubs(ports)
    try:
        _await_health(stubs, args.health_timeout)
        buyer_id, session_id = _login_replication_check(stubs)
        _cart_replication_check(stubs, buyer_id, session_id)
        _logout_replication_check(stubs, session_id)
    finally:
        for channel, _stub in stubs:
            channel.close()

    print("customer DB replication checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
