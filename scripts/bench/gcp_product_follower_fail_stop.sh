#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/gcp_failover_common.sh"

STATE_FILE="${PA3_STATE_DIR}/product_follower_fail_replica_id"

if [[ ! -f "${STATE_FILE}" ]]; then
  log "no recorded product follower failure state at ${STATE_FILE}"
  exit 1
fi

target_id="$(tr -d '[:space:]' < "${STATE_FILE}")"
if [[ -z "${target_id}" ]]; then
  log "state file ${STATE_FILE} is empty"
  exit 1
fi

log "recovering stopped product follower replica ${target_id}"
restart_product_replica "${target_id}"
rm -f "${STATE_FILE}"
