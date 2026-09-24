#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if command -v python3.11 >/dev/null 2>&1; then
  python3.11 scripts/install_local.py
elif command -v python3 >/dev/null 2>&1; then
  python3 scripts/install_local.py
else
  echo "Install Python 3.11, then run: bash install_mac.sh"
  exit 1
fi
