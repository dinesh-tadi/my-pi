#!/usr/bin/env bash
set -euo pipefail

# Script: on-boot-check.sh
# Purpose: Execute boot integrity verification and self-healing

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

python3 "$BASE_DIR/scripts/on-boot-check.py" "$@"
