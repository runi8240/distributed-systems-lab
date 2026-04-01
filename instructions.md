# PA3 Benchmark Instructions

This file documents how to restart the deployed PA3 services on GCP and run the Scenario 1 benchmarks with `5` operations per client.

Assumptions:
- GCP project: `pa3-demo`
- zone: `us-central1-a`
- VMs:
  - `pa3-vm-a`
  - `pa3-vm-b`
  - `pa3-vm-c`
  - `pa3-vm-d`
- Public frontend IPs:
  - buyer1 / seller1 on `34.30.160.147`
  - buyer2 / seller2 on `34.45.95.133`
  - buyer3 / seller3 on `35.222.98.184`
  - buyer4 / seller4 on `34.170.76.88`
- Internal VM IPs:
  - `pa3-vm-a` = `10.128.0.5`
  - `pa3-vm-b` = `10.128.0.6`
  - `pa3-vm-c` = `10.128.0.7`
  - `pa3-vm-d` = `10.128.0.8`

## Replica Layout

- Customer DB
  - product/customer are separate systems
  - customer0 on `pa3-vm-a:6101`
  - customer1 on `pa3-vm-b:6102`
  - customer2 on `pa3-vm-c:6103`
  - customer3 on `pa3-vm-d:6104`
  - customer4 on `pa3-vm-a:6105`
- Product DB
  - product0 on `pa3-vm-a:6201`
  - product1 on `pa3-vm-b:6202`
  - product2 on `pa3-vm-c:6203`
  - product3 on `pa3-vm-d:6204`
  - product4 on `pa3-vm-b:6205`
- Buyer frontends
  - buyer1 on `pa3-vm-a:6301`
  - buyer2 on `pa3-vm-b:6302`
  - buyer3 on `pa3-vm-c:6303`
  - buyer4 on `pa3-vm-d:6304`
- Seller frontends
  - seller1 on `pa3-vm-a:6401`
  - seller2 on `pa3-vm-b:6402`
  - seller3 on `pa3-vm-c:6403`
  - seller4 on `pa3-vm-d:6404`
- SOAP service
  - `pa3-vm-c:8008`

## Benchmark Command Template

All Scenario 1 runs in this document use:
- `--scenario 1`
- `--runs 1`
- `--ops-per-client 5`

Base command:

```bash
python3 scripts/bench/run_scenarios.py \
  --buyer-replicas "34.30.160.147:6301,34.45.95.133:6302,35.222.98.184:6303,34.170.76.88:6304" \
  --seller-replicas "34.30.160.147:6401,34.45.95.133:6402,35.222.98.184:6403,34.170.76.88:6404" \
  --scenario 1 \
  --failure-mode normal \
  --runs 1 \
  --ops-per-client 5
```

## Restart Everything

Use this when the system looks wedged or unusually slow.

### 1. Restart SOAP on VM C

```bash
gcloud compute ssh pa3-vm-c --zone us-central1-a
```

Then run:

```bash
pkill -f "soap/financial_transactions_service.py" || true
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 soap/financial_transactions_service.py \
  --host 0.0.0.0 \
  --port 8008 \
  > ~/pa3-logs/soap.log 2>&1 < /dev/null &
exit
```

### 2. Restart Product DB Replicas

#### VM A: product0

```bash
gcloud compute ssh pa3-vm-a --zone us-central1-a
```

```bash
pkill -f "db_product/product_server.py --host 0.0.0.0 --port 6201" || true
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 db_product/product_server.py \
  --host 0.0.0.0 \
  --port 6201 \
  --state ~/pa3-data/product0.db \
  --node-id 0 \
  --members "10.128.0.5:7200,10.128.0.6:7201,10.128.0.7:7202,10.128.0.8:7203,10.128.0.6:7204" \
  > ~/pa3-logs/product0.log 2>&1 < /dev/null &
exit
```

#### VM B: product1 and product4

```bash
gcloud compute ssh pa3-vm-b --zone us-central1-a
```

