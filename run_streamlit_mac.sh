#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "First run: bash install_mac.sh. Daily startup never installs packages."
  exit 1
fi
export FINANCIAL_DEPLOYMENT=local
export FINANCIAL_WORKSPACE=main
export FINANCIAL_DATA_DIR="${FINANCIAL_DATA_DIR:-$HOME/Library/Application Support/FinancialReportQA/data}"
echo "Data directory: $FINANCIAL_DATA_DIR"
exec ./.venv/bin/python -m streamlit run app/main.py --server.address 127.0.0.1 --server.port 8501
