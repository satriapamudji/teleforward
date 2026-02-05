<div align="center">

# TeleForward (TUI)

Secure Telegram -> Discord forwarder with a Rich terminal UI.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![TUI](https://img.shields.io/badge/TUI-rich-6A5ACD)
![Telegram](https://img.shields.io/badge/telegram-telethon-2CA5E0)
![Discord](https://img.shields.io/badge/discord-webhooks-5865F2)

</div>

TeleForward forwards messages from one or more Telegram channels/groups to one or more Discord channels using Discord webhooks. It is designed for VPS use (systemd included) and fast interactive operation via a Rich-based TUI.

## Contents

- [Features](#features)
- [Installation](#installation)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Configuration](#configuration)
- [Burst handling](#burst-handling)
- [Security notes](#security-notes)
- [Ubuntu VPS (systemd)](#ubuntu-vps-systemd)
- [Deployment scripts](#deployment-scripts)

## Features

- Telegram (user session) -> Discord (webhooks), no Discord bot token needed
- Multiple mappings:
  - many Telegram channels -> one webhook
  - one Telegram channel -> many webhooks
- Message delivery:
  - messages are kept separate (no batching)
  - per-webhook ordering, queueing, and retries on transient failures / rate limits
- Message appearance:
  - Discord embed "card" with sender (when available), content, and Discord timestamp
  - deterministic per-channel accent color
  - optional title hyperlink to the original `t.me/...` message when the Telegram channel has a public `@username`
- Skips media-only posts (media with no caption/text)

## Installation

### Local (Windows / Linux / macOS)

- Prereqs: Python 3.10+
- Setup (recommended):
  - Windows PowerShell: `.\scripts\local_setup.ps1`
  - Linux/macOS: `bash scripts/local_setup.sh`

### VPS (Ubuntu, systemd)

Use the end-to-end installer:

```bash
curl -fsSL https://raw.githubusercontent.com/satriapamudji/teleforward/main/scripts/vps_install_ubuntu.sh | sudo bash -s -- \
  --repo https://github.com/satriapamudji/teleforward.git
```

Then follow the printed “Next steps” (set secrets, run TUI once to login/export session, start service).

### For LLM agents

Fetch the installation guide and follow it:

```bash
curl -fsSL https://raw.githubusercontent.com/satriapamudji/teleforward/main/docs/INSTALL_UBUNTU_VPS.md
```

Fetch the LLM agent guide and follow it:

```bash
curl -fsSL https://raw.githubusercontent.com/satriapamudji/teleforward/main/docs/LLM_AGENT_GUIDE.md
```

If you’re using an LLM coding agent to deploy TeleForward, point it at:

- `scripts/vps_install_ubuntu.sh` and `scripts/vps_update_ubuntu.sh`
- `docs/INSTALL_UBUNTU_VPS.md` (step-by-step)
- The hard rules:
  - Never print/store Telegram session strings or Discord webhook URLs (they contain secrets).
  - Headless mode cannot prompt for Telegram login codes; use the TUI once to create/export a session.

<details>
<summary><strong>LLM agent checklist (click to expand)</strong></summary>

1. Install/update with `scripts/vps_install_ubuntu.sh` or `scripts/vps_update_ubuntu.sh`.
2. Confirm `/etc/teleforward/teleforward.env` has:
   - `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`
   - `DATA_DIR=/var/lib/teleforward`, `DATABASE_PATH=/var/lib/teleforward/teleforward.db`
3. Run the TUI once to create/export a Telegram session and store it as a secret:
   - `set -a; source /etc/teleforward/teleforward.env; set +a`
   - `teleforward tui` -> Login -> Export session string -> paste into env
4. Start and verify:
   - `sudo systemctl restart teleforward`
   - `sudo journalctl -u teleforward -f`

</details>

## Quick start

### Windows

```powershell
copy .env.example .env
pip install -r requirements.txt
python main.py tui
```

### Linux

```bash
cp .env.example .env
pip install -r requirements.txt
python main.py tui
```

### Optional: install as a CLI command

If you prefer running `teleforward ...` instead of `python main.py ...`, install the project into your venv:

```bash
pip install -e .
teleforward tui
```

In the TUI, most prompts accept `q` to go back/cancel, and Ctrl+C exits cleanly.

To run headless (VPS):

```bash
python main.py run
# or: teleforward run
```

## How it works

### Discord targeting model

Discord bots are not required. Create a webhook in the Discord channel you want to post into, then paste its URL into TeleForward. Each webhook is a destination. Mappings connect Telegram sources to webhook destinations.

### Message formatting

Messages are forwarded as a Discord embed:

- embed title: Telegram channel name (optionally hyperlinked to `t.me/...`)
- embed description: sender (when available) + message content
- embed footer: `TeleForward` (Discord also shows the embed timestamp)

## Configuration

Set these in `.env` (copy from `.env.example`) or via your environment.

### Required

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`

### Required for headless/VPS runs

When running in headless mode (`teleforward run` / systemd), TeleForward cannot prompt for a login code, so it needs an existing Telegram session:

- `TELEGRAM_SESSION_STRING` (recommended for VPS), **or**
- a previously-saved session string in the SQLite DB setting `app_settings.telegram_session_string` (saved automatically when you log in via the TUI with the same `DATABASE_PATH`).

### Optional (Discord formatting)

- `DISCORD_SUPPRESS_URL_EMBEDS=true` (default): wraps URLs in `<...>` to reduce Discord link-preview embeds.
- `DISCORD_STRIP_URLS=false`: removes URLs entirely if your server has an unfurl bot that still previews links.
- `DISCORD_ALLOW_MASS_MENTIONS=false` (default): neutralizes `@everyone` / `@here` to prevent mass pings.
- `DISCORD_INCLUDE_TELEGRAM_LINK=true` (default): makes the embed title clickable to `t.me/...` when possible.

### Optional (paths)

- `DATABASE_PATH`: SQLite path (default is under `DATA_DIR`)
- `DATA_DIR`: data directory for downloads and (optionally) Telegram session file

## Burst handling

TeleForward sends via an internal async queue:

- one worker per webhook URL (preserves per-webhook ordering)
- global in-flight cap + retries on 429 / 5xx / timeouts

## Security notes

- Prefer setting `TELEGRAM_SESSION_STRING` via environment/secret manager for production.
- If you use the TUI "save session" flow, it stores the session string inside the SQLite database (`DATABASE_PATH`).
- Keep your data directory private (database + downloads). On Linux, permissions should be restricted to only the service user.
- Only `https://discord.com/api/webhooks/...` (and Discord's official canary/ptb domains) are accepted to reduce SSRF risk.
- Treat Discord webhook URLs as secrets (they contain a token). If one leaks, regenerate the webhook in Discord and update TeleForward.

## Ubuntu VPS (systemd)

This repo includes a systemd unit: `deploy/systemd/teleforward.service`.

1. Copy the project to `/opt/teleforward` and create a venv:
   - `python3 -m venv /opt/teleforward/.venv`
   - `/opt/teleforward/.venv/bin/pip install -r /opt/teleforward/requirements.txt`
   - (optional, to enable the `teleforward` command) `/opt/teleforward/.venv/bin/pip install -e /opt/teleforward`
2. Create a dedicated user and config directory:
   - `sudo useradd --system --home /opt/teleforward --shell /usr/sbin/nologin teleforward`
   - `sudo mkdir -p /etc/teleforward`
   - `sudo cp deploy/systemd/teleforward.env.example /etc/teleforward/teleforward.env`
   - `sudo chmod 600 /etc/teleforward/teleforward.env && sudo chown teleforward:teleforward /etc/teleforward/teleforward.env`
3. Edit `/etc/teleforward/teleforward.env` with your secrets.
4. Install the service:
   - `sudo cp deploy/systemd/teleforward.service /etc/systemd/system/teleforward.service`
   - `sudo systemctl daemon-reload`
   - `sudo systemctl enable --now teleforward`
5. Validate + view logs:
   - `/opt/teleforward/.venv/bin/python /opt/teleforward/main.py doctor --test-webhooks`
   - `sudo journalctl -u teleforward -f`

### First-time Telegram login (VPS)

To run the systemd service, you must have a Telegram session available (see [Required for headless/VPS runs](#required-for-headlessvps-runs)).

Recommended flow:

1. Stop the service while you log in (prevents restart loops):
   - `sudo systemctl stop teleforward`
   - `sudo systemctl reset-failed teleforward`
2. Run the TUI with the systemd env file loaded:
   - `set -a; source /etc/teleforward/teleforward.env; set +a`
   - `teleforward tui`
3. In the TUI:
   - run **Login / refresh session**
   - then run **Export session string (copy to env)** and paste it into `/etc/teleforward/teleforward.env` as `TELEGRAM_SESSION_STRING=...`
4. Restart:
   - `sudo systemctl restart teleforward`

### VPS tips / troubleshooting

- If you run commands manually (not via systemd), `/etc/teleforward/teleforward.env` is not loaded automatically. Either:
  - activate the venv: `source /opt/teleforward/.venv/bin/activate`
  - or load the env file: `set -a; source /etc/teleforward/teleforward.env; set +a`
- To make manual runs easier, you can add a helper to `~/.bashrc`:
  - `teleforward() { set -a; source /etc/teleforward/teleforward.env; set +a; command teleforward "$@"; }`
- If systemd shows `OSError: [Errno 30] Read-only file system`, it usually means `DATA_DIR` / `DATABASE_PATH` were not loaded and the app tried to create `data/` under `/opt/teleforward` (which is read-only under the hardened unit). Fix by setting:
  - `DATA_DIR=/var/lib/teleforward`
  - `DATABASE_PATH=/var/lib/teleforward/teleforward.db`
- If you see `sqlite3.OperationalError: unable to open database file`:
  - check the path is absolute (must start with `/`): `DATABASE_PATH=/var/lib/teleforward/teleforward.db`
  - ensure `/var/lib/teleforward` is writable by the service user: `sudo -u teleforward test -w /var/lib/teleforward`
  - remove Windows CRLF if you edited the env on Windows: `sudo sed -i 's/\r$//' /etc/teleforward/teleforward.env`
- If you see `No TELEGRAM_SESSION_STRING set and no saved session in the database`, run the TUI login flow above (headless mode cannot prompt for login codes).

## Deployment scripts

These helpers automate common setups. Review scripts before running on a server.

### Ubuntu VPS (end-to-end)

From a cloned repo:

```bash
sudo bash scripts/vps_install_ubuntu.sh --repo .
```

From the internet (installer):

```bash
curl -fsSL https://raw.githubusercontent.com/satriapamudji/teleforward/main/scripts/vps_install_ubuntu.sh | sudo bash -s -- \
  --repo https://github.com/satriapamudji/teleforward.git
```

Update an existing VPS install:

```bash
sudo bash /opt/teleforward/scripts/vps_update_ubuntu.sh
```

### Local (Linux/macOS)

```bash
bash scripts/local_setup.sh
```

### Local (Windows PowerShell)

```powershell
.\scripts\local_setup.ps1
```
