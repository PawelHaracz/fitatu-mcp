# Phase 2 Scope Clarifications

## Critical decisions

| ID | Decision | Chosen |
|---|---|---|
| D1 | Local Product ORM table | **Add Product table + migration** (caller chose explicit cache over thin proxy) |
| D2 | Cache re-sync failure semantics | ok_remote_only after one inline retry |
| D3 | Allowed write date scope | Any date Fitatu accepts |
| D4 | Destructive op guard | Env flag `FITATU_ALLOW_DELETE` default `false` |

## Important decisions

| ID | Decision | Chosen |
|---|---|---|
| D5 | 401-refresh-retry helper | **Extract `_request` helper upfront** (pre-factor before adding 5 HTTP methods) |
| D6 | FITATU_WRITE_ENABLED flag | **Drop — register writes unconditionally** |
| D7 | Write tool envelope shape | **Multi-day shape for read-consistency** (day_count=1 for writes) |
| D8 | search_products scope | **Add explicit `scope` parameter** (custom \| catalog \| all) |
| D9 | Audit log table | No — stdout logging only |
| D10 | meal_key validation | Whitelist after Phase 0 discovery confirms the set |
| D11 | Idempotency-Key header | No — capture in Phase 0 whether Fitatu accepts it |

## Implications

- **Schema work**: ORM `Product` table + migration story required. No Alembic in repo today; need init-time DDL via `Base.metadata.create_all` (same as existing tables) plus a one-off migration helper for existing DBs.
- **Pre-factor**: Add `FitatuClient._request(method, url, json=...)` with built-in login/refresh/401 retry before writing `add_meal_item` etc. Refactor `get_day()` onto the helper as part of the change.
- **Response shape**: Write tools return `_range_envelope(day_date, day_date, [day_result])` to mirror read tools. Reduces ergonomics asymmetry.
- **search_products**: param list = `query: str, scope: Literal["custom","catalog","all"]="all", limit: int=20`.
- **Delete guard**: server.py reads `FITATU_ALLOW_DELETE` env at registration time; if `false`, the delete tool either raises or is not registered. (Pick non-registration to keep tool catalog truthful.)
- **No audit table, no idempotency keys, no write feature flag** — keep surface tight.

## Phase 0 (Discovery) deliverable required before spec finalization

`analysis/fitatu-api-discovery.md` must contain captured network traces for:
- POST /add_meal_item endpoint
- PUT /update_meal_item endpoint
- DELETE /delete_meal_item endpoint
- POST /create_custom_product endpoint
- GET/POST product search endpoint
- Discovered meal_key whitelist
- Whether Fitatu accepts `Idempotency-Key` header

Captured via Playwright MCP on pl-pl.fitatu.com.
