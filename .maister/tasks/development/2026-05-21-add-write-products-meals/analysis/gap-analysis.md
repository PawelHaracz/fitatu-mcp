# Gap Analysis: Add MCP Write Capability (Products & Meals)

**Date**: 2026-05-21
**Task path**: `.maister/tasks/development/2026-05-21-add-write-products-meals/`
**Inputs**: `codebase-analysis.md`, `clarifications.md`, `server.py`, `fitatu_client.py`

---

## Summary

This task introduces a write surface to a previously read-only MCP server (5 new tools: `add_meal_item`, `update_meal_item`, `delete_meal_item`, `create_custom_product`, `search_products`). The on-disk integration shape is small and well-isolated — three layers (HTTP client, service, MCP tool) with established templates for each — but the dominant cost driver and risk vector is external: Fitatu's write endpoints are undocumented and must be reverse-engineered via Playwright/DevTools against `pl-pl.fitatu.com` before any production code can be written. The cache reconciliation contract (write-through + `sync_day_from_fitatu`) is already feasible with the existing diff logic. Destructive operations on a user's nutrition log (DELETE, UPDATE) widen the blast radius of a misfiring LLM, so input contracts must require explicit ids — never name-based lookups — and the inbound auth model (shared MCP_API_KEY) must be acknowledged as not-per-user.

- **Risk level**: medium
- **Effort estimate**: medium (low after discovery completes; medium-to-high if Fitatu rejects key flows or requires unmodeled headers like CSRF)
- **Detected characteristics**: modifies_existing_code, creates_new_entities (schemas only — Product ORM is a decision), involves_data_operations
- **Blocking precondition**: Phase 0 discovery (capture write endpoints, payloads, headers, response shapes) — without it, tool signatures, validation, and cache-reconciliation contracts cannot be finalized.

---

## Task Characteristics

| Characteristic | Value | Justification |
|---|---|---|
| has_reproducible_defect | false | Greenfield feature; no defect to reproduce. |
| modifies_existing_code | true | Adds tools to `server.py`, methods to `fitatu_client.py`, helpers to `service.py`. Existing read tools remain untouched. |
| creates_new_entities | true (schemas) / decision (ORM) | New Pydantic schemas (`MealItemInput`, `ProductInput`, `ProductSearchResult`) are required. A new `Product` ORM table is a **decision** — recommendation: do not add for MVP, treat Fitatu as system of record. |
| involves_data_operations | true | CREATE / UPDATE / DELETE on remote (Fitatu) and reconciliation against local SQLite cache (DailyNutrition, MealNutrition, MealItem). |
| ui_heavy | false | MCP server only; no UI surface. (Multi-touchpoint UI / discoverability analysis intentionally skipped.) |

---

## Gaps Identified

### Missing capabilities (do not exist)

| # | Capability | Evidence |
|---|---|---|
| G1 | Add a food item to a day's meal slot on Fitatu | `fitatu_client.py:142-169` exposes only `get_day`. No POST/PUT/DELETE. |
| G2 | Update an existing `plan_day_diet_item_id` (quantity / measure / eaten flag) on Fitatu | Same: no update method on client. |
| G3 | Delete an existing `plan_day_diet_item_id` from Fitatu | Same: no delete method on client. |
| G4 | Create a custom (user-owned) product in Fitatu's catalog | No client method; no schema; no service helper. |
| G5 | Search Fitatu's product catalog by name | No client method; no schema; LLM cannot resolve product name → `product_id` to use with `add_meal_item`. |
| G6 | MCP-tool surface for any of the above | `server.py` exposes only `sync_day`, `get_day_summary`, `get_day_macros`, `get_day_meals`, `get_cache_stats` (lines 207-374). No write tools registered. |
| G7 | Validated Fitatu write endpoint contracts | Codebase contains URLs for login (`LOGIN_URL`), refresh (`REFRESH_URL`), and day GET (`DAY_URL_TEMPLATE`) — and **no write URLs whatsoever**. Endpoints, verbs, payload shapes, and response shapes are all unknown. |
| G8 | Cache reconciliation hook for writes | `sync_day_from_fitatu` exists in `service.py` but no caller pattern exists for "after writing X, refresh day Y". Helper composition needs to be designed. |
| G9 | Tool-level input validation for write contracts | `_validate_day_date` (server.py:111) and `_validate_date_range` (server.py:115) exist for dates only. No validators yet for `meal_key`, `product_id`, `measure_name`, `measure_quantity`, `eaten` flag, or custom-product payload. |
| G10 | Pydantic schemas for write inputs/outputs | `schemas.py` has read schemas only (`MacroTotals` referenced; no `MealItemInput`, `ProductInput`, `ProductSearchResult`, `WriteResult`). |

