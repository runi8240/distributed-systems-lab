# PA2 GCP VM Deployment (Separate VMs)

This runbook deploys the PA2 components on Google Compute Engine with one service per VM:

- `pa2-db-customer` -> `db_customer` on port `6001`
- `pa2-db-product` -> `db_product` on port `6002`
- `pa2-server-buyer` -> `server_buyer` on port `6003`
- `pa2-server-seller` -> `server_seller` on port `6004`
- `pa2-soap-financial` -> SOAP service on port `8008` (optional but enabled by default)

Your local Mac runs buyer/seller clients and connects to the buyer/seller VM public IPs.

## 1. Prerequisites (local)

- `gcloud` CLI installed and authenticated
- Compute Engine API enabled on project
- SSH keys configured for `gcloud compute ssh`

Set project and (optionally) default zone:

```bash
gcloud config set project tonal-env-488105-j5
gcloud config set compute/zone us-central1-a
```

## 2. Create VMs and firewall rules

From repo root:

```bash
chmod +x scripts/gcp-pa2/create_vms.sh scripts/gcp-pa2/configure_services.sh
scripts/gcp-pa2/create_vms.sh tonal-env-488105-j5
```

Notes:
- Defaults: `ZONE=us-central1-a`, `MACHINE_TYPE=e2-standard-2`
- Override defaults, e.g.:

```bash
ZONE=us-east1-b MACHINE_TYPE=e2-medium scripts/gcp-pa2/create_vms.sh tonal-env-488105-j5
```

If you do not want a separate SOAP VM (SOAP will run on buyer VM), run:

```bash
WITH_SOAP_VM=false scripts/gcp-pa2/create_vms.sh tonal-env-488105-j5
```

## 3. Push code and start services

```bash
scripts/gcp-pa2/configure_services.sh tonal-env-488105-j5
```

This script:
- copies your current repository to each VM
- installs Python + dependencies
- wires frontend services to backend internal VM IPs
- creates and starts `systemd` services

## 4. Verify

Check service status/logs:

```bash
gcloud compute ssh pa2-db-customer --command "sudo systemctl status pa2-db-customer --no-pager"
gcloud compute ssh pa2-db-product --command "sudo systemctl status pa2-db-product --no-pager"
gcloud compute ssh pa2-server-buyer --command "sudo systemctl status pa2-server-buyer --no-pager"
gcloud compute ssh pa2-server-seller --command "sudo systemctl status pa2-server-seller --no-pager"
gcloud compute ssh pa2-soap-financial --command "sudo systemctl status pa2-soap-financial --no-pager"
```

Smoke test from your Mac:

```bash
BUYER_IP=$(gcloud compute instances describe pa2-server-buyer --format='value(networkInterfaces[0].accessConfigs[0].natIP)')
SELLER_IP=$(gcloud compute instances describe pa2-server-seller --format='value(networkInterfaces[0].accessConfigs[0].natIP)')

curl -s http://$BUYER_IP:6003/health
curl -s http://$SELLER_IP:6004/health
```

## 5. Run local clients against cloud frontends

```bash
python3 client_buyer/cli.py --host "$BUYER_IP" --port 6003
python3 client_seller/cli.py --host "$SELLER_IP" --port 6004
```

## 6. Cleanup (optional)

```bash
gcloud compute instances delete -q pa2-db-customer pa2-db-product pa2-server-buyer pa2-server-seller pa2-soap-financial --zone us-central1-a
```
