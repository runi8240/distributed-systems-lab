#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/gcp_failover_common.sh"

FRONTEND_BUYER_REPLICA_ID="${FRONTEND_BUYER_REPLICA_ID:-1}"
FRONTEND_SELLER_REPLICA_ID="${FRONTEND_SELLER_REPLICA_ID:-2}"

log "recovering frontend failover: buyer replica ${FRONTEND_BUYER_REPLICA_ID}, seller replica ${FRONTEND_SELLER_REPLICA_ID}"
restart_buyer_replica "$FRONTEND_BUYER_REPLICA_ID"
restart_seller_replica "$FRONTEND_SELLER_REPLICA_ID"
