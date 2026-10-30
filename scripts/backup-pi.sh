#!/usr/bin/env bash
set -euo pipefail

# Script: backup-pi.sh
# Purpose: Execute 6-hourly automated backup of SQLite databases and .env secrets

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Run Python backup engine
python3 "$BASE_DIR/scripts/backup-pi.py" "$@"
