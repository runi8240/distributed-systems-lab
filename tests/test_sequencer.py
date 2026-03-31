import multiprocessing as mp
import queue
import socket
import time
import unittest
from typing import Any, Dict, List, Sequence, Tuple

from common.rotating_sequencer import RotatingSequencerAtomicBroadcast


HOST = "127.0.0.1"
MESSAGES_PER_NODE = 10
READY_TIMEOUT_SEC = 10.0
RESULT_TIMEOUT_SEC = 25.0
DELIVERY_TIMEOUT_SEC = 20.0


def _reserve_ports(count: int) -> List[int]:
    sockets: List[socket.socket] = []
    ports: List[int] = []
    try:
        for _ in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind((HOST, 0))
                sockets.append(sock)
                ports.append(int(sock.getsockname()[1]))
            except Exception:
                sock.close()
                raise
    finally:
        for sock in sockets:
            sock.close()
    return ports


def _worker(
    node_id: int,
    members: Sequence[Tuple[str, int]],
    start_event,
    results: mp.Queue,
    messages_per_node: int,
    delivery_timeout_sec: float,
) -> None:
    delivered: List[Tuple[int, int, int, Any]] = []
    total_messages = len(members) * messages_per_node

    def on_deliver(data: Any, metadata: Dict[str, Any]) -> None:
        delivered.append(
            (
                int(metadata["global_seq"]),
                int(metadata["origin_id"]),
                int(metadata["local_seq"]),
                data,
            )
        )

    sequencer = None
    try:
        sequencer = RotatingSequencerAtomicBroadcast(
            node_id=node_id,
            members=members,
            on_deliver=on_deliver,
        )
        results.put({"type": "ready", "node_id": node_id})

        if not start_event.wait(timeout=READY_TIMEOUT_SEC):
            raise TimeoutError(f"node {node_id} timed out waiting for start signal")

        for local_seq in range(messages_per_node):
            payload = {
                "origin_id": node_id,
                "local_seq": local_seq,
                "body": f"node-{node_id}-message-{local_seq}",
            }
            sequencer.broadcast(payload)
            time.sleep(0.01 * ((node_id + local_seq) % 3))

        deadline = time.time() + delivery_timeout_sec
        while time.time() < deadline and len(delivered) < total_messages:
            time.sleep(0.05)

        if len(delivered) != total_messages:
            raise AssertionError(
                f"node {node_id} delivered {len(delivered)} messages, expected {total_messages}"
            )

        delivered_sorted = sorted(delivered, key=lambda item: item[0])
        if delivered_sorted != delivered:
            raise AssertionError(f"node {node_id} delivered messages out of global sequence order")

        request_order = [(origin_id, local_seq) for _, origin_id, local_seq, _ in delivered]
        results.put(
            {
                "type": "result",
                "node_id": node_id,
                "delivered": request_order,
                "delivered_payloads": [payload for _, _, _, payload in delivered],
            }
        )
    except Exception as exc:
        results.put({"type": "error", "node_id": node_id, "error": repr(exc)})
    finally:
        if sequencer is not None:
            sequencer.close()


def _run_cluster(node_count: int, *, messages_per_node: int = MESSAGES_PER_NODE) -> Dict[int, List[Tuple[int, int]]]:
    ctx = mp.get_context("spawn")
    ports = _reserve_ports(node_count)
    members = [(HOST, port) for port in ports]
    start_event = ctx.Event()
    results: mp.Queue = ctx.Queue()

    processes = [
        ctx.Process(
            target=_worker,
            args=(node_id, members, start_event, results, messages_per_node, DELIVERY_TIMEOUT_SEC),
            daemon=False,
        )
        for node_id in range(node_count)
    ]

    for proc in processes:
        proc.start()

    ready_nodes = set()
    start_deadline = time.time() + READY_TIMEOUT_SEC
    while len(ready_nodes) < node_count and time.time() < start_deadline:
        timeout = max(0.1, start_deadline - time.time())
        try:
            item = results.get(timeout=timeout)
        except queue.Empty:
            continue
        if item["type"] == "ready":
            ready_nodes.add(int(item["node_id"]))
            continue
        if item["type"] == "error":
            raise AssertionError(f"worker {item['node_id']} failed before start: {item['error']}")
        raise AssertionError(f"unexpected pre-start message: {item}")

    if len(ready_nodes) != node_count:
        raise AssertionError(f"only {len(ready_nodes)} of {node_count} workers became ready")

    start_event.set()

    final_results: Dict[int, List[Tuple[int, int]]] = {}
    result_deadline = time.time() + RESULT_TIMEOUT_SEC
    while len(final_results) < node_count and time.time() < result_deadline:
        timeout = max(0.1, result_deadline - time.time())
        try:
            item = results.get(timeout=timeout)
        except queue.Empty:
            continue
        if item["type"] == "result":
            final_results[int(item["node_id"])] = list(item["delivered"])
            continue
        if item["type"] == "error":
            raise AssertionError(f"worker {item['node_id']} failed: {item['error']}")
        if item["type"] == "ready":
            continue
        raise AssertionError(f"unexpected result message: {item}")

    for proc in processes:
        proc.join(timeout=5.0)

    alive = [proc.pid for proc in processes if proc.is_alive()]
    if alive:
        for proc in processes:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=1.0)
        raise AssertionError(f"worker processes did not exit cleanly: {alive}")

    bad_exitcodes = {proc.pid: proc.exitcode for proc in processes if proc.exitcode not in (0, None)}
    if bad_exitcodes:
        raise AssertionError(f"worker processes exited with non-zero status: {bad_exitcodes}")

    if len(final_results) != node_count:
        raise AssertionError(f"only received {len(final_results)} of {node_count} final results")

    expected_order = next(iter(final_results.values()))
    expected_set = {(node_id, local_seq) for node_id in range(node_count) for local_seq in range(messages_per_node)}

    if set(expected_order) != expected_set:
        raise AssertionError("reference delivery order does not contain the expected request IDs exactly once")

    for node_id, delivered in final_results.items():
        if delivered != expected_order:
            raise AssertionError(
                f"node {node_id} delivered a different order than the reference replica"
            )

    return final_results


class RotatingSequencerProcessTest(unittest.TestCase):
    def test_five_node_total_order(self) -> None:
        final_results = _run_cluster(5)
        self.assertEqual(len(final_results), 5)
        for delivered in final_results.values():
            self.assertEqual(len(delivered), 50)

    def test_three_node_total_order(self) -> None:
        final_results = _run_cluster(3)
        self.assertEqual(len(final_results), 3)
        for delivered in final_results.values():
            self.assertEqual(len(delivered), 30)


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    unittest.main(verbosity=2)
