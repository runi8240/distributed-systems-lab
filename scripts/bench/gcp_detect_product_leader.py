import argparse
import json
import os
import sys
import urllib.request
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from common.grpc_db_client import ProductDBClient


def _parse_members(value: str) -> List[Tuple[str, int]]:
    members: List[Tuple[str, int]] = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        host, port = raw.rsplit(":", 1)
        members.append((host, int(port)))
    if len(members) != 5:
        raise ValueError("expected exactly 5 members")
    return members


def _status_endpoint(grpc_endpoint: Tuple[str, int]) -> Tuple[str, int]:
    return grpc_endpoint[0], grpc_endpoint[1] + 3000


def _fetch_status(host: str, port: int) -> Dict[str, object]:
    with urllib.request.urlopen(f"http://{host}:{port}/raft-status", timeout=3.0) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grpc-members", required=True)
    parser.add_argument("--raft-members", required=True)
    args = parser.parse_args()

    grpc_members = _parse_members(args.grpc_members)
    raft_members = _parse_members(args.raft_members)

    statuses: List[Dict[str, object]] = []
    for idx, endpoint in enumerate(grpc_members):
        status_host, status_port = _status_endpoint(endpoint)
        try:
            payload = _fetch_status(status_host, status_port)
        except Exception:
            continue
        payload["node_index"] = idx
        payload["status_endpoint"] = f"{status_host}:{status_port}"
        statuses.append(payload)

    for payload in statuses:
        if bool(payload.get("is_leader")):
            idx = int(payload["node_index"])
            result = {
                "leader_id": idx,
                "grpc_endpoint": f"{grpc_members[idx][0]}:{grpc_members[idx][1]}",
                "raft_endpoint": f"{raft_members[idx][0]}:{raft_members[idx][1]}",
                "status_endpoint": str(payload.get("status_endpoint", "")),
                "detected_by": "raft_status",
            }
            print(json.dumps(result))
            return 0

    for payload in statuses:
        leader_addr = str(payload.get("leader_addr", ""))
        if not leader_addr:
            continue
        for leader_idx, raft_endpoint in enumerate(raft_members):
            text = f"{raft_endpoint[0]}:{raft_endpoint[1]}"
            if leader_addr == text:
                result = {
                    "leader_id": leader_idx,
                    "grpc_endpoint": f"{grpc_members[leader_idx][0]}:{grpc_members[leader_idx][1]}",
                    "raft_endpoint": text,
                    "status_endpoint": str(payload.get("status_endpoint", "")),
                    "detected_by": "leader_addr_from_status",
                }
                print(json.dumps(result))
                return 0

    raise SystemExit("could not determine product leader from the configured replicas")


if __name__ == "__main__":
    raise SystemExit(main())