### Incomplete features (partial today)

| # | Feature | Current | Needed |
|---|---|---|---|
| I1 | Cache invalidation on data mutation | Today-only TTL (300s) refreshes today via `_is_today_stale`. Past days are static and never auto-invalidated. | Any successful write to date D (today or past) must trigger `sync_day_from_fitatu(db, client, D)` regardless of TTL, to reflect the write in subsequent reads. |
| I2 | 401-recovery pattern | Implemented for `get_day` only (lines 153-163). | Must be replicated, identically, on every new HTTP method. Risk of drift if copy-pasted; consider extracting `_request_with_retry(method, url, ...)` helper. (Decision item.) |
| I3 | Inbound auth granularity | Single shared `MCP_API_KEY` covers all `/mcp` routes (server.py:81-93). | Write tools inherit the same shared-secret model. There is no per-tool authorization, no read/write split, no audit trail of caller identity. Document the limitation; do not pretend to solve it. |
| I4 | Logging for mutations | Read tools log entry only (`logger.info("Tool X called ...")`). | Writes must log entry **and** outcome (success / status code / Fitatu response id), to make destructive actions auditable in stdout. |

### Behavioral changes needed

| # | Change | From | To |
|---|---|---|---|
| B1 | Trust boundary of MCP tools | Read-only proxy (idempotent, safe for retry) | Write proxy with non-idempotent and destructive ops. Tool descriptions must say so plainly so LLM planners do not retry blindly. |
| B2 | Cache invariant | "Cache may lag remote by up to TTL on today; past days are immutable from MCP's perspective." | "Local SQLite is system-of-record after writes; every successful write reconciles its affected day via `sync_day_from_fitatu`." |
| B3 | Error envelope for single-day writes | Read tools use per-day envelope `{day_date, error}` inside a multi-day envelope. | Single-day write tools should return a flat single-day envelope `{day_date, status, item|product|error}` — multi-day range envelopes do not apply to writes. |

---

## Integration Points

Files/modules touched (additive unless noted):

1. **`/Users/pawelharacz/src/private/fitatu-mcp/fitatu_client.py`** — add HTTP methods:
   - `add_meal_item(day_date, meal_key, payload) -> dict`
   - `update_meal_item(plan_day_diet_item_id, payload) -> dict`
   - `delete_meal_item(plan_day_diet_item_id) -> dict | None`
   - `create_custom_product(payload) -> dict`
   - `search_products(query, limit) -> list[dict]`
   - URL constants at module top alongside `LOGIN_URL` / `DAY_URL_TEMPLATE`.
   - Consider extracting a `_request(method, url, ...)` helper to centralize the 401-refresh-retry pattern (decision; see D5).

