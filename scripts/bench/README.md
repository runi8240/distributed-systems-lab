# Benchmark scripts

These scripts drive the buyer/seller frontend APIs to measure response time and throughput.

Prereqs:
- Start the four servers: `db_customer`, `db_product`, `server_buyer`, `server_seller`
- Ensure the buyer/seller frontends are reachable on the host/ports you pass in

Run all three scenarios (1, 10, 100 buyers/sellers), 10 runs each:
```bash
python3 scripts/bench/run_scenarios.py
```

Custom ports or runs:
```bash
python3 scripts/bench/run_scenarios.py --buyer-host 127.0.0.1 --buyer-port 6003 --seller-host 127.0.0.1 --seller-port 6004 --runs 10 --ops-per-client 1000
```

Output (per scenario):
- average response time (seconds per API call)
- average throughput (ops/second)
