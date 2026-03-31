import json
import socket
import threading
import time
from collections import defaultdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


RequestId = Tuple[int, int]
Address = Tuple[str, int]
DeliveryCallback = Callable[[Any, Dict[str, Any]], None]


class RotatingSequencerAtomicBroadcast:
    """
    UDP-based rotating-sequencer atomic broadcast.

    The class starts its receive and maintenance threads immediately. Payloads
    passed to `broadcast()` must be JSON-serializable.

    Message types:
    - Request
    - Sequence
    - Retransmit

    Delivery callback signature:
    - callback(data, metadata_dict)

    The metadata dict passed to the application includes:
    - global_seq
    - request_id
    - origin_id
    - local_seq
    - majority
    """

    def __init__(
        self,
        node_id: int,
        members: Dict[int, Address] | Sequence[Address],
        on_deliver: DeliveryCallback,
        *,
        bind_address: Optional[Address] = None,
        heartbeat_interval: float = 0.20,
        nack_retry_interval: float = 0.30,
        maintenance_interval: float = 0.05,
        socket_timeout: float = 0.10,
    ) -> None:
        self.node_id = int(node_id)
        self.members = self._normalize_members(members)
        self.group_size = len(self.members)
        self.majority = (self.group_size // 2) + 1
        self.on_deliver = on_deliver

        if self.node_id not in self.members:
            raise ValueError(f"node_id {self.node_id} is not present in members")

        self.heartbeat_interval = float(heartbeat_interval)
        self.nack_retry_interval = float(nack_retry_interval)
        self.maintenance_interval = float(maintenance_interval)
        self.socket_timeout = float(socket_timeout)

        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._running = True

        self._local_request_seq = 0
        self._arrival_counter = 0

        self._requests: Dict[RequestId, Dict[str, Any]] = {}
        self._request_messages: Dict[RequestId, Dict[str, Any]] = {}
        self._requests_by_sender: Dict[int, Dict[int, RequestId]] = defaultdict(dict)

        self._request_high_watermark: Dict[int, int] = {member_id: -1 for member_id in self.members}

        self._assigned_locals_by_sender: Dict[int, set[int]] = defaultdict(set)
        self._assigned_local_watermark: Dict[int, int] = {member_id: -1 for member_id in self.members}

        self._sequences: Dict[int, RequestId] = {}
        self._sequence_messages: Dict[int, Dict[str, Any]] = {}
        self._request_to_sequence: Dict[RequestId, int] = {}

        self._highest_sequence_announced = -1
        self._highest_sequence_received = -1
        self._highest_sequence_delivered = -1

        self._peer_metadata: Dict[int, Dict[str, Any]] = {
            member_id: {
                "highest_sequence_announced": -1,
                "highest_sequence_received": -1,
                "highest_sequence_delivered": -1,
                "request_high_watermark": {peer_id: -1 for peer_id in self.members},
                "updated_at": 0.0,
            }
            for member_id in self.members
        }

        self._missing_requests: Dict[RequestId, float] = {}
        self._missing_sequences: Dict[int, float] = {}
        self._last_heartbeat_snapshot: Optional[Tuple[int, int, int, Tuple[Tuple[int, int], ...]]] = None
        self._last_heartbeat_at = 0.0

        bind_host, bind_port = bind_address or self.members[self.node_id]
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((bind_host, bind_port))
        self._sock.settimeout(self.socket_timeout)

        self._refresh_self_metadata_locked()

        self._recv_thread = threading.Thread(target=self._recv_loop, name=f"rsab-recv-{self.node_id}", daemon=True)
        self._maint_thread = threading.Thread(target=self._maintenance_loop, name=f"rsab-maint-{self.node_id}", daemon=True)
        self._recv_thread.start()
        self._maint_thread.start()

    def close(self) -> None:
        with self._wake:
            if not self._running:
                return
            self._running = False
            self._wake.notify_all()
        try:
            self._sock.close()
        except OSError:
            pass
        self._recv_thread.join(timeout=1.0)
        self._maint_thread.join(timeout=1.0)

    def __enter__(self) -> "RotatingSequencerAtomicBroadcast":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    @property
    def highest_sequence_announced(self) -> int:
        with self._lock:
            return self._highest_sequence_announced

    @property
    def highest_sequence_received(self) -> int:
        with self._lock:
            return self._highest_sequence_received

    @property
    def highest_sequence_delivered(self) -> int:
        with self._lock:
            return self._highest_sequence_delivered

    def metadata_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self._make_metadata_locked()

    def broadcast(self, data: Any) -> RequestId:
        with self._wake:
            self._ensure_running_locked()
            request_id = (self.node_id, self._local_request_seq)
            self._local_request_seq += 1

            request_message = {
                "type": "Request",
                "sender_id": self.node_id,
                "request_id": [request_id[0], request_id[1]],
                "data": data,
                "meta": None,
            }
            self._store_request_locked(request_message, arrival_time=time.time())
            request_message["meta"] = self._make_metadata_locked()
            self._request_messages[request_id] = self._clone_message(request_message)
            self._send_to_all_locked(request_message, include_self=False)
            self._wake.notify_all()
            return request_id

    def _normalize_members(self, members: Dict[int, Address] | Sequence[Address]) -> Dict[int, Address]:
        if isinstance(members, dict):
            normalized = {int(member_id): (str(host), int(port)) for member_id, (host, port) in members.items()}
        else:
            normalized = {idx: (str(host), int(port)) for idx, (host, port) in enumerate(members)}
        expected = set(range(len(normalized)))
        if set(normalized) != expected:
            raise ValueError("member IDs must be contiguous and start at 0")
        return normalized

    def _ensure_running_locked(self) -> None:
        if not self._running:
            raise RuntimeError("rotating sequencer is closed")

    def _recv_loop(self) -> None:
        while True:
            try:
                payload, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                with self._lock:
                    if not self._running:
                        return
                continue
            except OSError:
                return

            try:
                message = json.loads(payload.decode("utf-8"))
            except Exception:
                continue

            with self._wake:
                if not self._running:
                    return
                self._handle_incoming_locked(message, addr)
                self._wake.notify_all()

    def _maintenance_loop(self) -> None:
        while True:
            deliveries: List[Tuple[Any, Dict[str, Any]]] = []
            with self._wake:
                if not self._running:
                    return

                self._maybe_assign_sequences_locked()
                self._retry_missing_locked()
                self._maybe_send_heartbeat_locked(force=False)
                deliveries = self._collect_deliveries_locked()

                if not deliveries:
                    self._wake.wait(timeout=self.maintenance_interval)

            for data, metadata in deliveries:
                try:
                    self.on_deliver(data, metadata)
                except Exception:
                    pass

    def _handle_incoming_locked(self, message: Dict[str, Any], addr: Address) -> None:
        msg_type = str(message.get("type", ""))
        sender_id = message.get("sender_id")
        if sender_id is None:
            return
        sender_id = int(sender_id)
        self._update_peer_metadata_locked(sender_id, message.get("meta"))

        if msg_type == "Request":
            self._handle_request_locked(message)
        elif msg_type == "Sequence":
            self._handle_sequence_locked(message)
        elif msg_type == "Retransmit":
            self._handle_retransmit_locked(message, addr)

        self._reconcile_against_peer_metadata_locked(sender_id)
        self._maybe_assign_sequences_locked()
        self._maybe_send_heartbeat_locked(force=False)

    def _handle_request_locked(self, message: Dict[str, Any]) -> None:
        request_id = self._parse_request_id(message.get("request_id"))
        if request_id is None:
            return

        sender_id, local_seq = request_id
        expected = self._request_high_watermark.get(sender_id, -1) + 1
        if local_seq > expected:
            self._mark_missing_request_range_locked(sender_id, expected, local_seq - 1)

        self._store_request_locked(message, arrival_time=time.time())

    def _handle_sequence_locked(self, message: Dict[str, Any]) -> None:
        request_id = self._parse_request_id(message.get("request_id"))
        global_seq = message.get("global_seq")
        sender_id = message.get("sender_id")
        if request_id is None or global_seq is None or sender_id is None:
            return

        global_seq = int(global_seq)
        sender_id = int(sender_id)
        if sender_id != (global_seq % self.group_size):
            return

        expected = self._highest_sequence_announced + 1
        if global_seq > expected:
            self._mark_missing_sequence_range_locked(expected, global_seq - 1)

        if global_seq not in self._sequences:
            self._sequences[global_seq] = request_id
            self._sequence_messages[global_seq] = self._clone_message(message)
            self._request_to_sequence[request_id] = global_seq
            self._assigned_locals_by_sender[request_id[0]].add(request_id[1])
            self._missing_sequences.pop(global_seq, None)

        if request_id not in self._requests:
            self._mark_missing_request_range_locked(request_id[0], request_id[1], request_id[1])

        self._recompute_watermarks_locked()

    def _handle_retransmit_locked(self, message: Dict[str, Any], addr: Address) -> None:
        requester_id = int(message.get("sender_id", -1))
        missing_requests = message.get("missing_requests") or []
        missing_sequences = message.get("missing_sequences") or []

        for request_id_raw in missing_requests:
            request_id = self._parse_request_id(request_id_raw)
            if request_id is None:
                continue
            cached = self._request_messages.get(request_id)
            if cached is None:
                continue
            resend = self._clone_message(cached)
            resend["meta"] = self._make_metadata_locked()
            self._send_locked(requester_id, resend)

        for seq_raw in missing_sequences:
            try:
                global_seq = int(seq_raw)
            except (TypeError, ValueError):
                continue
            cached = self._sequence_messages.get(global_seq)
            if cached is None:
                continue
            resend = self._clone_message(cached)
            resend["meta"] = self._make_metadata_locked()
            self._send_locked(requester_id, resend)

        if not missing_requests and not missing_sequences:
            return

        if requester_id in self.members:
            self._peer_metadata[requester_id]["updated_at"] = time.time()

    def _store_request_locked(self, message: Dict[str, Any], *, arrival_time: float) -> None:
        request_id = self._parse_request_id(message.get("request_id"))
        if request_id is None:
            return

        sender_id, local_seq = request_id
        if request_id not in self._requests:
            self._arrival_counter += 1
            self._requests[request_id] = {
                "request_id": request_id,
                "sender_id": sender_id,
                "local_seq": local_seq,
                "data": message.get("data"),
                "arrival_order": self._arrival_counter,
                "arrival_time": float(arrival_time),
            }
            self._requests_by_sender[sender_id][local_seq] = request_id

        self._request_messages[request_id] = self._clone_message(
            {
                "type": "Request",
                "sender_id": sender_id,
                "request_id": [sender_id, local_seq],
                "data": self._requests[request_id]["data"],
                "meta": self._make_metadata_locked(),
            }
        )
        self._missing_requests.pop(request_id, None)
        self._recompute_watermarks_locked()

    def _recompute_watermarks_locked(self) -> None:
        for sender_id in self.members:
            watermark = self._request_high_watermark.get(sender_id, -1)
            by_sender = self._requests_by_sender.get(sender_id, {})
            while (watermark + 1) in by_sender:
                watermark += 1
            self._request_high_watermark[sender_id] = watermark

        for sender_id in self.members:
            watermark = self._assigned_local_watermark.get(sender_id, -1)
            assigned = self._assigned_locals_by_sender.get(sender_id, set())
            while (watermark + 1) in assigned:
                watermark += 1
            self._assigned_local_watermark[sender_id] = watermark

        announced = self._highest_sequence_announced
        while (announced + 1) in self._sequences:
            announced += 1
        self._highest_sequence_announced = announced

        received = self._highest_sequence_received
        while True:
            next_seq = received + 1
            request_id = self._sequences.get(next_seq)
            if request_id is None or request_id not in self._requests:
                break
            received = next_seq
        self._highest_sequence_received = received

        self._refresh_self_metadata_locked()

    def _refresh_self_metadata_locked(self) -> None:
        self._peer_metadata[self.node_id] = {
            "highest_sequence_announced": self._highest_sequence_announced,
            "highest_sequence_received": self._highest_sequence_received,
            "highest_sequence_delivered": self._highest_sequence_delivered,
            "request_high_watermark": dict(self._request_high_watermark),
            "updated_at": time.time(),
        }

    def _maybe_assign_sequences_locked(self) -> None:
        while True:
            next_global_seq = self._highest_sequence_received + 1
            if (next_global_seq % self.group_size) != self.node_id:
                return

            request_id = self._select_pending_request_locked()
            if request_id is None:
                return

            message = {
                "type": "Sequence",
                "sender_id": self.node_id,
                "global_seq": next_global_seq,
                "request_id": [request_id[0], request_id[1]],
                "meta": None,
            }
            self._sequences[next_global_seq] = request_id
            self._sequence_messages[next_global_seq] = self._clone_message(message)
            self._request_to_sequence[request_id] = next_global_seq
            self._assigned_locals_by_sender[request_id[0]].add(request_id[1])
            self._recompute_watermarks_locked()
            message["meta"] = self._make_metadata_locked()
            self._sequence_messages[next_global_seq] = self._clone_message(message)
            self._send_to_all_locked(message, include_self=False)

    def _select_pending_request_locked(self) -> Optional[RequestId]:
        best: Optional[Tuple[int, int, int, RequestId]] = None
        for request_id, entry in self._requests.items():
            if request_id in self._request_to_sequence:
                continue
            sender_id, local_seq = request_id
            if local_seq != (self._assigned_local_watermark.get(sender_id, -1) + 1):
                continue
            candidate = (int(entry["arrival_order"]), sender_id, local_seq, request_id)
            if best is None or candidate < best:
                best = candidate
        return best[3] if best is not None else None

    def _collect_deliveries_locked(self) -> List[Tuple[Any, Dict[str, Any]]]:
        deliveries: List[Tuple[Any, Dict[str, Any]]] = []
        while True:
            next_seq = self._highest_sequence_delivered + 1
            request_id = self._sequences.get(next_seq)
            if request_id is None:
                break
            entry = self._requests.get(request_id)
            if entry is None:
                break
            if not self._majority_has_received_locked(next_seq):
                break

            self._highest_sequence_delivered = next_seq
            self._refresh_self_metadata_locked()
            deliveries.append(
                (
                    entry["data"],
                    {
                        "global_seq": next_seq,
                        "request_id": request_id,
                        "origin_id": request_id[0],
                        "local_seq": request_id[1],
                        "majority": self.majority,
                    },
                )
            )

        if deliveries:
            self._maybe_send_heartbeat_locked(force=True)
        return deliveries

    def _majority_has_received_locked(self, global_seq: int) -> bool:
        count = 0
        for member_id in self.members:
            peer_meta = self._peer_metadata.get(member_id)
            if peer_meta is None:
                continue
            if int(peer_meta.get("highest_sequence_received", -1)) >= global_seq:
                count += 1
        return count >= self.majority

    def _update_peer_metadata_locked(self, sender_id: int, meta: Any) -> None:
        if sender_id not in self.members or not isinstance(meta, dict):
            return

        request_watermark = {member_id: -1 for member_id in self.members}
        raw = meta.get("request_high_watermark") or {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                try:
                    request_watermark[int(key)] = int(value)
                except (TypeError, ValueError):
                    continue

        self._peer_metadata[sender_id] = {
            "highest_sequence_announced": int(meta.get("highest_sequence_announced", -1)),
            "highest_sequence_received": int(meta.get("highest_sequence_received", -1)),
            "highest_sequence_delivered": int(meta.get("highest_sequence_delivered", -1)),
            "request_high_watermark": request_watermark,
            "updated_at": time.time(),
        }

    def _reconcile_against_peer_metadata_locked(self, peer_id: int) -> None:
        peer_meta = self._peer_metadata.get(peer_id)
        if peer_meta is None:
            return

        peer_announced = int(peer_meta.get("highest_sequence_announced", -1))
        if peer_announced > self._highest_sequence_announced:
            self._mark_missing_sequence_range_locked(self._highest_sequence_announced + 1, peer_announced)

        peer_request_watermarks = peer_meta.get("request_high_watermark") or {}
        for sender_id in self.members:
            peer_wm = int(peer_request_watermarks.get(sender_id, -1))
            local_wm = self._request_high_watermark.get(sender_id, -1)
            if peer_wm > local_wm:
                self._mark_missing_request_range_locked(sender_id, local_wm + 1, peer_wm)

        peer_received = int(peer_meta.get("highest_sequence_received", -1))
        limit = min(peer_received, self._highest_sequence_announced)
        for global_seq in range(0, limit + 1):
            request_id = self._sequences.get(global_seq)
            if request_id is not None and request_id not in self._requests:
                self._mark_missing_request_range_locked(request_id[0], request_id[1], request_id[1])

    def _mark_missing_request_range_locked(self, sender_id: int, start_local_seq: int, end_local_seq: int) -> None:
        if sender_id not in self.members:
            return
        if end_local_seq < start_local_seq:
            return
        for local_seq in range(int(start_local_seq), int(end_local_seq) + 1):
            request_id = (sender_id, local_seq)
            if request_id in self._requests:
                continue
            self._missing_requests.setdefault(request_id, 0.0)

    def _mark_missing_sequence_range_locked(self, start_seq: int, end_seq: int) -> None:
        if end_seq < start_seq:
            return
        for global_seq in range(int(start_seq), int(end_seq) + 1):
            if global_seq in self._sequences:
                continue
            self._missing_sequences.setdefault(global_seq, 0.0)

    def _retry_missing_locked(self) -> None:
        now = time.time()
        request_groups: Dict[int, List[List[int]]] = defaultdict(list)
        for request_id, last_sent in list(self._missing_requests.items()):
            if request_id in self._requests:
                self._missing_requests.pop(request_id, None)
                continue
            if now - last_sent < self.nack_retry_interval:
                continue
            request_groups[request_id[0]].append([request_id[0], request_id[1]])
            self._missing_requests[request_id] = now

        for sender_id, missing_requests in request_groups.items():
            message = {
                "type": "Retransmit",
                "sender_id": self.node_id,
                "missing_requests": missing_requests,
                "missing_sequences": [],
                "meta": self._make_metadata_locked(),
            }
            self._send_locked(sender_id, message)

        sequence_groups: Dict[int, List[int]] = defaultdict(list)
        for global_seq, last_sent in list(self._missing_sequences.items()):
            if global_seq in self._sequences:
                self._missing_sequences.pop(global_seq, None)
                continue
            if now - last_sent < self.nack_retry_interval:
                continue
            target_id = global_seq % self.group_size
            sequence_groups[target_id].append(global_seq)
            self._missing_sequences[global_seq] = now

        for target_id, missing_sequences in sequence_groups.items():
            message = {
                "type": "Retransmit",
                "sender_id": self.node_id,
                "missing_requests": [],
                "missing_sequences": missing_sequences,
                "meta": self._make_metadata_locked(),
            }
            self._send_locked(target_id, message)

    def _maybe_send_heartbeat_locked(self, *, force: bool) -> None:
        now = time.time()
        snapshot = self._heartbeat_snapshot_locked()
        if not force:
            if snapshot == self._last_heartbeat_snapshot and (now - self._last_heartbeat_at) < self.heartbeat_interval:
                return
            if (now - self._last_heartbeat_at) < self.heartbeat_interval:
                return

        heartbeat = {
            "type": "Retransmit",
            "sender_id": self.node_id,
            "missing_requests": [],
            "missing_sequences": [],
            "meta": self._make_metadata_locked(),
        }
        self._send_to_all_locked(heartbeat, include_self=False)
        self._last_heartbeat_snapshot = snapshot
        self._last_heartbeat_at = now

    def _heartbeat_snapshot_locked(self) -> Tuple[int, int, int, Tuple[Tuple[int, int], ...]]:
        return (
            self._highest_sequence_announced,
            self._highest_sequence_received,
            self._highest_sequence_delivered,
            tuple(sorted(self._request_high_watermark.items())),
        )

    def _make_metadata_locked(self) -> Dict[str, Any]:
        return {
            "highest_sequence_announced": self._highest_sequence_announced,
            "highest_sequence_received": self._highest_sequence_received,
            "highest_sequence_delivered": self._highest_sequence_delivered,
            "request_high_watermark": {str(sender_id): int(value) for sender_id, value in self._request_high_watermark.items()},
        }

    def _send_to_all_locked(self, message: Dict[str, Any], *, include_self: bool) -> None:
        for member_id in self.members:
            if not include_self and member_id == self.node_id:
                continue
            self._send_locked(member_id, message)

    def _send_locked(self, target_id: int, message: Dict[str, Any]) -> None:
        target = self.members.get(target_id)
        if target is None:
            return
        try:
            encoded = json.dumps(message, separators=(",", ":")).encode("utf-8")
            self._sock.sendto(encoded, target)
        except OSError:
            pass

    def _parse_request_id(self, raw: Any) -> Optional[RequestId]:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return None
        try:
            return int(raw[0]), int(raw[1])
        except (TypeError, ValueError):
            return None

    def _clone_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        return json.loads(json.dumps(message))

