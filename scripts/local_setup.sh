#!/usr/bin/env bash
set -euo pipefail

# Local setup helper (Linux/macOS)
#
# Usage:
#   bash scripts/local_setup.sh
#
# Creates a venv in ./.venv, installs dependencies, and runs the TUI.

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' || {
  echo "Python 3.10+ is required." >&2
  exit 1
}

if [[ ! -f ".env" ]]; then
  echo "Creating .env from .env.example"
  cp .env.example .env
  echo "Edit .env and set TELEGRAM_API_ID/TELEGRAM_API_HASH before running."
fi

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
pip install -e .

teleforward tui
