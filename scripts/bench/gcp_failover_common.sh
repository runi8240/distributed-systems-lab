#!/usr/bin/env bash

set -euo pipefail

PA3_GCP_PROJECT="${PA3_GCP_PROJECT:-pa3-demo}"
PA3_GCP_ZONE="${PA3_GCP_ZONE:-us-central1-a}"

PA3_VM_A="${PA3_VM_A:-pa3-vm-a}"
PA3_VM_B="${PA3_VM_B:-pa3-vm-b}"
PA3_VM_C="${PA3_VM_C:-pa3-vm-c}"
PA3_VM_D="${PA3_VM_D:-pa3-vm-d}"

PA3_APP_DIR="${PA3_APP_DIR:-~/pa3-app}"
PA3_DATA_DIR="${PA3_DATA_DIR:-~/pa3-data}"
PA3_LOG_DIR="${PA3_LOG_DIR:-~/pa3-logs}"
PA3_STATE_DIR="${PA3_STATE_DIR:-/tmp/pa3-bench-state}"

mkdir -p "$PA3_STATE_DIR"

GCLOUD_BASE=(gcloud --project "$PA3_GCP_PROJECT")
PA3_GCP_SSH_RETRIES="${PA3_GCP_SSH_RETRIES:-3}"

log() {
  printf '[pa3-failover] %s\n' "$*"
}

run_remote() {
  local vm="$1"
  shift
  local cmd="$*"
  local attempt
  local rc=0

  for attempt in $(seq 1 "$PA3_GCP_SSH_RETRIES"); do
    if "${GCLOUD_BASE[@]}" compute ssh "$vm" --zone "$PA3_GCP_ZONE" --command "$cmd"; then
      return 0
    fi
    rc=$?
    log "plain ssh to ${vm} failed on attempt ${attempt}/${PA3_GCP_SSH_RETRIES} with exit code ${rc}"
    sleep 2
  done

  log "falling back to IAP SSH for ${vm}"
  for attempt in $(seq 1 "$PA3_GCP_SSH_RETRIES"); do
    if "${GCLOUD_BASE[@]}" compute ssh "$vm" --zone "$PA3_GCP_ZONE" --tunnel-through-iap --command "$cmd"; then
      return 0
    fi
    rc=$?
    log "IAP ssh to ${vm} failed on attempt ${attempt}/${PA3_GCP_SSH_RETRIES} with exit code ${rc}"
    sleep 2
  done

  return "$rc"
}

vm_int_ip() {
  local vm="$1"
  "${GCLOUD_BASE[@]}" compute instances describe "$vm" --zone "$PA3_GCP_ZONE" --format='value(networkInterfaces[0].networkIP)'
}

customer_grpc_members() {
  local a b c d
  a="$(vm_int_ip "$PA3_VM_A")"
  b="$(vm_int_ip "$PA3_VM_B")"
  c="$(vm_int_ip "$PA3_VM_C")"
  d="$(vm_int_ip "$PA3_VM_D")"
  printf '%s' "${a}:6101,${b}:6102,${c}:6103,${d}:6104,${a}:6105"
}

product_grpc_members() {
  local a b c d
  a="$(vm_int_ip "$PA3_VM_A")"
  b="$(vm_int_ip "$PA3_VM_B")"
  c="$(vm_int_ip "$PA3_VM_C")"
  d="$(vm_int_ip "$PA3_VM_D")"
  printf '%s' "${a}:6201,${b}:6202,${c}:6203,${d}:6204,${b}:6205"
}

product_raft_members() {
  local a b c d
  a="$(vm_int_ip "$PA3_VM_A")"
  b="$(vm_int_ip "$PA3_VM_B")"
  c="$(vm_int_ip "$PA3_VM_C")"
  d="$(vm_int_ip "$PA3_VM_D")"
  printf '%s' "${a}:7200,${b}:7201,${c}:7202,${d}:7203,${b}:7204"
}

soap_wsdl() {
  local c
  c="$(vm_int_ip "$PA3_VM_C")"
  printf 'http://%s:8008/?wsdl' "$c"
}

product_vm() {
  case "$1" in
    0) printf '%s' "$PA3_VM_A" ;;
    1) printf '%s' "$PA3_VM_B" ;;
    2) printf '%s' "$PA3_VM_C" ;;
    3) printf '%s' "$PA3_VM_D" ;;
    4) printf '%s' "$PA3_VM_B" ;;
    *) log "unknown product replica id: $1"; return 1 ;;
  esac
}

product_grpc_port() {
  case "$1" in
    0) printf '6201' ;;
    1) printf '6202' ;;
    2) printf '6203' ;;
    3) printf '6204' ;;
    4) printf '6205' ;;
    *) log "unknown product replica id: $1"; return 1 ;;
  esac
}

product_log_file() {
  case "$1" in
    0) printf '%s/product0.log' "$PA3_LOG_DIR" ;;
    1) printf '%s/product1.log' "$PA3_LOG_DIR" ;;
    2) printf '%s/product2.log' "$PA3_LOG_DIR" ;;
    3) printf '%s/product3.log' "$PA3_LOG_DIR" ;;
    4) printf '%s/product4.log' "$PA3_LOG_DIR" ;;
    *) log "unknown product replica id: $1"; return 1 ;;
  esac
}

