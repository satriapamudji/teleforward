# TeleForward (TUI) - Telegram -> Discord

TeleForward forwards messages from one or more Telegram channels/groups to one or more Discord channels using Discord webhooks. It is designed to run securely on a VPS (systemd included) and be operated via a Rich-based terminal UI.

## What it does

- Telegram (user session) -> Discord (webhooks), no Discord bot token needed
- Multiple mappings:
  - many Telegram channels -> one webhook
  - one Telegram channel -> many webhooks
- Message delivery:
  - messages are kept separate (no batching)
  - per-webhook ordering + retries on rate limits / transient errors
- Message appearance:
  - Discord embed "card" with sender (when available), content, and Discord timestamp
  - deterministic per-channel accent color
  - optional title hyperlink to the original `t.me/...` message when the channel has a public `@username`
- Skips media-only posts (media with no caption/text)

## Quick start (Windows / Linux)

1. Copy `.env.example` to `.env` and fill:
   - `TELEGRAM_API_ID`
   - `TELEGRAM_API_HASH`
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Run the interactive TUI:
   - `python main.py tui`
4. Start forwarding (headless):
   - `python main.py run`

Tip: In the TUI, most prompts accept `q` to go back/cancel, and Ctrl+C exits cleanly.

## How Discord "channels" are targeted

Discord bots are not required. Create a webhook in the Discord channel you want to post into, then paste its URL into TeleForward.

## Message formatting knobs

- `DISCORD_SUPPRESS_URL_EMBEDS=true` (default): wraps URLs in `<...>` to reduce Discord link-preview embeds.
- `DISCORD_STRIP_URLS=false` (optional): removes URLs entirely if your server has an unfurl bot that still previews links.
- `DISCORD_ALLOW_MASS_MENTIONS=false` (default): neutralizes `@everyone` / `@here` to prevent mass pings.
- `DISCORD_INCLUDE_TELEGRAM_LINK=true` (default): makes the embed title clickable to `t.me/...` when possible.

## Burst handling (Discord rate limits)

TeleForward sends via an internal async queue:

- one worker per webhook URL (preserves per-webhook ordering)
- global in-flight cap + retries on 429 / 5xx / timeouts

## Security notes (VPS)

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
