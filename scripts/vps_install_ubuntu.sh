#!/usr/bin/env bash
set -euo pipefail

# TeleForward VPS installer (Ubuntu/Debian-ish)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/satriapamudji/teleforward/main/scripts/vps_install_ubuntu.sh | sudo bash -s -- \
#     --repo https://github.com/satriapamudji/teleforward.git
#
# Or from a cloned repo:
#   sudo bash scripts/vps_install_ubuntu.sh --repo .
#
# After install:
#   1) Edit /etc/teleforward/teleforward.env (add TELEGRAM_API_ID/HASH and optionally TELEGRAM_SESSION_STRING)
#   2) Run first-time login via TUI once:
#        set -a; source /etc/teleforward/teleforward.env; set +a
#        /opt/teleforward/.venv/bin/teleforward tui
#      then export session string and paste into env
#   3) Start service:
#        sudo systemctl restart teleforward

REPO="https://github.com/satriapamudji/teleforward.git"
APP_DIR="/opt/teleforward"
ENV_DIR="/etc/teleforward"
ENV_FILE="$ENV_DIR/teleforward.env"
SERVICE_FILE="/etc/systemd/system/teleforward.service"
APP_USER="teleforward"

usage() {
  cat <<EOF
TeleForward VPS installer (Ubuntu)

Options:
  --repo <url-or-path>   Git repo URL (default: $REPO) or local path "."
  --dir <path>           Install directory (default: $APP_DIR)
  --user <name>          Service user (default: $APP_USER)

Example:
  sudo bash scripts/vps_install_ubuntu.sh --repo $REPO
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2;;
    --dir) APP_DIR="${2:-}"; shift 2;;
    --user) APP_USER="${2:-}"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

need_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "Run as root (use sudo)." >&2; exit 1; }
}

need_root
need_cmd python3
need_cmd git
need_cmd systemctl

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then
  echo "Python 3.10+ is required (found $(python3 -V))." >&2
  exit 1
fi

echo "[1/7] Create service user ($APP_USER) if missing"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

echo "[2/7] Install/update app at $APP_DIR"
mkdir -p "$APP_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

if [[ "$REPO" == "." ]]; then
  rsync -a --delete --exclude ".git" ./ "$APP_DIR/"
elif [[ -d "$REPO" ]]; then
  rsync -a --delete --exclude ".git" "$REPO"/ "$APP_DIR/"
else
  if [[ -d "$APP_DIR/.git" ]]; then
    git -C "$APP_DIR" remote set-url origin "$REPO" || true
    git -C "$APP_DIR" fetch --all --prune
    git -C "$APP_DIR" reset --hard origin/main || git -C "$APP_DIR" reset --hard origin/master
  else
    rm -rf "$APP_DIR"/*
    git clone --depth 1 "$REPO" "$APP_DIR"
  fi
fi

chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "[3/7] Create venv + install dependencies"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -U pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/pip" install -e "$APP_DIR"

echo "[4/7] Create env file at $ENV_FILE (if missing)"
mkdir -p "$ENV_DIR"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$APP_DIR/deploy/systemd/teleforward.env.example" "$ENV_FILE"
  chown "$APP_USER:$APP_USER" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
else
  chmod 600 "$ENV_FILE" || true
fi

echo "[5/7] Install systemd service"
cp "$APP_DIR/deploy/systemd/teleforward.service" "$SERVICE_FILE"
chmod 644 "$SERVICE_FILE"
systemctl daemon-reload

echo "[6/7] Enable service (will fail until Telegram session exists)"
systemctl enable teleforward >/dev/null 2>&1 || true

echo "[7/7] Next steps"
cat <<EOF

Installed TeleForward.

1) Edit secrets:
   sudo nano $ENV_FILE

2) First-time Telegram login (TUI):
   set -a; source $ENV_FILE; set +a
   $APP_DIR/.venv/bin/teleforward tui

3) Start service:
   sudo systemctl restart teleforward
   sudo journalctl -u teleforward -f

EOF
