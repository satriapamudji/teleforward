#!/usr/bin/env bash
set -euo pipefail

# TeleForward VPS updater (Ubuntu/Debian-ish)
#
# Usage (from /opt/teleforward):
#   sudo bash scripts/vps_update_ubuntu.sh
#
# Or from anywhere:
#   sudo bash /opt/teleforward/scripts/vps_update_ubuntu.sh

APP_DIR="/opt/teleforward"

need_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "Run as root (use sudo)." >&2; exit 1; }
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

need_root
need_cmd git
need_cmd systemctl

if [[ ! -d "$APP_DIR" ]]; then
  echo "Expected app directory at $APP_DIR" >&2
  exit 1
fi

echo "[1/4] Pull latest"
git -C "$APP_DIR" pull --ff-only

echo "[2/4] Reinstall package (editable) in venv"
"$APP_DIR/.venv/bin/pip" install -e "$APP_DIR"

echo "[3/4] Restart service"
systemctl restart teleforward

echo "[4/4] Status"
systemctl status teleforward --no-pager -l || true
