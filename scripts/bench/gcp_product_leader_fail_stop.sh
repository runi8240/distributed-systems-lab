#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/gcp_failover_common.sh"

STATE_FILE="${PA3_STATE_DIR}/product_leader_fail_replica_id"

if [[ ! -f "${STATE_FILE}" ]]; then
  log "no recorded product leader failure state at ${STATE_FILE}"
  exit 1
fi

leader_id="$(tr -d '[:space:]' < "${STATE_FILE}")"
if [[ -z "${leader_id}" ]]; then
  log "state file ${STATE_FILE} is empty"
  exit 1
fi

log "recovering stopped product leader replica ${leader_id}"
restart_product_replica "${leader_id}"
rm -f "${STATE_FILE}"
