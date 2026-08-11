# TDD-XXX: <title>

**Item:** ITEM-XXX  **Status:** Draft  **Date:** <date>

## Problem / motivation

## Scope

**In scope:** ...
**Out of scope:** ...

## Affected MCP tools / artifacts

For each new or changed `@mcp.tool()` in `server.py`, or each changed build
script + artifact schema if the item doesn't touch a tool directly:

- **Input schema:** ...
- **Output schema:** ...
- **Error cases:** ...
- **`sleeper_core` / build-script changes:** which module, what changes —
  `sleeper_core` has no MCP imports; logic goes there, `server.py` stays a
  thin wrapper.

## Provenance impact

Any new field follows the project's provenance convention: `measured`,
`measured:<year>`, `stale:team_changed`, `missing:not_recorded` (safe to
impute), `missing:no_team_context` (must NOT be imputed), `unsourced`. State
which tag new fields get and why.

## Test plan

The specific tests `implementer` will write first (RED), by name or
description — golden-output cases, schema validation, edge cases.

## Risk

## Open questions
