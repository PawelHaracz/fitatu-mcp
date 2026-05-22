# Codebase Analysis Report

**Date**: 2026-05-21
**Task**: Add MCP write capability for products (foods/ingredients) and meals (dishes/recipes)
**Description**: Currently the MCP exposes read-only tools (get_day_macros, get_day_meals, get_day_summary, sync_day, get_cache_stats). Enable users to CREATE/UPDATE/DELETE products and meals via MCP tools that proxy to the Fitatu backend API.
**Analyzer**: codebase-analyzer skill (3 Explore agents: File Discovery, Code Analysis, Pattern Mining)

---

## Executive Summary

Fitatu-MCP is a synchronous, read-only middleware between MCP clients (n8n/Claude) and the proprietary Fitatu nutrition API. It already has a clean separation across transport (`server.py`), HTTP client (`fitatu_client.py`), business logic (`service.py`), ORM (`models.py`), and Pydantic schemas (`schemas.py`), with a working auth/refresh loop and additive SQLite cache. **Extending it to write is structurally cheap on our side (3 well-known integration points) but blocked by a single hard unknown: the Fitatu backend write endpoints are not in the codebase and must be reverse-engineered from the web app before any tool can be implemented.** The lowest-risk path is a write-through MVP that calls Fitatu directly and invalidates the local day cache, deferring any local product catalog until a clear need emerges.

---

## File Inventory

### Primary Files

**/Users/pawelharacz/src/private/fitatu-mcp/server.py** (377 lines)
- FastAPI host + FastMCP registration, Bearer middleware, date validation, range envelopes, today-staleness check.
- New write tools will be added here as `@mcp.tool(...)` functions following the `mcp_sync_day` template (lines 207-239).

**/Users/pawelharacz/src/private/fitatu-mcp/fitatu_client.py** (169 lines)
- Sync HTTP client (`requests`), JWT decode, login/refresh, single `get_day()` method.
- Will gain `create_product`, `update_product`, `delete_product`, `create_meal_item`, `update_meal_item`, `delete_meal_item` (names TBD by API).
- The 401-recovery pattern in `get_day()` (lines 153-163) is the canonical shape every new HTTP method should mirror.

**/Users/pawelharacz/src/private/fitatu-mcp/service.py** (330 lines)
- Translates Fitatu payloads into ORM rows via additive diffing keyed on `plan_day_diet_item_id`.
- Will gain CRUD helpers and a cache-invalidation/re-sync helper invoked after each successful write.

**/Users/pawelharacz/src/private/fitatu-mcp/models.py** (92 lines)
- ORM: `DailyNutrition`, `MealNutrition`, `MealItem`. No `Product` table.
- `MealItem.plan_day_diet_item_id` is the per-day identity used by the diff in `persist_day_summary`; writes returning a new `planDayDietItemId` plug straight back into existing reconciliation.

**/Users/pawelharacz/src/private/fitatu-mcp/schemas.py** (47 lines)
- Pydantic v2 models. New input/output schemas (`ProductInput`, `MealItemInput`) will live here.

### Related Files

**/Users/pawelharacz/src/private/fitatu-mcp/database.py** (16 lines) - SQLite engine + `SessionLocal`. Untouched.
**/Users/pawelharacz/src/private/fitatu-mcp/.env.example** (38 lines) - Config reference; may grow new toggles for write features.
**/Users/pawelharacz/src/private/fitatu-mcp/README.md** (106 lines) - Tool catalogue to be updated.
**/Users/pawelharacz/src/private/fitatu-mcp/Dockerfile**, **compose.yml** - Container wiring, no functional change needed.

---

## Architecture

Sync end to end (no async). Layering:

```
MCP client
  -> FastAPI (server.py) [Bearer middleware, FastMCP streamable_http]
    -> @mcp.tool functions [validation + envelope]
      -> service.py [Fitatu payload <-> Pydantic <-> ORM, additive diff]
        -> fitatu_client.py [requests, login/refresh, JWT user_id]
        -> SQLAlchemy 2.0 (DeclarativeBase, Mapped[])
          -> SQLite (data/*.db)
```

Read flow (`get_day_meals`): validate range -> `with SessionLocal()` -> `_ensure_user_id()` -> per-day `_load_or_sync_day` (DB hit or `sync_day_from_fitatu` -> `client.get_day` -> `aggregate_day_summary` -> `persist_day_summary`) -> `db_day_to_schema` -> envelope `{ start_date, end_date, day_count, days: [...] }`.

Auth model:
- Inbound MCP: `Bearer MCP_API_KEY` middleware (server.py:81-93).
- Outbound Fitatu: BASE_HEADERS + Bearer token + `API-Cluster: pl-pl{user_id}`; lazy login, 401 -> refresh -> login -> retry.

