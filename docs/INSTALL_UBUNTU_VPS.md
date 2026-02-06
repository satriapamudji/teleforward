# Install (Ubuntu VPS + systemd)

This guide installs TeleForward as a hardened systemd service on Ubuntu/Debian-ish hosts.

## Prereqs

- Ubuntu/Debian
- `python3` (3.10+), `git`, `systemd`
- A Telegram API ID + hash from `my.telegram.org`
- Destination targets (as needed):
  - Discord webhook URL(s)
  - Telegram destination chat/topic id(s)

## One-command install

```bash
curl -fsSL https://raw.githubusercontent.com/satriapamudji/teleforward/main/scripts/vps_install_ubuntu.sh | sudo bash -s -- \
  --repo https://github.com/satriapamudji/teleforward.git
```

This installs to:

- app: `/opt/teleforward`
- env: `/etc/teleforward/teleforward.env`
- state: `/var/lib/teleforward`
- service: `teleforward.service`

## Configure secrets

Edit:

```bash
sudo nano /etc/teleforward/teleforward.env
```

Set at least:

- `TELEGRAM_API_ID=...`
- `TELEGRAM_API_HASH=...`

Optional but recommended (headless runs):

- `TELEGRAM_SESSION_STRING=...`

## First-time Telegram login (required)

Headless mode cannot prompt for login codes. Do a one-time login via the TUI:

```bash
cd /opt/teleforward
source .venv/bin/activate
set -a; source /etc/teleforward/teleforward.env; set +a
teleforward tui
```

In the TUI:

1. Run **Login / refresh session**
2. Run **Export session string (copy to env)** and paste it into `/etc/teleforward/teleforward.env` as `TELEGRAM_SESSION_STRING=...`

## Start the service

```bash
sudo systemctl restart teleforward
sudo systemctl status teleforward --no-pager
sudo journalctl -u teleforward -f
```

## Update later

```bash
sudo bash /opt/teleforward/scripts/vps_update_ubuntu.sh
```

## Troubleshooting

- If systemd shows `OSError: [Errno 30] Read-only file system`, confirm:
  - `DATA_DIR=/var/lib/teleforward`
  - `DATABASE_PATH=/var/lib/teleforward/teleforward.db`
- If SQLite can't open the DB:
  - ensure `DATABASE_PATH` starts with `/` (absolute path)
  - remove CRLF from the env file: `sudo sed -i 's/\r$//' /etc/teleforward/teleforward.env`
  - check permissions: `sudo -u teleforward test -w /var/lib/teleforward`

