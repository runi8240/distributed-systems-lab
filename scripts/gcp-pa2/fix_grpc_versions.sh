#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${1:-tonal-env-488105-j5}"
ZONE="${ZONE:-us-central1-a}"
GRPC_VERSION="${GRPC_VERSION:-1.78.1}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

restart_for_vm() {
  case "$1" in
    pa2-db-customer) echo "pa2-db-customer" ;;
    pa2-db-product) echo "pa2-db-product" ;;
    pa2-server-buyer) echo "pa2-server-buyer" ;;
    pa2-server-seller) echo "pa2-server-seller" ;;
    pa2-soap-financial) echo "pa2-soap-financial" ;;
    *) echo "" ;;
  esac
}

require_cmd gcloud

gcloud config set project "$PROJECT_ID" >/dev/null

VMS=(
  "pa2-db-customer"
  "pa2-db-product"
  "pa2-server-buyer"
  "pa2-server-seller"
  "pa2-soap-financial"
)

for vm in "${VMS[@]}"; do
  service="$(restart_for_vm "$vm")"
  echo "Updating grpc on $vm..."
  gcloud compute ssh "$vm" \
    --project "$PROJECT_ID" \
    --zone "$ZONE" \
    --command "
      set -euo pipefail
      ~/pa2-app/.venv/bin/pip install --upgrade 'grpcio==${GRPC_VERSION}' 'grpcio-tools==${GRPC_VERSION}'
      if [ -n '${service}' ]; then
        sudo systemctl restart '${service}'
        sudo systemctl status '${service}' --no-pager --lines=20
      fi
    "
done

echo "Done. grpc packages pinned to ${GRPC_VERSION} on all PA2 VMs."
