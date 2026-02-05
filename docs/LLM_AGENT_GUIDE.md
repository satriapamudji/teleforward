# LLM Agent Guide

This project is designed to be friendly to coding/deployment agents (Codex, ChatGPT, Claude, etc.).

## Safety rules (must follow)

- Treat these as secrets:
  - `TELEGRAM_SESSION_STRING`
  - Discord webhook URLs (they contain a token)
- Never paste secrets into logs, README examples, issues, or chat transcripts.
- Prefer `systemd` on VPS. Headless mode cannot prompt for Telegram login codes.

## Useful commands

Local:

- Run TUI: `teleforward tui`
- Run headless: `teleforward run`
- Validate: `teleforward doctor --test-webhooks`

Ubuntu VPS:

- Install: `scripts/vps_install_ubuntu.sh`
- Update: `scripts/vps_update_ubuntu.sh`
- Service logs: `sudo journalctl -u teleforward -f`

## Where things live

- TUI: `tui/app.py`
- Telegram client wrapper: `core/telegram_client.py`
- Forwarder + embed formatting: `core/forwarder.py`
- Discord webhooks sender: `core/discord_sender.py`
- Queue/dispatcher: `core/discord_dispatcher.py`
- DB: `database/`
