#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${1:-tonal-env-488105-j5}"
ZONE="${ZONE:-us-central1-a}"
WITH_SOAP_VM="${WITH_SOAP_VM:-true}"

DB_CUSTOMER_VM="pa2-db-customer"
DB_PRODUCT_VM="pa2-db-product"
SERVER_BUYER_VM="pa2-server-buyer"
SERVER_SELLER_VM="pa2-server-seller"
SOAP_VM="pa2-soap-financial"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

instance_exists() {
  gcloud compute instances describe "$1" --project "$PROJECT_ID" --zone "$ZONE" >/dev/null 2>&1
}

vm_internal_ip() {
  gcloud compute instances describe "$1" --project "$PROJECT_ID" --zone "$ZONE" --format='value(networkInterfaces[0].networkIP)'
}

vm_external_ip() {
  gcloud compute instances describe "$1" --project "$PROJECT_ID" --zone "$ZONE" --format='value(networkInterfaces[0].accessConfigs[0].natIP)'
}

bootstrap_vm() {
  local vm="$1"
  local role="$2"

  echo "Configuring $vm ($role)..."
  gcloud compute scp \
    --project "$PROJECT_ID" \
    --zone "$ZONE" \
    "$ARTIFACT" "$vm:~/pa2-src.tgz"

  gcloud compute ssh "$vm" \
    --project "$PROJECT_ID" \
    --zone "$ZONE" \
    --command "ROLE='$role' DB_CUSTOMER_IP='$DB_CUSTOMER_IP' DB_PRODUCT_IP='$DB_PRODUCT_IP' SOAP_IP='$SOAP_IP' bash -s" <<'REMOTE'
set -euo pipefail

sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip tar

mkdir -p "$HOME/pa2-app"
tar -xzf "$HOME/pa2-src.tgz" -C "$HOME/pa2-app"

python3 -m venv "$HOME/pa2-app/.venv"
"$HOME/pa2-app/.venv/bin/pip" install --upgrade pip
"$HOME/pa2-app/.venv/bin/pip" install -r "$HOME/pa2-app/requirements.txt"

case "$ROLE" in
  db_customer)
    RUN_ARGS="db_customer/customer_server.py --host 0.0.0.0 --port 6001 --state /home/$USER/pa2-app/db_customer/state.db"
    SERVICE_NAME="pa2-db-customer"
    ;;
  db_product)
    RUN_ARGS="db_product/product_server.py --host 0.0.0.0 --port 6002 --state /home/$USER/pa2-app/db_product/state.db"
    SERVICE_NAME="pa2-db-product"
    ;;
  server_buyer)
    RUN_ARGS="server_buyer/buyer_server.py --host 0.0.0.0 --port 6003 --customer-host $DB_CUSTOMER_IP --customer-port 6001 --product-host $DB_PRODUCT_IP --product-port 6002 --soap-wsdl http://$SOAP_IP:8008/?wsdl"
    SERVICE_NAME="pa2-server-buyer"
    ;;
  server_seller)
    RUN_ARGS="server_seller/seller_server.py --host 0.0.0.0 --port 6004 --customer-host $DB_CUSTOMER_IP --customer-port 6001 --product-host $DB_PRODUCT_IP --product-port 6002"
    SERVICE_NAME="pa2-server-seller"
    ;;
  soap)
    RUN_ARGS="soap/financial_transactions_service.py --host 0.0.0.0 --port 8008"
    SERVICE_NAME="pa2-soap-financial"
    ;;
  *)
    echo "Unknown ROLE=$ROLE" >&2
    exit 1
    ;;
esac

RUN_SCRIPT="$HOME/pa2-app/run_${SERVICE_NAME}.sh"
cat > "$RUN_SCRIPT" <<EOF_RUN
#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/pa2-app"
exec "$HOME/pa2-app/.venv/bin/python3" $RUN_ARGS
EOF_RUN
chmod +x "$RUN_SCRIPT"

cat <<EOF_UNIT | sudo tee /etc/systemd/system/${SERVICE_NAME}.service >/dev/null
[Unit]
Description=${SERVICE_NAME}
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/home/$USER/pa2-app
Environment=PYTHONUNBUFFERED=1
ExecStart=${RUN_SCRIPT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF_UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}.service"
sudo systemctl status "${SERVICE_NAME}.service" --no-pager --lines=20
REMOTE
}

require_cmd gcloud
require_cmd tar

for vm in "$DB_CUSTOMER_VM" "$DB_PRODUCT_VM" "$SERVER_BUYER_VM" "$SERVER_SELLER_VM"; do
  if ! instance_exists "$vm"; then
    echo "Missing VM: $vm. Run scripts/gcp-pa2/create_vms.sh first." >&2
    exit 1
  fi
done

if [[ "$WITH_SOAP_VM" == "true" ]] && ! instance_exists "$SOAP_VM"; then
  echo "Missing VM: $SOAP_VM. Run scripts/gcp-pa2/create_vms.sh first." >&2
  exit 1
fi

DB_CUSTOMER_IP="$(vm_internal_ip "$DB_CUSTOMER_VM")"
DB_PRODUCT_IP="$(vm_internal_ip "$DB_PRODUCT_VM")"
if [[ "$WITH_SOAP_VM" == "true" ]]; then
  SOAP_IP="$(vm_internal_ip "$SOAP_VM")"
else
  SOAP_IP="$(vm_internal_ip "$SERVER_BUYER_VM")"
fi

ARTIFACT="$(mktemp /tmp/pa2-src.XXXXXX.tgz)"
trap 'rm -f "$ARTIFACT"' EXIT

tar \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  -czf "$ARTIFACT" .

bootstrap_vm "$DB_CUSTOMER_VM" "db_customer"
bootstrap_vm "$DB_PRODUCT_VM" "db_product"

if [[ "$WITH_SOAP_VM" == "true" ]]; then
  bootstrap_vm "$SOAP_VM" "soap"
else
  bootstrap_vm "$SERVER_BUYER_VM" "soap"
fi

bootstrap_vm "$SERVER_BUYER_VM" "server_buyer"
bootstrap_vm "$SERVER_SELLER_VM" "server_seller"

BUYER_PUBLIC_IP="$(vm_external_ip "$SERVER_BUYER_VM")"
SELLER_PUBLIC_IP="$(vm_external_ip "$SERVER_SELLER_VM")"

echo ""
echo "Deployment complete."
echo "Buyer frontend public endpoint: http://${BUYER_PUBLIC_IP}:6003"
echo "Seller frontend public endpoint: http://${SELLER_PUBLIC_IP}:6004"
echo ""
echo "From your Mac, run:"
echo "python3 client_buyer/cli.py --host ${BUYER_PUBLIC_IP} --port 6003"
echo "python3 client_seller/cli.py --host ${SELLER_PUBLIC_IP} --port 6004"
