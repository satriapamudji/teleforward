# Changelog

All notable changes to this project will be documented in this file.

Last updated: `2026-02-07 13:44:15 +08:00` (local time)

## [Unreleased] - Logged `2026-02-07 13:44:15 +08:00`

### Added
- v2 planning documentation for multi-destination forwarding (Discord + Telegram):
  - `changelog/planned/docs/PLAN_V2_MULTI_DESTINATION.md`
- operator UX flow documentation for env/session/config handling:
  - `changelog/planned/docs/USER_FLOW_ENV_AND_V2.md`
- CLI env management command:
  - `teleforward config show`
  - `teleforward config set <KEY> <VALUE>`
  - `teleforward config unset <KEY>`
- TUI settings menu (`17`) to manage:
  - Telegram API id/hash
  - Telegram session string (`.env` + DB export path)
  - `DATABASE_PATH`, `DATA_DIR`, `LOG_LEVEL`
- Doctor enhancements:
  - prints `ENV_PATH`
  - prints `TELEGRAM_SESSION_SOURCE`
  - optional `--sync-session-to-env`
- v2 alpha.1 foundation:
  - new destination and route schema tables
  - idempotent v1 -> v2 sync
  - dispatcher logging supports legacy (`forward_logs`) and v2 (`forward_logs_v2`) paths
- v2 alpha.2 core routing foundation:
  - Telegram destination sender with retry/flood-wait handling (`core/telegram_sender.py`)
  - v2 route rows now include Telegram destination fields (`chat_id`, optional `topic_id`)
  - forwarder supports mixed v2 fanout (Discord + Telegram) at runtime
  - direct Telegram route logging to `forward_logs_v2`
  - loop guard for outbound/self messages and source==destination chat
- v2 beta operator UX:
  - TUI v2 destination management:
    - list/add/manage destination (Discord webhook or Telegram chat)
  - TUI v2 route management:
    - create/manage routes from Telegram sources to v2 destinations
  - setup wizard now uses v2 destination + route flow
  - status bar now shows v2 destination/route counts
  - logs view uses v2 route logs (`forward_logs_v2`)
- doctor validation enhancements:
  - prints active counts for channels/destinations/routes
  - optional `--test-telegram-destinations` reachability check for Telegram destinations
- v2-only cleanup:
  - forwarder runtime now loads active v2 routes only (no v1 runtime fallback)
  - tools `15` and `16` in TUI now target v2 destinations/routes
  - primary TUI menu is destination/route-first (legacy webhook/mapping menus removed)
  - doctor webhook tests now validate Discord destinations from v2 destination table
- v2 destination edit persistence fix:
  - legacy backfill sync is now insert-only (no overwrite/delete of existing v2 rows)
  - editing v2 destination targets (e.g. Discord webhook URL) now persists across restarts
- terminology cleanup (v2 UX):
  - TUI primary wording now uses source/destination/route terminology
  - status header now shows `sources=... destinations=... routes=...`
  - run/tools labels updated for clearer v2 intent
  - doctor now prints `ACTIVE_SOURCES` (plus `ACTIVE_CHANNELS` alias)
- TUI visual polish pass:
  - dashboard now includes compact metric cards (sources/destinations/routes/session)
  - menu tables now use stronger box styles and alternating row contrast
  - section panels now have distinct border colors for faster scanning
  - navigation panel now includes an explicit keyboard hint subtitle
- legacy retirement decision (v2.0):
  - v1 tables are retained as compatibility history (non-destructive policy)
  - new migration audit command: `teleforward migrate verify-v2`
  - policy document: `changelog/planned/docs/LEGACY_RETIREMENT_DECISION.md`
- TUI naming/copy polish:
  - app header now shows `teleforward <version>` instead of a hardcoded `v2` label
  - removed `v2` wording from destination/route/log labels in the TUI
  - menu wording refined for clearer flow (`Run forwarder`, `Show recent logs`, `Settings`)
  - package version moved to `2.0.0` for stable v2 labeling (no prerelease `b` suffix)
- homepage layout tightening:
  - home screen now renders inside a single centered `Home` box panel
  - dashboard header is compact (`Panel.fit`) and nested inside the home container
- homepage responsive sizing/padding:
  - increased inner vertical spacing in `Home` panel (`padding=(1, 2)`)
  - menu auto-switches between two-column and stacked layout by terminal width
  - home panel width now recalculates from current terminal width each redraw
- live home resize/input (cross-platform):
  - Windows: non-blocking key capture via `msvcrt` while `Live` continuously re-renders
  - Linux/macOS: non-blocking key capture via `termios`/`tty`/`select` with raw-mode restore
  - non-interactive stdin fallback keeps prompt-based input path
- docs/readme sync:
  - README command list now shows explicit `teleforward migrate verify-v2`
  - install guide prereqs now include Discord and Telegram destination targets
  - fixed install guide encoding typo in SQLite troubleshooting note
  - LLM guide now shows both baseline and destination-level doctor validation commands

### Security
- fix Discord webhook token redaction (avoid leaking tokens in exceptions/diagnostics)
- redact webhook URLs in TUI destination/route tables
- remove tracked `.env` and ignore `.env.*` while keeping `.env.example`

## [2.0.0] - Planned (remaining) (logged `2026-02-06 18:30:11 +08:00`)

### Planned
- Production rollout hardening:
  - run live VPS canary checks for mixed Discord + Telegram routes
  - verify topic/thread delivery behavior in real Telegram forum chats
- Operational safeguards:
  - document backup/rollback procedure for DB and env during cutover
  - add explicit runbook for systemd deployments
- Release packaging:
  - bump project version for v2 release tag
  - finalize release notes and announce v2 as default operating model
