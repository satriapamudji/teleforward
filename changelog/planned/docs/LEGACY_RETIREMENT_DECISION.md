# Legacy Retirement Decision (v2)

Decision timestamp: `2026-02-06 18:33:48 +08:00`

## Final Decision

For v2.0 rollout, keep v1 tables as compatibility history (no destructive table drop in v2.0).

## Why

- Forwarding runtime is already v2-only (destinations/routes).
- Existing installs may still have legacy data that must remain auditable.
- Some transform-rule references still point to legacy mapping ids (`transform_rules.mapping_id`).

## Operational Policy (effective now)

- Source of truth for forwarding: v2 tables only (`destinations`, `route_mappings`, `forward_logs_v2`).
- Legacy sync behavior: insert-only backfill from v1 to v2 (no overwrite/delete of v2 rows).
- No automatic legacy cleanup/drop command is exposed in v2.0.

## Verification Command

Use:

```bash
teleforward migrate verify-v2
```

This validates:
- v1 webhook rows mirrored into v2 destinations
- v1 channel mapping rows mirrored into v2 routes
- orphan/missing legacy references
- transform rules that reference missing legacy mappings

## Future Retirement Gate (post-v2.0)

Before any hard legacy drop:

1. Move transform-rule linkage from legacy mapping ids to stable v2 route linkage.
2. Ship explicit backup + verify + cleanup workflow.
3. Run canary validation on VPS and confirm parity before deletion.