```bash
pkill -f "db_product/product_server.py --host 0.0.0.0 --port 6202" || true
pkill -f "db_product/product_server.py --host 0.0.0.0 --port 6205" || true
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 db_product/product_server.py \
  --host 0.0.0.0 \
  --port 6202 \
  --state ~/pa3-data/product1.db \
  --node-id 1 \
  --members "10.128.0.5:7200,10.128.0.6:7201,10.128.0.7:7202,10.128.0.8:7203,10.128.0.6:7204" \
  > ~/pa3-logs/product1.log 2>&1 < /dev/null &

nohup ~/pa3-app/.venv/bin/python3 db_product/product_server.py \
  --host 0.0.0.0 \
  --port 6205 \
  --state ~/pa3-data/product4.db \
  --node-id 4 \
  --members "10.128.0.5:7200,10.128.0.6:7201,10.128.0.7:7202,10.128.0.8:7203,10.128.0.6:7204" \
  > ~/pa3-logs/product4.log 2>&1 < /dev/null &
exit
```

#### VM C: product2

```bash
gcloud compute ssh pa3-vm-c --zone us-central1-a
```

```bash
pkill -f "db_product/product_server.py --host 0.0.0.0 --port 6203" || true
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 db_product/product_server.py \
  --host 0.0.0.0 \
  --port 6203 \
  --state ~/pa3-data/product2.db \
  --node-id 2 \
  --members "10.128.0.5:7200,10.128.0.6:7201,10.128.0.7:7202,10.128.0.8:7203,10.128.0.6:7204" \
  > ~/pa3-logs/product2.log 2>&1 < /dev/null &
exit
```

#### VM D: product3

```bash
gcloud compute ssh pa3-vm-d --zone us-central1-a
```

```bash
pkill -f "db_product/product_server.py --host 0.0.0.0 --port 6204" || true
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 db_product/product_server.py \
  --host 0.0.0.0 \
  --port 6204 \
  --state ~/pa3-data/product3.db \
  --node-id 3 \
  --members "10.128.0.5:7200,10.128.0.6:7201,10.128.0.7:7202,10.128.0.8:7203,10.128.0.6:7204" \
  > ~/pa3-logs/product3.log 2>&1 < /dev/null &
exit
```

### 3. Restart Buyer Frontends

#### buyer1

```bash
gcloud compute ssh pa3-vm-a --zone us-central1-a
```

```bash
pkill -f "server_buyer/buyer_server.py --host 0.0.0.0 --port 6301" || true
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 server_buyer/buyer_server.py \
  --host 0.0.0.0 \
  --port 6301 \
  --customer-members "10.128.0.5:6101,10.128.0.6:6102,10.128.0.7:6103,10.128.0.8:6104,10.128.0.5:6105" \
  --product-members "10.128.0.5:6201,10.128.0.6:6202,10.128.0.7:6203,10.128.0.8:6204,10.128.0.6:6205" \
  --soap-wsdl "http://10.128.0.7:8008/?wsdl" \
  > ~/pa3-logs/buyer1.log 2>&1 < /dev/null &
exit
```

#### buyer2

```bash
gcloud compute ssh pa3-vm-b --zone us-central1-a
```

```bash
pkill -f "server_buyer/buyer_server.py --host 0.0.0.0 --port 6302" || true
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 server_buyer/buyer_server.py \
  --host 0.0.0.0 \
  --port 6302 \
  --customer-members "10.128.0.5:6101,10.128.0.6:6102,10.128.0.7:6103,10.128.0.8:6104,10.128.0.5:6105" \
  --product-members "10.128.0.5:6201,10.128.0.6:6202,10.128.0.7:6203,10.128.0.8:6204,10.128.0.6:6205" \
  --soap-wsdl "http://10.128.0.7:8008/?wsdl" \
  > ~/pa3-logs/buyer2.log 2>&1 < /dev/null &
exit
```

#### buyer3

```bash
gcloud compute ssh pa3-vm-c --zone us-central1-a
```