product_state_file() {
  case "$1" in
    0) printf '%s/product0.db' "$PA3_DATA_DIR" ;;
    1) printf '%s/product1.db' "$PA3_DATA_DIR" ;;
    2) printf '%s/product2.db' "$PA3_DATA_DIR" ;;
    3) printf '%s/product3.db' "$PA3_DATA_DIR" ;;
    4) printf '%s/product4.db' "$PA3_DATA_DIR" ;;
    *) log "unknown product replica id: $1"; return 1 ;;
  esac
}

buyer_vm() {
  case "$1" in
    1) printf '%s' "$PA3_VM_A" ;;
    2) printf '%s' "$PA3_VM_B" ;;
    3) printf '%s' "$PA3_VM_C" ;;
    4) printf '%s' "$PA3_VM_D" ;;
    *) log "unknown buyer replica id: $1"; return 1 ;;
  esac
}

seller_vm() {
  case "$1" in
    1) printf '%s' "$PA3_VM_A" ;;
    2) printf '%s' "$PA3_VM_B" ;;
    3) printf '%s' "$PA3_VM_C" ;;
    4) printf '%s' "$PA3_VM_D" ;;
    *) log "unknown seller replica id: $1"; return 1 ;;
  esac
}

buyer_port() {
  printf '630%s' "$1"
}

seller_port() {
  printf '640%s' "$1"
}

buyer_log_file() {
  printf '%s/buyer%s.log' "$PA3_LOG_DIR" "$1"
}

seller_log_file() {
  printf '%s/seller%s.log' "$PA3_LOG_DIR" "$1"
}

stop_product_replica() {
  local replica_id="$1"
  local vm port
  vm="$(product_vm "$replica_id")"
  port="$(product_grpc_port "$replica_id")"
  log "stopping product replica ${replica_id} on ${vm}:${port}"
  run_remote "$vm" "pkill -f 'db_product/product_server.py --host 0.0.0.0 --port ${port}' || true"
}

restart_product_replica() {
  local replica_id="$1"
  local vm port state_file log_file members
  vm="$(product_vm "$replica_id")"
  port="$(product_grpc_port "$replica_id")"
  state_file="$(product_state_file "$replica_id")"
  log_file="$(product_log_file "$replica_id")"
  members="$(product_raft_members)"
  log "restarting product replica ${replica_id} on ${vm}:${port}"
  run_remote "$vm" "pkill -f 'db_product/product_server.py --host 0.0.0.0 --port ${port}' || true; cd ${PA3_APP_DIR}; nohup ${PA3_APP_DIR}/.venv/bin/python3 db_product/product_server.py --host 0.0.0.0 --port ${port} --state ${state_file} --node-id ${replica_id} --members '${members}' > ${log_file} 2>&1 < /dev/null &"
}

stop_buyer_replica() {
  local replica_id="$1"
  local vm port
  vm="$(buyer_vm "$replica_id")"
  port="$(buyer_port "$replica_id")"
  log "stopping buyer replica ${replica_id} on ${vm}:${port}"
  run_remote "$vm" "pkill -f 'server_buyer/buyer_server.py --host 0.0.0.0 --port ${port}' || true"
}

restart_buyer_replica() {
  local replica_id="$1"
  local vm port log_file customer_members product_members wsdl
  vm="$(buyer_vm "$replica_id")"
  port="$(buyer_port "$replica_id")"
  log_file="$(buyer_log_file "$replica_id")"
  customer_members="$(customer_grpc_members)"
  product_members="$(product_grpc_members)"
  wsdl="$(soap_wsdl)"
  log "restarting buyer replica ${replica_id} on ${vm}:${port}"
  run_remote "$vm" "pkill -f 'server_buyer/buyer_server.py --host 0.0.0.0 --port ${port}' || true; cd ${PA3_APP_DIR}; nohup ${PA3_APP_DIR}/.venv/bin/python3 server_buyer/buyer_server.py --host 0.0.0.0 --port ${port} --customer-members '${customer_members}' --product-members '${product_members}' --soap-wsdl '${wsdl}' > ${log_file} 2>&1 < /dev/null &"
}

stop_seller_replica() {
  local replica_id="$1"
  local vm port
  vm="$(seller_vm "$replica_id")"
  port="$(seller_port "$replica_id")"
  log "stopping seller replica ${replica_id} on ${vm}:${port}"
  run_remote "$vm" "pkill -f 'server_seller/seller_server.py --host 0.0.0.0 --port ${port}' || true"
}

restart_seller_replica() {
  local replica_id="$1"
  local vm port log_file customer_members product_members
  vm="$(seller_vm "$replica_id")"
  port="$(seller_port "$replica_id")"
  log_file="$(seller_log_file "$replica_id")"
  customer_members="$(customer_grpc_members)"
  product_members="$(product_grpc_members)"
  log "restarting seller replica ${replica_id} on ${vm}:${port}"
  run_remote "$vm" "pkill -f 'server_seller/seller_server.py --host 0.0.0.0 --port ${port}' || true; cd ${PA3_APP_DIR}; nohup ${PA3_APP_DIR}/.venv/bin/python3 server_seller/seller_server.py --host 0.0.0.0 --port ${port} --customer-members '${customer_members}' --product-members '${product_members}' > ${log_file} 2>&1 < /dev/null &"
}