2. **`/Users/pawelharacz/src/private/fitatu-mcp/service.py`** — add per-operation service helpers, each returning a typed result and calling `sync_day_from_fitatu(db, client, day_date)` on success when the operation affects a date:
   - `add_meal_item_via_fitatu(db, client, day_date, meal_key, payload)`
   - `update_meal_item_via_fitatu(db, client, day_date, plan_day_diet_item_id, payload)`
   - `delete_meal_item_via_fitatu(db, client, day_date, plan_day_diet_item_id)`
   - `create_custom_product_via_fitatu(client, payload)` (no day affected; no re-sync needed)
   - `search_products_via_fitatu(client, query, limit)` (read; no re-sync)

3. **`/Users/pawelharacz/src/private/fitatu-mcp/server.py`** — register 5 `@mcp.tool(...)` functions following the `mcp_sync_day` template:
   - `mcp_add_meal_item`, `mcp_update_meal_item`, `mcp_delete_meal_item`, `mcp_create_custom_product`, `mcp_search_products`.
   - Plain Python types for parameters (no Pydantic — confirmed anti-pattern at codebase-analysis.md:117).
   - `_validate_day_date` for date inputs; new lightweight validators for `meal_key` and numeric ranges.

4. **`/Users/pawelharacz/src/private/fitatu-mcp/schemas.py`** — add input/output schemas:
   - `MealItemInput`, `MealItemUpdateInput`, `ProductInput`, `ProductSearchResult`, `WriteResult` (or per-op result types).
   - Pydantic v2 conventions (`str | None = None`, `Field(default_factory=list)`).

5. **`/Users/pawelharacz/src/private/fitatu-mcp/models.py`** — **decision-gated** (see D1). MVP recommendation: no changes. If user wants a local Product cache, add a `Product` table with `Mapped[]` + `UniqueConstraint(product_id, owner_user_id)`; otherwise skip.

6. **`/Users/pawelharacz/src/private/fitatu-mcp/README.md`** — add the 5 new tools to the tool catalogue with destructive-action warnings.

7. **`/Users/pawelharacz/src/private/fitatu-mcp/.env.example`** — possibly add toggles (e.g. `FITATU_WRITE_ENABLED`, `FITATU_ALLOW_DELETE`) if we gate writes behind env flags (decision D6).

8. **New file: `.maister/tasks/development/2026-05-21-add-write-products-meals/analysis/fitatu-api-discovery.md`** — output of the Playwright discovery sub-phase. Required input for spec writing.

### Layer separation honored

Read tools remain untouched. The diff in `persist_day_summary` already reconciles by `plan_day_diet_item_id`, so post-write re-sync slots straight into existing infrastructure without additive-diff changes.

---

## Data Lifecycle Analysis

### Entity: MealItem (a logged food in a day's meal slot)

Local persistence model already exists (`models.MealItem`, keyed by `plan_day_diet_item_id`). The gap is at the **mutation** layer — current code only mirrors Fitatu state, never originates it.

| Operation | Backend (Fitatu) | Backend (local) | MCP tool | User access (LLM/n8n) | Status |
|---|---|---|---|---|---|
| CREATE meal item | Unknown endpoint — discovery required | `persist_day_summary` re-runs after re-sync; new `plan_day_diet_item_id` appears via additive diff | Missing (`mcp_add_meal_item` to be added) | Missing | INCOMPLETE — requires Phase 0 + implementation |
| READ meal item | `GET /api/diet-and-activity-plan/{uid}/day/{date}` (`DAY_URL_TEMPLATE`) | `_load_or_sync_day` + `db_day_to_schema` | `get_day_meals`, `get_day_summary` | OK | COMPLETE |
| UPDATE meal item | Unknown endpoint — discovery required | Re-sync rewrites row via diff (PK preserved on update) | Missing (`mcp_update_meal_item`) | Missing | INCOMPLETE |
| DELETE meal item | Unknown endpoint — discovery required | Re-sync removes orphaned row (additive diff handles deletion if `persist_day_summary` is full-replace per day; **verify** before relying on it) | Missing (`mcp_delete_meal_item`) | Missing | INCOMPLETE |
| SEARCH products | Unknown endpoint — discovery required | No local product cache (by design) | Missing (`mcp_search_products`) | Missing | INCOMPLETE |
| CREATE custom product | Unknown endpoint — discovery required | No local persistence (by recommendation) | Missing (`mcp_create_custom_product`) | Missing | INCOMPLETE |

