#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${1:-tonal-env-488105-j5}"
ZONE="${ZONE:-us-central1-a}"
NETWORK="${NETWORK:-default}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-standard-2}"
IMAGE_FAMILY="${IMAGE_FAMILY:-debian-12}"
IMAGE_PROJECT="${IMAGE_PROJECT:-debian-cloud}"
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

create_firewall_if_missing() {
  local rule_name="$1"
  shift
  if gcloud compute firewall-rules describe "$rule_name" --project "$PROJECT_ID" >/dev/null 2>&1; then
    echo "Firewall rule already exists: $rule_name"
  else
    gcloud compute firewall-rules create "$rule_name" --project "$PROJECT_ID" "$@"
  fi
}

create_vm_if_missing() {
  local name="$1"
  local tags="$2"
  if gcloud compute instances describe "$name" --project "$PROJECT_ID" --zone "$ZONE" >/dev/null 2>&1; then
    echo "VM already exists: $name"
    return
  fi

  gcloud compute instances create "$name" \
    --project "$PROJECT_ID" \
    --zone "$ZONE" \
    --machine-type "$MACHINE_TYPE" \
    --image-family "$IMAGE_FAMILY" \
    --image-project "$IMAGE_PROJECT" \
    --network "$NETWORK" \
    --tags "$tags"
}

print_ips() {
  local vm="$1"
  local internal_ip
  local external_ip
  internal_ip="$(gcloud compute instances describe "$vm" --project "$PROJECT_ID" --zone "$ZONE" --format='value(networkInterfaces[0].networkIP)')"
  external_ip="$(gcloud compute instances describe "$vm" --project "$PROJECT_ID" --zone "$ZONE" --format='value(networkInterfaces[0].accessConfigs[0].natIP)')"
  printf '%-20s internal=%-15s external=%s\n' "$vm" "$internal_ip" "$external_ip"
}

require_cmd gcloud

gcloud config set project "$PROJECT_ID" >/dev/null

echo "Creating firewall rules..."
create_firewall_if_missing "pa2-allow-internal-services" \
  --network "$NETWORK" \
  --direction INGRESS \
  --priority 1000 \
  --action ALLOW \
  --rules tcp:6001-6004,tcp:8008 \
  --source-tags pa2-service \
  --target-tags pa2-service

create_firewall_if_missing "pa2-allow-buyer-public" \
  --network "$NETWORK" \
  --direction INGRESS \
  --priority 1000 \
  --action ALLOW \
  --rules tcp:6003 \
  --source-ranges 0.0.0.0/0 \
  --target-tags pa2-buyer

create_firewall_if_missing "pa2-allow-seller-public" \
  --network "$NETWORK" \
  --direction INGRESS \
  --priority 1000 \
  --action ALLOW \
  --rules tcp:6004 \
  --source-ranges 0.0.0.0/0 \
  --target-tags pa2-seller

echo "Creating VMs..."
create_vm_if_missing "$DB_CUSTOMER_VM" "pa2-service,pa2-db-customer"
create_vm_if_missing "$DB_PRODUCT_VM" "pa2-service,pa2-db-product"
create_vm_if_missing "$SERVER_BUYER_VM" "pa2-service,pa2-buyer"
create_vm_if_missing "$SERVER_SELLER_VM" "pa2-service,pa2-seller"

if [[ "$WITH_SOAP_VM" == "true" ]]; then
  create_vm_if_missing "$SOAP_VM" "pa2-service,pa2-soap"
fi

echo ""
echo "VM IP summary:"
print_ips "$DB_CUSTOMER_VM"
print_ips "$DB_PRODUCT_VM"
print_ips "$SERVER_BUYER_VM"
print_ips "$SERVER_SELLER_VM"
if [[ "$WITH_SOAP_VM" == "true" ]]; then
  print_ips "$SOAP_VM"
fi

echo ""
echo "Next step: run scripts/gcp-pa2/configure_services.sh $PROJECT_ID"
