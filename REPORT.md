### Performance Results

Benchmark source:
- Pilot run with `runs=10` and `ops_per_client=1000` (default parameters) using:
  - `python3 scripts/bench/run_scenarios.py --buyer-host <buyer-ip> --seller-host <seller-ip> `

Measured pilot values:

| Scenario | Buyers + Sellers | Clients | Avg Response Time (s) | Avg Throughput (ops/s) |
|---|---:|---:|---:|---:|
| 1 | 1 + 1 | 2 | 0.098011 | 20.34 |
| 2 | 10 + 10 | 20 | 0.114528 | 171.49 |
| 3 | 100 + 100 | 200 | 0.284379 | 656.96 |


Notes and explanation:
- Response time increases with concurrency, consistent with queueing and resource contention at frontend and backend services.
- Throughput increases from scenario 1 to 3 because total parallelism is much higher, but the growth is sub-linear due to saturation effects.
- Compared with PA1, PA2 is expected to show higher latency and lower per-client efficiency due to REST + gRPC translation and cross-VM network overhead.