**Completeness (post-implementation, assuming discovery succeeds)**: 100% for the MVP-defined surface.
**Completeness (today)**: ~25% — only READ is operable.
**Orphaned operations**: None at the read layer. Writes are *globally missing*, not orphaned (no read/write asymmetry to debug — both ends will land in the same change).
**Re-sync subtlety**: confirm in implementation that `persist_day_summary` removes deleted items (not just no-ops on missing keys); if not, DELETE re-sync will leave a stale row and require a service-layer compensating delete. Worth a targeted test before declaring the cache invariant satisfied.

### Entity: Product (Fitatu catalog item)

| Operation | Backend (Fitatu) | Local | MCP tool | Status |
|---|---|---|---|---|
| CREATE custom product | Unknown — discovery | None (Fitatu = source of truth) | Missing | INCOMPLETE |
| READ product (search) | Unknown — discovery | None | Missing | INCOMPLETE |
| UPDATE / DELETE product | Out of MVP scope (clarifications.md restricts to "create_custom_product") | None | Out of scope | DEFERRED |

UPDATE/DELETE of custom products are intentionally **out of MVP scope** per `clarifications.md` Q1. Flag as a follow-up — leaving CREATE without UPDATE/DELETE is technically an orphaned lifecycle (user can create products via MCP but cannot fix or remove their mistakes via MCP, only via Fitatu web app). Acceptable for MVP given the small blast radius (catalog growth, not corrupted nutrition logs) and the safety valve that the Fitatu web app still works. **Document this explicitly**, do not silently ship.

---

## Risk Assessment

| Risk | Likelihood | Impact | Level | Mitigation |
|---|---|---|---|---|
| Fitatu write endpoints differ from assumptions (verb, payload shape, required headers like CSRF) | High | High (blocks implementation) | High | Phase 0 discovery is a hard precondition; capture full request/response in `fitatu-api-discovery.md` before spec phase. |
| Fitatu rejects programmatic writes (rate-limit, captcha, signed request, app-id mismatch) | Medium | High | Medium-High | Mirror existing `app-uuid` / `api-key` / `api-secret` / `app-version` headers exactly. If rejected: surface clearly, do not silently retry. |
| Cache divergence after write (re-sync fails, write succeeds) | Medium | Medium | Medium | On re-sync failure after a successful write, return a structured warning in the tool response (`{status: "ok_remote_only", warning: "..."}`) — do NOT raise; the write happened. |
| LLM misfires destructive call (delete wrong item) | Medium | High | Medium-High | Require explicit `plan_day_diet_item_id` (no name lookup). Tool descriptions explicitly mark destructive. Optional env-flag gate (D6). |
| `persist_day_summary` does not delete rows on day re-sync (only adds/updates) | Unknown — verify | Medium | Medium | Add a quick implementation-time test: re-sync after a DELETE and assert the row is gone. If not, fix in service layer. |
| Shared MCP_API_KEY = any caller can mutate any data | High (by design) | Medium (single-user MCP) | Medium | Document constraint in README + tool description. Out of MVP scope to fix. |
| Drift between 5 copies of 401-retry logic across new client methods | High if naive copy-paste | Medium | Medium | Extract `_request(method, url, ...)` helper or accept duplication consciously (D5). |
| Fitatu changes endpoints between discovery and ship | Low | Medium | Low | Endpoint URLs live as module-level constants in `fitatu_client.py`; one-line patch surface. |
| Multi-day writes (caller logs the same item across multiple dates) | Low (out of MVP) | Low | Low | Tools are explicitly single-day for MVP; document. |
| Time zone / day boundary confusion (Fitatu day = local day? UTC?) | Medium | Medium | Medium | Capture and document during Phase 0; date inputs are already `YYYY-MM-DD` strings, no TZ in transit. Decide if "today" semantics for cache TTL agree with Fitatu's. |