Cache model: only `today` is TTL-bound (`TODAY_TTL_SECONDS`, default 300s); past days are static unless explicitly re-synced. Writes must invalidate or re-sync the affected day(s).

---

## Existing Patterns

### Tool template (server.py:207-239 `mcp_sync_day`)
- `@mcp.tool(name=..., description=...)` with snake_case name, `mcp_<tool_name>` function.
- Plain Python types for parameters (no Pydantic). Optional params default to `""`.
- Validate inputs via `_DATE_RE`, `_parse_date`, `_validate_date_range`.
- `with SessionLocal() as db:` context manager.
- `_ensure_user_id()` lazy-logs in if needed.
- Return a structured `dict`, use `_range_envelope(...)` for multi-day.
- `logger.info("Tool X called key=%s", value)` at entry.

### HTTP client template (`get_day`, lines 142-169)
- Lazy login if `self.token` missing.
- `headers = BASE_HEADERS.copy(); headers["Authorization"] = f"Bearer {self.token}"; headers["API-Cluster"] = f"pl-pl{self.user_id}"`.
- `requests.<method>(url, headers=headers, json=payload, timeout=20)`.
- `if status == 401: refresh() or login(); retry`.
- Non-2xx -> `raise RuntimeError(f"<op> failed with status {status}: {response.text}")`.

### Schema conventions
- Pydantic v2 `BaseModel`, `str | None = None`, `Field(default_factory=list)`, `.model_dump()` to serialize.

### ORM conventions
- `Mapped[type]` + `mapped_column(...)`.
- `UniqueConstraint` in `__table_args__` (see `uq_meal_item_plan_id`).
- `ForeignKey(..., ondelete="CASCADE")` + `relationship(..., cascade="all, delete-orphan", passive_deletes=True)`.
- Timestamps: `lambda: datetime.now(timezone.utc)` for default and `onupdate`.

### Error handling
- Client layer: raises `FitatuAuthError` or `RuntimeError`.
- Service layer: validates, raises `ValueError`, logs info/warning.
- Tool layer: `try/except Exception as exc: logger.warning(...); days.append({"day_date": ..., "error": str(exc)})` for per-day isolation.

### Naming
- Tools: snake_case (`sync_day`, will be e.g. `add_meal_item`, `delete_meal_item`).
- Tool functions: `mcp_<tool_name>`.
- Helpers: `_underscore_prefix`.
- Schemas: `<Name>Schema`. Models: PascalCase.
- Variables: `day_date: str` (YYYY-MM-DD), `user_id: str`, `meal_key: str`.

### Anti-patterns to avoid (called out by Pattern Mining)
- Raw `requests.*` from tool handlers - always go through `FitatuClient`.
- Bare DB sessions without context manager.
- Swallowing errors silently.
- Hardcoded URLs in tools - URL templates live in `fitatu_client.py`.
- Pydantic models as MCP tool parameters - use plain types and convert internally.

---

## Integration Points

Three layers, each additive (no edits to existing read tools required):

1. **`fitatu_client.py`** - add HTTP methods. Each new method copies the `get_day` shape (headers + 401-retry + raise on non-2xx).
2. **`service.py`** - add per-operation helpers (e.g. `create_meal_item_on_fitatu(db, client, day_date, meal_key, payload)`) that:
   - Call the new client method.
   - On success, trigger `sync_day_from_fitatu(db, client, day_date)` to reconcile the cache (write-through + re-fetch).
   - Return a Pydantic schema representing the new/updated resource.
3. **`server.py`** - register new `@mcp.tool(...)` functions, validate inputs, open session, call service helper, return envelope.

Optional fourth layer:
4. **`models.py` / `schemas.py`** - only needed if we decide to cache the Fitatu product catalog locally. Recommended NOT for MVP.

---

## Critical Unknowns

These must be resolved (via DevTools Network capture on the Fitatu web app) before implementation can begin:

1. **Write endpoint URLs and HTTP verbs** for:
   - Add a product (food/ingredient) to a meal slot on a given day.
   - Update an existing `planDayDietItemId` (e.g. change quantity or mark eaten).
   - Delete a `planDayDietItemId` from a meal.
   - Create / update / delete custom products in the user's catalog (if Fitatu supports it at all).
   - Create / update / delete custom "meals" in the sense of multi-item dishes/recipes (if Fitatu supports it).
2. **Payload shapes** (camelCase, units, required vs optional fields).
3. **Headers**: does any write require additional headers beyond `Authorization`, `API-Cluster`, the BASE_HEADERS pack? CSRF token? `X-Requested-With`?
4. **Identity semantics**:
   - Can a custom product be created standalone, or only by referencing a Fitatu catalog `productId`?
   - Are meals (breakfast/lunch/dinner/...) fixed by `meal_key`, or can users create new meal slots?