```bash
pkill -f "server_buyer/buyer_server.py --host 0.0.0.0 --port 6303" || true
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 server_buyer/buyer_server.py \
  --host 0.0.0.0 \
  --port 6303 \
  --customer-members "10.128.0.5:6101,10.128.0.6:6102,10.128.0.7:6103,10.128.0.8:6104,10.128.0.5:6105" \
  --product-members "10.128.0.5:6201,10.128.0.6:6202,10.128.0.7:6203,10.128.0.8:6204,10.128.0.6:6205" \
  --soap-wsdl "http://10.128.0.7:8008/?wsdl" \
  > ~/pa3-logs/buyer3.log 2>&1 < /dev/null &
exit
```

#### buyer4

```bash
gcloud compute ssh pa3-vm-d --zone us-central1-a
```

```bash
pkill -f "server_buyer/buyer_server.py --host 0.0.0.0 --port 6304" || true
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 server_buyer/buyer_server.py \
  --host 0.0.0.0 \
  --port 6304 \
  --customer-members "10.128.0.5:6101,10.128.0.6:6102,10.128.0.7:6103,10.128.0.8:6104,10.128.0.5:6105" \
  --product-members "10.128.0.5:6201,10.128.0.6:6202,10.128.0.7:6203,10.128.0.8:6204,10.128.0.6:6205" \
  --soap-wsdl "http://10.128.0.7:8008/?wsdl" \
  > ~/pa3-logs/buyer4.log 2>&1 < /dev/null &
exit
```

### 4. Restart Seller Frontends

#### seller1

```bash
gcloud compute ssh pa3-vm-a --zone us-central1-a
```

```bash
pkill -f "server_seller/seller_server.py --host 0.0.0.0 --port 6401" || true
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 server_seller/seller_server.py \
  --host 0.0.0.0 \
  --port 6401 \
  --customer-members "10.128.0.5:6101,10.128.0.6:6102,10.128.0.7:6103,10.128.0.8:6104,10.128.0.5:6105" \
  --product-members "10.128.0.5:6201,10.128.0.6:6202,10.128.0.7:6203,10.128.0.8:6204,10.128.0.6:6205" \
  > ~/pa3-logs/seller1.log 2>&1 < /dev/null &
exit
```

#### seller2

```bash
gcloud compute ssh pa3-vm-b --zone us-central1-a
```

```bash
pkill -f "server_seller/seller_server.py --host 0.0.0.0 --port 6402" || true
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 server_seller/seller_server.py \
  --host 0.0.0.0 \
  --port 6402 \
  --customer-members "10.128.0.5:6101,10.128.0.6:6102,10.128.0.7:6103,10.128.0.8:6104,10.128.0.5:6105" \
  --product-members "10.128.0.5:6201,10.128.0.6:6202,10.128.0.7:6203,10.128.0.8:6204,10.128.0.6:6205" \
  > ~/pa3-logs/seller2.log 2>&1 < /dev/null &
exit
```

#### seller3

```bash
gcloud compute ssh pa3-vm-c --zone us-central1-a
```

```bash
pkill -f "server_seller/seller_server.py --host 0.0.0.0 --port 6403" || true
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 server_seller/seller_server.py \
  --host 0.0.0.0 \
  --port 6403 \
  --customer-members "10.128.0.5:6101,10.128.0.6:6102,10.128.0.7:6103,10.128.0.8:6104,10.128.0.5:6105" \
  --product-members "10.128.0.5:6201,10.128.0.6:6202,10.128.0.7:6203,10.128.0.8:6204,10.128.0.6:6205" \
  > ~/pa3-logs/seller3.log 2>&1 < /dev/null &
exit
```

#### seller4

```bash
gcloud compute ssh pa3-vm-d --zone us-central1-a
```

```bash
pkill -f "server_seller/seller_server.py --host 0.0.0.0 --port 6404" || true
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 server_seller/seller_server.py \
  --host 0.0.0.0 \
  --port 6404 \
  --customer-members "10.128.0.5:6101,10.128.0.6:6102,10.128.0.7:6103,10.128.0.8:6104,10.128.0.5:6105" \
  --product-members "10.128.0.5:6201,10.128.0.6:6202,10.128.0.7:6203,10.128.0.8:6204,10.128.0.6:6205" \
  > ~/pa3-logs/seller4.log 2>&1 < /dev/null &
exit
```

