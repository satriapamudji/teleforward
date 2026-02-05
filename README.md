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
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Configuration](#configuration)
- [Burst handling](#burst-handling)
- [Security notes](#security-notes)
- [Ubuntu VPS (systemd)](#ubuntu-vps-systemd)

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

In the TUI, most prompts accept `q` to go back/cancel, and Ctrl+C exits cleanly.

To run headless (VPS):

```bash
python main.py run
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

## Ubuntu VPS (systemd)

This repo includes a systemd unit: `deploy/systemd/teleforward.service`.

1. Copy the project to `/opt/teleforward` and create a venv:
   - `python3 -m venv /opt/teleforward/.venv`
   - `/opt/teleforward/.venv/bin/pip install -r /opt/teleforward/requirements.txt`
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
