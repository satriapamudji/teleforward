# TeleForward v2 Plan: Multi-Destination Routing

## Purpose

Define v2 so one or more Telegram source channels can forward to many destinations across platforms:
- Discord webhooks
- Telegram destinations (channel/group, with optional topic/thread targeting)

This is a design-first document. No runtime behavior is changed by this file.

## Current State (v1)

- Sources: multiple Telegram channels/groups.
- Destinations: Discord webhooks only.
- Routing: many-to-many between source channels and Discord webhooks.
- Dispatcher: per-webhook queue worker model.

## v2 Target Behavior

- A single source message can fan out to N destinations, where N can include:
  - multiple Discord destinations
  - multiple Telegram destinations
- Support "many sources -> one Telegram destination" and mixed fanout in the same run.
- Keep per-route transform behavior (group + route rules), as in v1.

## Explicit Answer to Product Question

Yes, v2 should support:
- multiple Discord destinations at once
- multiple Telegram destinations at once
- mixed Discord + Telegram destinations for the same source message

## Scope

### In Scope
- Unified destination abstraction.
- Telegram send support for destination chat/topic.
- Routing updates in forwarder/dispatcher.
- TUI flows for destination management and route mapping.
- Schema migration from v1 data.

### Out of Scope (v2.0 baseline)
- Cross-account Telegram forwarding (single Telegram session only).
- Complex batching/aggregation before send.
- Message edit/delete sync.

## Proposed Data Model

Keep `telegram_channels` as source entities.

Add a unified destination model:
- `destinations`
  - `id` (pk)
  - `name` (string)
  - `destination_type` (`discord_webhook` | `telegram_chat`)
  - `is_active` (bool)
  - `created_at`

Platform-specific config tables:
- `destination_discord`
  - `destination_id` (fk destinations.id, unique)
  - `webhook_url`
- `destination_telegram`
  - `destination_id` (fk destinations.id, unique)
  - `chat_id` (int64)
  - `topic_id` (nullable int, top message/thread id)

Replace/augment mapping:
- `route_mappings`
  - `id` (pk)
  - `source_channel_id` (fk telegram_channels.id)
  - `destination_id` (fk destinations.id)
  - `group_id` (nullable fk forwarding_groups.id)
  - `is_active` (bool)
  - `created_at`

Logs:
- keep `forward_logs`, but reference route id (`route_mapping_id`) and destination info for debug clarity.

## Migration Strategy

1. Add new tables without removing v1 tables.
2. Backfill:
   - each active `discord_webhooks` row -> `destinations + destination_discord`.
   - each `channel_mappings` row -> `route_mappings`.
3. Read path transition:
   - forwarder reads v2 routes first.
   - remove v1 runtime fallback once v2 is stable.
4. Legacy policy for v2.0:
   - keep v1 tables as compatibility history (no destructive drop in v2.0).
   - use `teleforward migrate verify-v2` for migration/audit checks.
   - see `changelog/planned/docs/LEGACY_RETIREMENT_DECISION.md`.

## Runtime Architecture Changes

Introduce sender interface:
- `DestinationSender.send(destination, message_payload, media)` -> `(success, error)`

Implementations:
- `DiscordDestinationSender` (existing logic moved from `discord_sender.py`).
- `TelegramDestinationSender` (new, based on `TelegramClientWrapper` send APIs).

Forwarder changes:
- replace `_channel_webhook_map` with `_channel_route_map`.
- route item includes destination type/config and transform context.
- build one transformed payload per source message, then fan out per route.

Dispatcher changes:
- queue key becomes destination key (not webhook URL only).
- retain per-destination worker isolation.

## Telegram Destination Semantics

- `chat_id` required.
- `topic_id` optional:
  - if set, send into that forum topic/thread.
  - implementation detail must be validated against Telethon send API behavior.

Loop prevention:
- skip outbound/self messages (`message.out` and sender self checks).
- optional hard block when source channel equals destination chat.

Rate limiting:
- catch Telegram flood-wait exceptions and sleep/retry.
- keep bounded queue sizes per destination.

## TUI/UX Changes

New management sections:
- `Destinations`
  - add/list/edit/toggle/delete Discord destinations
  - add/list/edit/toggle/delete Telegram destinations
- `Routes`
  - create/manage routes from source channels to destinations (multi-select)

Wizard updates:
- include destination type choice.
- keep v1 quick path for Discord-only users.

## Observability

- Log with destination type/name/id on every send result.
- Doctor command checks:
  - invalid Discord webhook URLs
  - Telegram destination reachability/permission (optional network check)
  - orphaned/inactive routes

## Testing Plan

Unit:
- route loading and filtering (active/inactive/group handling)
- sender selection by destination type
- loop-prevention guard behavior

Integration:
- one source -> many Discord destinations
- one source -> many Telegram destinations
- one source -> mixed Discord+Telegram destinations
- flood-wait retry path (mocked)

Migration:
- v1 DB with existing mappings migrates to equivalent v2 routes.

## Rollout Plan

1. v2.0.0-alpha.1
   - schema + migration + internal sender interface
   - Discord parity through new route layer
2. v2.0.0-alpha.2
   - Telegram destination sender + queue/backoff + loop prevention
3. v2.0.0-beta.1
   - TUI destination/route UX
   - doctor checks + docs refresh
4. v2.0.0
   - default to v2 route model in all flows

## Acceptance Criteria for v2.0.0

- Can configure multiple Discord and Telegram destinations simultaneously.
- Can map multiple sources to the same Telegram destination.
- Source message fanout succeeds independently per destination.
- Failed destination does not block other destination sends.
- Existing v1 Discord mappings migrate without manual SQL edits.