## Verify Frontend Health

```bash
curl -s --max-time 5 http://34.30.160.147:6301/health
curl -s --max-time 5 http://34.45.95.133:6302/health
curl -s --max-time 5 http://35.222.98.184:6303/health
curl -s --max-time 5 http://34.170.76.88:6304/health

curl -s --max-time 5 http://34.30.160.147:6401/health
curl -s --max-time 5 http://34.45.95.133:6402/health
curl -s --max-time 5 http://35.222.98.184:6403/health
curl -s --max-time 5 http://34.170.76.88:6404/health
```

## Check Which Product Raft Replica Is Leader

The product replicas expose `/raft-status` on internal ports:
- product0: `9201`
- product1: `9202`
- product2: `9203`
- product3: `9204`
- product4: `9205`

Run from inside GCP, for example from `pa3-vm-a`:

```bash
gcloud compute ssh pa3-vm-a --zone us-central1-a --command '
curl --max-time 5 http://10.128.0.5:9201/raft-status; echo
curl --max-time 5 http://10.128.0.6:9202/raft-status; echo
curl --max-time 5 http://10.128.0.7:9203/raft-status; echo
curl --max-time 5 http://10.128.0.8:9204/raft-status; echo
curl --max-time 5 http://10.128.0.6:9205/raft-status; echo
'
```

Look for the JSON line with:
- `"is_leader": true`

Example:
- if product0 returns `"is_leader": true`, then the leader is:
  - node ID `0`
  - gRPC `10.128.0.5:6201`
  - Raft `10.128.0.5:7200`

## Force a Different Leader

Raft does not let you directly choose a leader, but you can force a re-election by killing the current leader.

Example: if product0 is current leader, kill it:

```bash
gcloud compute ssh pa3-vm-a --zone us-central1-a
```

Then run:

```bash
pkill -f "db_product/product_server.py --host 0.0.0.0 --port 6201" || true
exit
```

Wait `10-15` seconds, then check `/raft-status` again on the remaining replicas. One follower should now have:
- `"is_leader": true`

To bring the old leader back:

```bash
gcloud compute ssh pa3-vm-a --zone us-central1-a
```

```bash
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 db_product/product_server.py \
  --host 0.0.0.0 \
  --port 6201 \
  --state ~/pa3-data/product0.db \
  --node-id 0 \
  --members "10.128.0.5:7200,10.128.0.6:7201,10.128.0.7:7202,10.128.0.8:7203,10.128.0.6:7204" \
  > ~/pa3-logs/product0.log 2>&1 < /dev/null &
exit
```

## Scenario 1, Normal

Run:

```bash
python3 scripts/bench/run_scenarios.py \
  --buyer-replicas "34.30.160.147:6301,34.45.95.133:6302,35.222.98.184:6303,34.170.76.88:6304" \
  --seller-replicas "34.30.160.147:6401,34.45.95.133:6402,35.222.98.184:6403,34.170.76.88:6404" \
  --scenario 1 \
  --failure-mode normal \
  --runs 1 \
  --ops-per-client 5
```

## Scenario 1, Frontend Failure

Goal:
- one buyer frontend replica fails
- one seller frontend replica fails
- clients fail over to the remaining frontends

Recommended failure pair:
- buyer1 on `34.30.160.147:6301`
- seller2 on `34.45.95.133:6402`

Stop buyer1:

```bash
gcloud compute ssh pa3-vm-a --zone us-central1-a
```

```bash
pkill -f "server_buyer/buyer_server.py --host 0.0.0.0 --port 6301" || true
exit
```

Stop seller2:

```bash
gcloud compute ssh pa3-vm-b --zone us-central1-a
```

```bash
pkill -f "server_seller/seller_server.py --host 0.0.0.0 --port 6402" || true
exit
```

Run the benchmark:

