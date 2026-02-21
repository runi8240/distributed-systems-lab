# Benchmark scripts

`run_scenarios.py` drives PA2 REST frontends to measure:
- Average response time (per API call)
- Average throughput (ops/second)

The script runs the required three scenarios:
- Scenario 1: 1 buyer + 1 seller
- Scenario 2: 10 buyers + 10 sellers
- Scenario 3: 100 buyers + 100 sellers

Defaults match PA2 requirements: `--runs 10 --ops-per-client 1000`.

## Prereqs

- All server components deployed and running
- Buyer frontend reachable at `http://<buyer-host>:6003`
- Seller frontend reachable at `http://<seller-host>:6004`

## Run all scenarios (required setup)

```bash
python3 scripts/bench/run_scenarios.py \
  --buyer-host <BUYER_VM_PUBLIC_IP> \
  --seller-host <SELLER_VM_PUBLIC_IP>
```

## Run selected scenarios

```bash
python3 scripts/bench/run_scenarios.py \
  --buyer-host <BUYER_VM_PUBLIC_IP> \
  --seller-host <SELLER_VM_PUBLIC_IP> \
  --scenario 1 --scenario 3
```

## Custom run count and ops count

```bash
python3 scripts/bench/run_scenarios.py \
  --buyer-host <BUYER_VM_PUBLIC_IP> \
  --seller-host <SELLER_VM_PUBLIC_IP> \
  --runs 10 \
  --ops-per-client 1000
```

## Outputs

The script prints scenario summaries and writes:
- `scripts/bench/results/pa2_metrics_<timestamp>.json`
- `scripts/bench/results/pa2_metrics_<timestamp>.md`

The generated Markdown includes:
- experiment setup summary
- result table ready to paste into `REPORT.md`
- analysis prompts for PA1 vs PA2 comparison text