### Overall risk

**medium** — Implementation surface is small, patterns are clean, and the additive-diff cache reconciler is already in place. The risk is concentrated externally (Fitatu API discovery) and in the destructive nature of two of the five operations. Both are mitigable: Phase 0 collapses the API unknown to evidence; explicit-id requirements + structured logging contain destructive blast radius.

---

## Issues Requiring Decisions

### Critical (must decide before implementation begins)

#### D1 — Add a local `Product` ORM table or treat Fitatu as system of record for products?

- **Issue**: `create_custom_product` returns a `product_id`. `search_products` returns results. If we cache locally, we own a new schema, migrations, retention, and divergence risk. If we don't, every search hits Fitatu.
- **Options**:
  - (a) **No local Product table** — Fitatu is source of truth; every `search_products` is a passthrough; `create_custom_product` returns the upstream `product_id` directly. *(Codebase-analysis recommendation.)*
  - (b) Add `Product` table with `product_id`, `name`, `owner_user_id`, basic macros. Cache search results and custom products locally. Supports offline lookup and reduces Fitatu round-trips.
- **Recommendation**: (a) for MVP.
- **Rationale**: Aligns with the existing "Fitatu = source of truth" pattern; avoids adding migration story (no Alembic in repo); keeps surface minimal. Re-evaluate after MVP if search latency or quota becomes a problem.
- **Blocking**: Yes — affects schema/model files in the implementation phase.

#### D2 — How to handle write success + cache re-sync failure?

- **Issue**: Two-step operation (write to Fitatu, then re-sync). If the write succeeds but the re-sync fails (transient 5xx, timeout), what does the tool return?
- **Options**:
  - (a) Return `{status: "ok_remote_only", warning: "cache_sync_failed: ..."}`. The Fitatu write is authoritative; the cache will catch up on the next read of that day.
  - (b) Raise — surface as a tool error, force the caller to retry. Risks duplicate writes.
  - (c) Retry re-sync inline (max 2 attempts), then fall back to (a).
- **Recommendation**: (a), with a single inline retry pass (light version of (c)).
- **Rationale**: The write already happened; raising would push callers toward retries that duplicate writes (since most write endpoints are non-idempotent). Documenting the warning preserves correctness while making the lag visible to the LLM/operator.
- **Blocking**: Yes — defines tool response envelope, which the spec phase must lock down.

#### D3 — Are writes allowed on past days, or only today?

- **Issue**: Fitatu may allow editing yesterday's lunch; the MCP could allow it too. But destructive ops on past data have higher consequence (the user has acted on that data already, e.g. inferred macros).
- **Options**:
  - (a) Allow writes on any date Fitatu accepts. No MCP-side restriction.
  - (b) Restrict writes to today only.
  - (c) Restrict writes to a configurable window (e.g. today + N past days).
- **Recommendation**: (a) — match Fitatu's behavior; do not invent a stricter policy.
- **Rationale**: The MCP is a thin proxy. Imposing a date window adds complexity and surprises power users. The destructive-action mitigation lives in "require explicit id," not in "narrow the date window."
- **Blocking**: Yes — affects validation logic in tool layer.

#### D4 — Confirmation gate for DELETE operations?

