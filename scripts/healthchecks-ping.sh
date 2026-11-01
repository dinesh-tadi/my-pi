#!/usr/bin/env bash
# Script: healthchecks-ping.sh
# Purpose: Send a 1-minute heartbeat to Healthchecks.io dead-man's switch

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PING_URL=$(grep -E '^HEALTHCHECKS_PING_URL=' "$BASE_DIR/stock-watcher/.env" 2>/dev/null | cut -d '=' -f2- | tr -d ' "' || true)

if [ -n "$PING_URL" ]; then
    curl -m 5 -fsS "$PING_URL" > /dev/null 2>&1 || true
fi
