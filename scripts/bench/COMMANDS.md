# Benchmark scripts

`run_scenarios.py` now targets the PA3 replicated deployment. It measures:
- average response time per client API
- average throughput per client API
- success rate / outcome counts for each API
- normal and failure-mode runs

It supports all three load scenarios:
- Scenario 1: 1 buyer + 1 seller
- Scenario 2: 10 buyers + 10 sellers
- Scenario 3: 100 buyers + 100 sellers

Defaults match the assignment: `--runs 10 --ops-per-client 1000`.

## Prereqs

- Replicated buyer and seller frontends are reachable
- Failure-mode commands are available if you want the PA3 failure cases automated
- Product/customer backends are already deployed and healthy behind the frontends

## Scenario 1, no failures

```bash
python3 scripts/bench/run_scenarios.py \
  --buyer-replicas 34.30.160.147:6301,34.45.95.133:6302,35.222.98.184:6303,34.170.76.88:6304 \
  --seller-replicas 34.30.160.147:6401,34.45.95.133:6402,35.222.98.184:6403,34.170.76.88:6404 \
  --scenario 1 \
  --failure-mode normal
```

## Scenario 1, frontend replica failure

```bash
python3 scripts/bench/run_scenarios.py \
  --buyer-replicas 34.30.160.147:6301,34.45.95.133:6302,35.222.98.184:6303,34.170.76.88:6304 \
  --seller-replicas 34.30.160.147:6401,34.45.95.133:6402,35.222.98.184:6403,34.170.76.88:6404 \
  --scenario 1 \
  --failure-mode frontend_failover \
  --frontend-failure-start-cmd "gcloud compute ssh pa3-vm-a --zone us-central1-a --command 'pkill -f \"server_buyer/buyer_server.py --host 0.0.0.0 --port 6301\" || true'; gcloud compute ssh pa3-vm-b --zone us-central1-a --command 'pkill -f \"server_seller/seller_server.py --host 0.0.0.0 --port 6402\" || true'" \
  --frontend-failure-stop-cmd "<restart the stopped buyer and seller replicas>"
```

## Scenario 1, product follower failure

```bash
python3 scripts/bench/run_scenarios.py \
  --buyer-replicas 34.30.160.147:6301,34.45.95.133:6302,35.222.98.184:6303,34.170.76.88:6304 \
  --seller-replicas 34.30.160.147:6401,34.45.95.133:6402,35.222.98.184:6403,34.170.76.88:6404 \
  --scenario 1 \
  --failure-mode product_follower_fail \
  --product-follower-failure-start-cmd "<stop one known non-leader product replica>" \
  --product-follower-failure-stop-cmd "<restart that product replica>"
```

## Scenario 1, product leader failure

```bash
python3 scripts/bench/run_scenarios.py \
  --buyer-replicas 34.30.160.147:6301,34.45.95.133:6302,35.222.98.184:6303,34.170.76.88:6304 \
  --seller-replicas 34.30.160.147:6401,34.45.95.133:6402,35.222.98.184:6403,34.170.76.88:6404 \
  --scenario 1 \
  --failure-mode product_leader_fail \
  --product-leader-failure-start-cmd "<stop the current product leader replica>" \
  --product-leader-failure-stop-cmd "<restart that product replica>"
```

## Outputs

The script writes:
- `scripts/bench/results/pa3_metrics_<timestamp>.json`
- `scripts/bench/results/pa3_metrics_<timestamp>.md`

The JSON is the full source of record. The Markdown is a condensed table suitable for `REPORT.md` / `README.md`.

## Notes

- The benchmark runs each buyer API and each seller API separately, then reports per-operation metrics.
- `MakePurchase` may return `PAYMENT_DECLINED` because the SOAP banking service is probabilistic; those responses are counted in the output outcome table.
- For scenario 1, each measured operation runs with one relevant client of that role.