5. **Response shapes**: does the server return the new `planDayDietItemId` so we can update the cache without re-syncing?
6. **Rate limits / idempotency**: do writes have client-supplied idempotency keys?

Until (1)-(3) are known, write tool signatures and validation cannot be finalised.

---

## Risk Assessment

| Factor | Value | Level |
|--------|-------|-------|
| Files touched (MVP) | 3 (client, service, server) | Low |
| New HTTP methods | 3-6 depending on scope | Medium |
| Consumers blocked on Fitatu API discovery | All write tools | High |
| Test coverage | none in repo | High |
| Data destructive actions exposed | Yes (DELETE) | Medium |
| Cache consistency | Today TTL + post-write re-sync needed | Medium |
| Auth scope changes | None (same Bearer + API-Cluster) | Low |

### Overall: Moderate

Implementation surface is small and the existing patterns are unusually clean for grafting writes. The dominant risk is external: an undocumented third-party API. Secondary risks are the lack of tests in the repo and the fact that DELETE operations on a user's nutrition log are irreversible from the MCP side.

### Key risks
- **Reverse-engineering drift**: Fitatu can change endpoints without notice. Mitigation: keep endpoint URLs in `fitatu_client.py` constants; one place to patch.
- **Stale cache after write**: a write that succeeds but is not reflected in the local DB will mislead subsequent reads. Mitigation: every write helper must call `sync_day_from_fitatu(db, client, day_date)` on success before returning.
- **Accidental destructive calls from LLM**: tools like `delete_meal_item` are easy to misfire. Mitigation: require explicit ids (`plan_day_diet_item_id`), no "delete by name" tools, explicit per-day scope.
- **Inbound auth is shared-secret only**: every MCP caller can delete any item. Acceptable for single-user MCP, document the constraint.

---

## Recommended Approach

### Phase 0 - Discovery (blocking)
Use DevTools or Playwright MCP against pl-pl.fitatu.com web app while logged in to capture:
- Adding a food to today's lunch.
- Updating quantity on an existing entry.
- Deleting an entry.
- (Optionally) creating a custom product and saving a recipe.
Record method, URL, headers, request JSON, response JSON for each. Store the capture under `.maister/tasks/development/2026-05-21-add-write-products-meals/research/`.

### Phase 1 - MVP: write-through meal items (no local catalog)
Smallest unit of value: the ability to add / update / delete entries in a day's meal slot.

1. **`fitatu_client.py`**: add `add_meal_item(day_date, meal_key, payload)`, `update_meal_item(plan_day_diet_item_id, payload)`, `delete_meal_item(plan_day_diet_item_id)`. Each mirrors `get_day` (headers + 401 retry + RuntimeError on non-2xx). Endpoint URL constants at module top.
2. **`service.py`**: add `add_meal_item_via_fitatu(db, client, day_date, meal_key, payload)` etc. Each calls the client method, then calls `sync_day_from_fitatu(db, client, day_date)` to reconcile, then returns the affected `MealItemSchema`.
3. **`server.py`**: add `mcp_add_meal_item`, `mcp_update_meal_item`, `mcp_delete_meal_item` tools. Plain-typed args (`day_date: str, meal_key: str, product_id: int, measure_name: str, measure_quantity: float, ...`). Validate date with `_validate_day_date`. Per-day error capture pattern. Return single-day envelope: `{"day_date", "status": "ok"|"error", "item": {...}}`.

### Phase 2 - Products (custom foods)
Only if Phase 0 confirms Fitatu supports user products:
- `create_product`, `update_product`, `delete_product` HTTP methods.
- Corresponding service helpers (no DB persistence required for MVP - keep Fitatu as source of truth).
- Tools that return the new `productId` so the caller can pass it back to `add_meal_item`.

### Phase 3 - Recipes / "meals" (multi-item dishes)
Same shape as Phase 2 if Fitatu supports recipe creation. Avoid until Phase 1+2 land and Phase 0 confirms semantics.

### Cross-cutting recommendations
- Reject Pydantic models as tool parameters; build them internally inside the tool function.
- Add a tiny `_validate_day_date` helper if not already used by single-day tools (server.py already has the building block at line 111).
- Document new tools in README's tool catalogue alongside read tools.
- Log writes at `logger.info` with structured key=value (`logger.info("Tool add_meal_item called day_date=%s meal_key=%s product_id=%s", ...)`).
- Do not add new tables for MVP. Re-sync after every write is fast enough (one HTTP round trip) and avoids divergence.

---

## Next Steps

1. Run Phase 0 discovery (Playwright MCP or manual DevTools capture). This is the gating step.
2. Once endpoints are captured, hand the analysis + capture to the gap-analyzer / spec phase to produce the tool contract (names, parameters, response envelopes, error cases).
3. Implementation can then follow the three-layer pattern outlined in Phase 1 with no architectural surprises.
