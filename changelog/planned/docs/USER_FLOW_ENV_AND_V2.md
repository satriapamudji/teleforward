# User Flow: Env, Session, and v2 Rollout

## Goals

- Let users manage Telegram/API/session settings without hand-editing files.
- Keep configuration health checks in CLI (`doctor`).
- Operate fully on the v2 destination/route model.

## Recommended User Flow

1. Initial setup (no manual file editing required):
   - `teleforward config set TELEGRAM_API_ID <id>`
   - `teleforward config set TELEGRAM_API_HASH <hash>`
   - `teleforward tui`

2. Telegram login/session:
   - In TUI, use `Login / refresh session`.
   - Use `Settings (env/db/session)` -> `Export logged-in Telegram session to db + .env`.

3. Validate:
   - `teleforward doctor`
   - `teleforward migrate verify-v2` (legacy mirror audit)
   - Optional: `teleforward doctor --sync-session-to-env` to persist DB session into `.env`.
   - Optional: `teleforward doctor --test-telegram-destinations` to verify Telegram destination reachability.

4. Normal operations:
   - Configure sources, destinations, mappings/routes in TUI.
   - Run with `teleforward run` (headless) or `teleforward tui`.

## Why This Flow

- `.env` edits are centralized through TUI/CLI commands.
- Session source is explicit (`env`, `db`, or `none`) in `doctor`.
- Safe in VPS mode because session persistence can be validated before service restarts.

## v2 Rollout Status

- Completed now (alpha.1 foundation):
  - v2 schema (destinations/routes/logs v2)
  - v1 -> v2 sync/backfill
- Completed now (alpha.2 core):
  - Telegram destination sender with retry/flood-wait handling
  - topic/thread field support in v2 route data model
  - mixed Discord+Telegram route fanout in forwarder runtime path
  - loop-prevention guard (outbound/self and source==destination chat)
- Completed now (beta):
  - full destination/route management UX in TUI
  - setup wizard uses v2 destination + route flow
  - `doctor` checks for Telegram destinations
- Completed now (v2-only cleanup):
  - runtime forwarding path uses v2 routes only
  - TUI tools `15` and `16` are v2 destination/route aware
  - primary TUI menu flow is v2 destinations/routes
- Completed now (legacy retirement decision for v2.0):
  - v1 tables are kept as compatibility history (non-destructive)
  - migration audit command available: `teleforward migrate verify-v2`
