#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/gcp_failover_common.sh"

STATE_FILE="${PA3_STATE_DIR}/product_follower_fail_replica_id"
PRODUCT_GRPC="$(product_grpc_members)"
PRODUCT_RAFT="$(product_raft_members)"

leader_json="$(python3 "${SCRIPT_DIR}/gcp_detect_product_leader.py" --grpc-members "${PRODUCT_GRPC}" --raft-members "${PRODUCT_RAFT}")"
leader_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["leader_id"])' <<<"${leader_json}")"

target_id=""
for candidate in 0 1 2 3 4; do
  if [[ "${candidate}" != "${leader_id}" ]]; then
    target_id="${candidate}"
    break
  fi
done

if [[ -z "${target_id}" ]]; then
  log "could not select a product follower to stop"
  exit 1
fi

printf '%s\n' "${target_id}" > "${STATE_FILE}"
log "detected product leader replica ${leader_id}; stopping follower replica ${target_id}"
stop_product_replica "${target_id}"