```bash
python3 scripts/bench/run_scenarios.py \
  --buyer-replicas "34.30.160.147:6301,34.45.95.133:6302,35.222.98.184:6303,34.170.76.88:6304" \
  --seller-replicas "34.30.160.147:6401,34.45.95.133:6402,35.222.98.184:6403,34.170.76.88:6404" \
  --scenario 1 \
  --failure-mode normal \
  --runs 1 \
  --ops-per-client 5
```

Then restart buyer1 and seller2 using the restart commands above.

## Scenario 1, Product Follower Failure

Goal:
- kill a product replica that is not the leader
- measure client API latency while the cluster keeps operating

Example:
- if product0 is leader, kill product2 on `pa3-vm-c:6203`

Stop product2:

```bash
gcloud compute ssh pa3-vm-c --zone us-central1-a
```

```bash
pkill -f "db_product/product_server.py --host 0.0.0.0 --port 6203" || true
exit
```

Run the benchmark:

```bash
python3 scripts/bench/run_scenarios.py \
  --buyer-replicas "34.30.160.147:6301,34.45.95.133:6302,35.222.98.184:6303,34.170.76.88:6304" \
  --seller-replicas "34.30.160.147:6401,34.45.95.133:6402,35.222.98.184:6403,34.170.76.88:6404" \
  --scenario 1 \
  --failure-mode normal \
  --runs 1 \
  --ops-per-client 5
```

Then restart product2:

```bash
gcloud compute ssh pa3-vm-c --zone us-central1-a
```

```bash
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 db_product/product_server.py \
  --host 0.0.0.0 \
  --port 6203 \
  --state ~/pa3-data/product2.db \
  --node-id 2 \
  --members "10.128.0.5:7200,10.128.0.6:7201,10.128.0.7:7202,10.128.0.8:7203,10.128.0.6:7204" \
  > ~/pa3-logs/product2.log 2>&1 < /dev/null &
exit
```

## Scenario 1, Product Leader Failure

Goal:
- kill the current product leader
- let Raft elect a new leader
- measure client API latency during/after failover

Example:
- if product0 is leader, kill product0 on `pa3-vm-a:6201`

Stop product0:

```bash
gcloud compute ssh pa3-vm-a --zone us-central1-a
```

```bash
pkill -f "db_product/product_server.py --host 0.0.0.0 --port 6201" || true
exit
```

Wait `10-15` seconds, then confirm a new leader with `/raft-status`.

Run the benchmark:

```bash
python3 scripts/bench/run_scenarios.py \
  --buyer-replicas "34.30.160.147:6301,34.45.95.133:6302,35.222.98.184:6303,34.170.76.88:6304" \
  --seller-replicas "34.30.160.147:6401,34.45.95.133:6402,35.222.98.184:6403,34.170.76.88:6404" \
  --scenario 1 \
  --failure-mode normal \
  --runs 1 \
  --ops-per-client 5
```

Then restart product0:

```bash
gcloud compute ssh pa3-vm-a --zone us-central1-a
```

```bash
cd ~/pa3-app
nohup ~/pa3-app/.venv/bin/python3 db_product/product_server.py \
  --host 0.0.0.0 \
  --port 6201 \
  --state ~/pa3-data/product0.db \
  --node-id 0 \
  --members "10.128.0.5:7200,10.128.0.6:7201,10.128.0.7:7202,10.128.0.8:7203,10.128.0.6:7204" \
  > ~/pa3-logs/product0.log 2>&1 < /dev/null &
exit
```

## Save Benchmark Output

If you want to save the benchmark output for each run:

```bash
mkdir -p scripts/bench/results scripts/bench/logs
TS="$(date -u +%Y%m%dT%H%M%SZ)"
python3 scripts/bench/run_scenarios.py \
  --buyer-replicas "34.30.160.147:6301,34.45.95.133:6302,35.222.98.184:6303,34.170.76.88:6304" \
  --seller-replicas "34.30.160.147:6401,34.45.95.133:6402,35.222.98.184:6403,34.170.76.88:6404" \
  --scenario 1 \
  --failure-mode normal \
  --runs 1 \
  --ops-per-client 5 \
  --output-dir "scripts/bench/results/run_${TS}" \
  2>&1 | tee "scripts/bench/logs/run_${TS}.log"
```