- **Issue**: `delete_meal_item` is the most consequential write. An LLM that misreads context could delete the wrong item irreversibly (from MCP's perspective; user can re-add via Fitatu web, but only if they notice).
- **Options**:
  - (a) No extra gate — explicit `plan_day_diet_item_id` is sufficient.
  - (b) Require a second parameter `confirm: bool = false` that must be `true` for the call to proceed. Adds friction; LLMs will set it true reflexively.
  - (c) Env flag `FITATU_ALLOW_DELETE` defaulting to `false`. Self-hoster opts in.
- **Recommendation**: (a) with a "destructive — requires explicit plan_day_diet_item_id" warning baked into the tool description; **add** (c) as a defensive default for new deployments.
- **Rationale**: (b) is theater — LLMs auto-confirm. (c) is one-line, low cost, and aligns with the secure-by-default ethos. (a) keeps the API clean once a deployer has decided they want write access.
- **Blocking**: Yes — affects tool registration and env config.

### Important (should decide; defaults can carry the MVP)

#### D5 — Centralize the 401-retry pattern, or copy it?

- **Issue**: The `get_day` 401-recovery pattern (`fitatu_client.py:155-163`) will be duplicated 5 times. Drift risk if Fitatu adjusts auth handling.
- **Options**:
  - (a) Copy the pattern into each new method (matches current style).
  - (b) Extract `_request(method, url, headers_extra=None, **kwargs)` that handles the 401 path centrally and refactor `get_day` to use it.
- **Default**: (a) for the first PR (keeps the diff small and behavior identical), then refactor in a follow-up once all 5 methods exist and we can see the actual drift surface.
- **Rationale**: Avoid premature abstraction; refactor with evidence after the 5 methods are real.
- **Blocking**: No.

#### D6 — Feature-flag writes behind an env var?

- **Issue**: Self-hosters may want to deploy this image as a strictly read-only MCP. Compiling in write tools but disabling them at runtime is a one-flag toggle.
- **Options**:
  - (a) No flag — writes always registered.
  - (b) `FITATU_WRITE_ENABLED=false` (default) gates registration of write tools.
- **Default**: (b).
- **Rationale**: Defensive default for a destructive surface; one-line config in `server.py` (`if FITATU_WRITE_ENABLED: @mcp.tool(...)`). Matches D4(c) philosophy.
- **Blocking**: No, but easier to introduce now than retrofit.

#### D7 — Tool result envelope: single-day flat, or multi-day-shaped for consistency?

- **Issue**: Existing read tools wrap results in `_range_envelope(start_date, end_date, days=[...])`. Write tools operate on one day at a time. Mimicking the envelope adds noise; deviating adds inconsistency.
- **Options**:
  - (a) Single-day flat envelope: `{day_date, status, item|product|error}`.
  - (b) Multi-day envelope with `day_count: 1`: `{start_date, end_date, day_count: 1, days: [{...}]}`.
- **Default**: (a).
- **Rationale**: Writes are single-target operations; the multi-day envelope is semantically misleading. The two surfaces (read range, write single) can legitimately diverge.
- **Blocking**: No.

#### D8 — `search_products` scope: user's custom products only, full catalog, or both?

- **Issue**: Fitatu search likely returns both global catalog and user-owned items. LLMs adding meal items need both. Decision affects whether we expose a single `query` parameter or also a `scope: "custom" | "catalog" | "all"`.
- **Options**:
  - (a) Single `query` parameter; results include both, marked with an `is_custom` flag if Fitatu surfaces it.
  - (b) Add `scope` parameter with the three values.
- **Default**: (a). Refine after discovery.
- **Rationale**: Keep the surface narrow until evidence demands otherwise. Defer based on actual Fitatu response shape.
- **Blocking**: No (defer to discovery).

#### D9 — Historical write log (audit trail) in SQLite?

- **Issue**: Once writes exist, debuggability is harder. A small append-only table `mcp_write_log(id, ts, user_id, tool_name, args_json, result_status, fitatu_response)` would make incidents traceable.
- **Options**:
  - (a) No log — rely on stdout logs only.
  - (b) Add a write-log table.
- **Default**: (a) for MVP; stdout logging is already structured.
- **Rationale**: Adds an ORM table and persistence concerns; not justified until an incident demands it. Revisit after MVP.
- **Blocking**: No.

#### D10 — `meal_key` validation: enum or free-form?

- **Issue**: Fitatu meals are typically `breakfast`, `second_breakfast`, `lunch`, `snack`, `dinner`, `supper` (Polish app conventions). Should we validate the set, or pass through whatever the caller supplies?
- **Options**:
  - (a) Validate against the known set; reject unknown values with a helpful error.
  - (b) Pass through; let Fitatu reject.
- **Default**: (a) — derive the set from `models.MealNutrition.meal_key` values seen in cache, plus what discovery confirms.
- **Rationale**: Tighter input contracts make LLM tool-use more reliable and produce better error messages.
- **Blocking**: No (but finalize after discovery).

#### D11 — Idempotency keys for write tools?

- **Issue**: An LLM may retry a transient failure and double-write. Idempotency keys would prevent that, but only if Fitatu supports them (unknown).
- **Options**:
  - (a) No idempotency; document at-least-once semantics.
  - (b) Generate client-side keys (UUID) and pass them in a header if Fitatu accepts.
- **Default**: (a). Capture in Phase 0 whether Fitatu accepts an `Idempotency-Key` header; revisit if yes.
- **Rationale**: Speculation without evidence. Defer.
- **Blocking**: No (defer to discovery).

---

## Recommendations

1. **Gate everything on Phase 0 discovery.** Do not write production code until `fitatu-api-discovery.md` exists with method/URL/headers/request/response captures for each of the 5 operations. The spec phase reads that file; without it, the spec is fiction.
2. **Make D1–D4 explicit before spec phase** — present these to the user; defaults are reasonable but they shape the public contract.
3. **Land D5 as "copy first, refactor once it exists" — avoid abstractions before there's a second example.**
4. **Implement D6 (write-enabled flag) at registration time, not inside the tool**, so a misconfigured deployment cannot reach the destructive code paths at all.
5. **Add a single integration check during implementation**: log a write, force a re-sync, assert the cache reflects it. This guards against the `persist_day_summary` deletion-handling unknown (Risk row 5).
6. **Update README's tool catalogue with destructive-action warnings inline** — not a footnote.
7. **Cap MVP at the 5 tools listed in clarifications.md.** Resist scope creep into product UPDATE/DELETE, recipes, or bulk operations even if Fitatu's API supports them; ship in a second iteration.

---

## Estimated Effort

| Phase | Effort | Driver |
|---|---|---|
| Phase 0: Discovery (Playwright capture) | low-medium | Manual; one session if Fitatu's web app is cooperative. Risk: hidden flows (CSRF, signed requests). |
| Phase 1: HTTP client methods (5) | low | Mirror `get_day`. Mostly mechanical once endpoints are known. |
| Phase 2: Service helpers (5) + cache invariant | low | Existing `sync_day_from_fitatu` slots in; small composition layer. |
| Phase 3: MCP tools (5) + schemas + validators | low-medium | Boilerplate per tool; new validators for `meal_key`, numeric ranges, product payload. |
| Phase 4: Docs (README) + env example + tests | low | Tests deserve their own decision — repo currently has none. Add at least one happy-path integration test per tool against a mocked Fitatu, or document the test gap honestly. |

**Total**: medium overall, dominated by discovery + tests-or-no-tests decision.

---

## Critical Issues

1. **Fitatu write endpoints unknown** — every other concern is downstream of this. Resolve via Phase 0 before spec writing.
2. **Cache deletion path unverified** — confirm `persist_day_summary` actually removes orphaned `MealItem` rows on day re-sync, not just adds/updates. Implementation-time test required.
3. **Destructive ops + shared-secret auth** — known design constraint; document explicitly and consider D4(c) + D6 as defense in depth.

---

## Scope Expansion Recommended

**No.** MVP scope (5 tools, no local Product table, no recipes) is correct and bounded. Resist expansion until Phase 0 surfaces evidence that the cost of a richer surface is justified.
