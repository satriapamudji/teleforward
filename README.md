<div align="center">

# TeleForward (TUI)

Secure Telegram -> Discord forwarder with a Rich terminal UI.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![TUI](https://img.shields.io/badge/TUI-rich-6A5ACD)
![Telegram](https://img.shields.io/badge/telegram-telethon-2CA5E0)
![Discord](https://img.shields.io/badge/discord-webhooks-5865F2)

</div>

TeleForward forwards messages from one or more Telegram channels/groups to one or more Discord channels using Discord webhooks. It is designed for VPS use (systemd included) and day-to-day operation via a Rich-based TUI.

> [!WARNING]
> Discord webhook URLs and `TELEGRAM_SESSION_STRING` are secrets. Do not paste them into logs, issues, or chat transcripts. If a webhook URL leaks, regenerate it in Discord.

## Contents

- [What you get](#what-you-get)
- [Quick start (local)](#quick-start-local)
- [Quick start (Ubuntu VPS + systemd)](#quick-start-ubuntu-vps--systemd)
- [First-time Telegram login (required for VPS)](#first-time-telegram-login-required-for-vps)
- [Usage](#usage)
- [Configuration reference](#configuration-reference)
- [Security notes](#security-notes)
- [Deployment scripts](#deployment-scripts)
- [For LLM agents](#for-llm-agents)

## What you get

- Telegram (user session) -> Discord (webhooks), no Discord bot token needed
- Multiple mappings:
  - many Telegram channels -> one webhook
  - one Telegram channel -> many webhooks
- Messages stay separate (no batching), sent via a queue with retries
- Discord embeds:
  - per-channel accent color
  - sender (when available) + content
  - optional title link to `t.me/...` (only when the channel has a public `@username`)
- Skips media-only posts (media with no caption/text)

## Quick start (local)

### Windows (PowerShell)

```powershell
copy .env.example .env
notepad .env
pip install -r requirements.txt
pip install -e .
teleforward tui
```

### Linux/macOS

```bash
cp .env.example .env
$EDITOR .env
pip install -r requirements.txt
pip install -e .
teleforward tui
```

> [!TIP]
> In the TUI, most prompts accept `q` to cancel/back, and Ctrl+C exits cleanly.

## Quick start (Ubuntu VPS + systemd)

End-to-end installer (recommended):

```bash
curl -fsSL https://raw.githubusercontent.com/satriapamudji/teleforward/main/scripts/vps_install_ubuntu.sh | sudo bash -s -- \
  --repo https://github.com/satriapamudji/teleforward.git
```

Then:

1. Edit secrets: `sudo nano /etc/teleforward/teleforward.env`
2. Do the one-time Telegram login via TUI (see below)
3. Start: `sudo systemctl restart teleforward`
4. Logs: `sudo journalctl -u teleforward -f`

## First-time Telegram login (required for VPS)

Headless mode (`teleforward run` / systemd) cannot prompt for Telegram login codes. You must log in once via the TUI and export a session.

1. Stop the service while logging in (prevents restart loops):

```bash
sudo systemctl stop teleforward
sudo systemctl reset-failed teleforward
```

2. Run the TUI with the systemd env file loaded:

```bash
set -a; source /etc/teleforward/teleforward.env; set +a
teleforward tui
```

3. In the TUI:
- Run **Login / refresh session**
- Run **Export session string (copy to env)** and paste into `/etc/teleforward/teleforward.env` as `TELEGRAM_SESSION_STRING=...`

4. Start the service:

```bash
sudo systemctl restart teleforward
sudo journalctl -u teleforward -f
```

## Usage

Commands:

```bash
teleforward tui      # interactive setup + tools
teleforward run      # headless forwarder (VPS)
teleforward doctor   # config sanity checks
```

Systemd:

```bash
sudo systemctl status teleforward --no-pager
sudo journalctl -u teleforward -f
```

## Configuration reference

Set these in `.env` (local) or `/etc/teleforward/teleforward.env` (VPS).

| Variable | Required | Default | Notes |
|---|---:|---|---|
| `TELEGRAM_API_ID` | yes | - | From `my.telegram.org` |
| `TELEGRAM_API_HASH` | yes | - | From `my.telegram.org` |
| `TELEGRAM_SESSION_STRING` | VPS | - | Recommended for headless runs |
| `DATA_DIR` | VPS | (derived) | For hardened systemd: `/var/lib/teleforward` |
| `DATABASE_PATH` | VPS | `data/teleforward.db` | For hardened systemd: `/var/lib/teleforward/teleforward.db` |
| `LOG_LEVEL` | no | `INFO` | |
| `DISCORD_ALLOW_MASS_MENTIONS` | no | `false` | If false, neutralizes `@everyone`/`@here` |
| `DISCORD_SUPPRESS_URL_EMBEDS` | no | `true` | Wraps URLs in `<...>` to reduce previews |
| `DISCORD_STRIP_URLS` | no | `false` | Removes URLs entirely (if your server has an unfurl bot) |
| `DISCORD_INCLUDE_TELEGRAM_LINK` | no | `true` | Title links to `t.me/...` when possible |

> [!IMPORTANT]
> If systemd runs with the hardened unit, TeleForward can only write to `/var/lib/teleforward`. Keep `DATA_DIR` and `DATABASE_PATH` set accordingly.

## Security notes

- Prefer storing `TELEGRAM_SESSION_STRING` in a secret manager on VPS.
- Keep `/etc/teleforward/teleforward.env` mode `600` and owned by the service user.
- Webhook URLs are validated to Discord domains and HTTPS to reduce SSRF risk.
- Webhook tokens are redacted from errors and HTTP request logging is suppressed by default.

## Deployment scripts

- Ubuntu VPS install: `scripts/vps_install_ubuntu.sh`
- Ubuntu VPS update: `scripts/vps_update_ubuntu.sh`
- Local setup:
  - Linux/macOS: `scripts/local_setup.sh`
  - Windows: `scripts/local_setup.ps1`

## For LLM agents

Fetch the install guide:

```bash
curl -fsSL https://raw.githubusercontent.com/satriapamudji/teleforward/main/docs/INSTALL_UBUNTU_VPS.md
```

Fetch the agent guide:

```bash
curl -fsSL https://raw.githubusercontent.com/satriapamudji/teleforward/main/docs/LLM_AGENT_GUIDE.md
```

Checklist:

1. Never paste secrets (webhook URLs, session strings) into output.
2. Use `scripts/vps_install_ubuntu.sh` / `scripts/vps_update_ubuntu.sh`.
3. For VPS, ensure:
   - `DATA_DIR=/var/lib/teleforward`
   - `DATABASE_PATH=/var/lib/teleforward/teleforward.db`
4. Run TUI once to login and export a session, then start systemd.
