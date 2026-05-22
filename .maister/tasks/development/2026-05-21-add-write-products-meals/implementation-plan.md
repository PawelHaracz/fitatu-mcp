# Implementation Plan: Add MCP Write Capability for Products and Meal Items

**Spec**: `./spec.md` (audited 2026-05-22; products body + §15 meal-item addendum patches all inline)
**Scope**: Products (SECONDARY infrastructure) + meal items (PRIMARY user goal). 7 new tools total: 4 product + 3 meal-item.
**Approach**: Test-Driven (red → green per group). Strictly additive to existing layering.

**Plan revision 2026-05-22**: Originally products-only. Spec §0 + §15 expanded scope after `www.fitatu.com` bundle reverse-engineering unblocked all needed endpoints. Group 9 added for meal-item tools. Group 4 extended with day-item HTTP methods. Group 5 extended with 3 new meal-item registrations. Group 2 adds `BASE_URL_READ`/`BASE_URL_WRITE` rename + `base_url` kwarg.

---

## Overview

| Metric | Value |
|---|---|
| Total Task Groups | 9 (8 implementation + 1 testing review) |
| Total Steps | ~75 |
| Expected New Tests | 30 implementation + up to 10 review = **30-40 total** |
| New Production Files | 0 (all changes additive to existing modules) |
| New Test Files | 6 (`tests/conftest.py`, `test_fitatu_client.py`, `test_service_products.py`, `test_server_tools.py`, `test_search_products.py`, `test_meal_items.py`) |
| New Config Files | 1 (`requirements-dev.txt`) |

**Reuse anchors** (from spec §13):
- `FitatuClient.BASE_HEADERS` + login/refresh state → consumed by new `_request` helper
- `_ensure_user_id` pattern (server.py) → used by every new tool
- `SessionLocal` context manager → standard `with SessionLocal() as db:`
- `Base.metadata.create_all` startup hook → auto-picks-up new `Product` table
- `ValueError` / `RuntimeError` conventions → no new error classes

---

## Implementation Steps

---

### Task Group 1: Test Infrastructure Bootstrap
**Dependencies:** None
**Estimated Steps:** 6
**Why first:** Every subsequent group writes tests. No tests exist today (spec §10.0). Must land before any TDD step.

- [ ] 1.0 Stand up the test harness
  - [ ] 1.1 Create `requirements-dev.txt` with `pytest`, `pytest-mock`, `httpx`
    - `httpx` is transitive for FastAPI `TestClient` — declare it explicitly so dev installs are reproducible
    - Do NOT pin runtime deps here; keep `requirements.txt` untouched
  - [ ] 1.2 Create empty `tests/__init__.py`
  - [ ] 1.3 Create `tests/conftest.py` with env stubs + DB override
    - Set env BEFORE any package import: `FITATU_USERNAME`, `FITATU_PASSWORD`, `FITATU_API_SECRET`, `MCP_API_KEY`, `FITATU_DB_FILE=:memory:`
    - Fixture `in_memory_db`: creates fresh `sqlite:///:memory:` engine, calls `Base.metadata.create_all(engine)`, yields engine
    - Fixture `db_session`: yields a fresh `SessionLocal()` bound to that engine; rolls back at teardown
    - Fixture `fake_session`: returns a `MagicMock(spec=requests.Session)` for `FitatuClient` injection
  - [ ] 1.4 Write 2 smoke tests (`tests/test_bootstrap.py`)
    - `test_env_stubs_loaded`: assert `os.environ["FITATU_USERNAME"]` is set
    - `test_in_memory_db_has_tables`: assert `Base.metadata.tables` includes existing tables after `create_all`
  - [ ] 1.5 Document run command in plan: `pytest tests/test_bootstrap.py -v`
  - [ ] 1.6 Ensure bootstrap tests pass (run ONLY the 2 above)

**Acceptance Criteria:**
- `pip install -r requirements-dev.txt` succeeds in a clean venv
- `pytest tests/test_bootstrap.py` returns 2 passed, 0 failed
- No tests touch real Fitatu HTTP or real filesystem DB

---

### Task Group 2: FitatuClient `_request` Helper Refactor
**Dependencies:** 1
**Estimated Steps:** 7
**Spec ref:** §4.1, §14 AC1
**Bar:** Behavioral equivalence on `get_day`. Pure refactor — no new endpoints.

- [ ] 2.0 Extract `_request` and re-route `get_day` through it
  - [ ] 2.1 Write 4 tests in `tests/test_fitatu_client.py` (RED) using `fake_session`
    - `test_request_lazy_login_on_first_call`: `self.token is None` → `_request` triggers `login()` first
    - `test_request_retries_once_on_401_then_refresh`: first 401 → refresh → second 200; assert 2 HTTP calls + 1 refresh
    - `test_request_falls_back_to_relogin_on_refresh_failure`: 401 → refresh raises → re-login → retry succeeds
    - `test_get_day_equivalence_post_refactor`: pin pre-refactor URL `https://pl-pl.fitatu.com/api/diet-and-activity-plan/{uid}/day/{date}` + headers (Bearer, API-Cluster, Accept v3) — assert post-refactor call matches byte-for-byte against mock
  - [ ] 2.2 Add `BASE_URL_READ = "https://pl-pl.fitatu.com"` and `BASE_URL_WRITE = "https://www.fitatu.com/api"` constants at module top of `fitatu_client.py` (per spec H1 patch + §15.0)
    - Replace the three existing hardcoded URL constants with `BASE_URL_READ`
    - `FitatuClient.__init__` accepts optional `base_url_read=None`, `base_url_write=None` kwargs (default to module constants)
  - [ ] 2.3 Implement `_request(method, path, *, json=None, params=None, accept_version="v3", base_url=None)` per spec §4.1 + §15.2 H1 patch
    - When `base_url is None`, default to `self.base_url_read` (which defaults to `BASE_URL_READ`)
    - Build full URL from `base_url + path`
    - Headers: `BASE_HEADERS` + `Authorization: Bearer {self.token}` + `API-Cluster: pl-pl{self.user_id}` + `Accept: application/vnd.fitatu.{accept_version}+json` (mirror existing get_day shape)
    - Lazy login if `self.token is None`
    - On 401: refresh-then-retry once; if refresh fails, re-login + retry; final 401 → `RuntimeError("Authenticated request failed: 401 after refresh+relogin")`
    - Returns raw `requests.Response`
  - [ ] 2.4 Refactor `get_day()` to call `_request("GET", f"/api/diet-and-activity-plan/{uid}/day/{date}")`
    - Drop the inlined login/401 loop (fitatu_client.py:142-169)
    - Keep public signature + return shape identical
  - [ ] 2.5 Verify `FitatuAuthError` is raised ONLY by `login()` (not by `_request`) — spec §4.1 explicit constraint
  - [ ] 2.6 Ensure all 4 tests pass (GREEN). Run ONLY `tests/test_fitatu_client.py`
  - [ ] 2.7 Manual sanity check: search for remaining hardcoded URL strings in `fitatu_client.py` — should be zero matches except `BASE_URL`

**Acceptance Criteria:**
- 4 tests pass in `tests/test_fitatu_client.py`
- `get_day` produces byte-identical HTTP requests pre/post refactor (test 2.1.4 enforces this)
- `BASE_URL` is the single source of truth for the host
- `FitatuAuthError` semantics preserved (only `login()` raises it)

---

### Task Group 3: Local `Product` Table + Service Helpers
**Dependencies:** 1
**Estimated Steps:** 8
**Spec ref:** §3 (model), §5 (service helpers), §6 (schema), §14 AC2
**Note:** Can run in parallel with Group 2 (no shared files). Plan sequences after Group 1 only for cognitive load; if executed in parallel, no rework.

- [ ] 3.0 Add ORM model, Pydantic schema, and service-layer CRUD
  - [ ] 3.1 Write 6 tests in `tests/test_service_products.py` (RED) using `db_session` fixture
    - `test_product_table_created_by_create_all`: assert `"products"` in `Base.metadata.tables` after `create_all`
    - `test_upsert_product_insert_then_update_idempotent`: insert payload, upsert same id with changed name → row count stays 1, name updated, `updated_at > created_at`
    - `test_search_products_local_case_insensitive_substring`: insert "Homemade Hummus", "Greek Yogurt"; query `"HUM"` → returns hummus only
    - `test_search_products_local_respects_scope_custom`: insert one `source="custom"`, one `source="catalog"`; scope=`"custom"` returns only custom
    - `test_search_products_local_respects_limit`: insert 5 matching rows; `limit=2` returns 2
    - `test_delete_product_removes_row`: insert then `delete_product(id)` → row gone; returns `True`; deleting nonexistent returns `False`
  - [ ] 3.2 Add `Product` class to `models.py` per spec §3 (verbatim field list)
    - `id` PK no autoincrement (Fitatu-assigned)
    - `raw` String nullable (JSON dump of full Fitatu response; no length cap in SQLite)
    - `source` default `"custom"`, length 16
    - `created_at`/`updated_at` use `datetime.now(timezone.utc)` (matches commit 4e17c96 — no deprecated `utcnow`)
    - Drop `food_type` (YAGNI per spec §3)
    - All needed column types already imported in `models.py:3`
  - [ ] 3.3 Add `ProductSchema` to `schemas.py` per spec §6 (verbatim field list)
    - Pydantic v2, optional macros default `None`
    - Include `from_attributes = True` config so `ProductSchema.model_validate(orm_obj)` works
  - [ ] 3.4 Implement `upsert_product(db, payload, source="custom") -> Product` in `service.py`
    - Map Fitatu's 43-field response → Product columns (only the columns we keep; rest goes into `raw` as JSON string)
    - Use `db.get(Product, id)` to check existence; insert or update fields; commit + refresh
  - [ ] 3.5 Implement `get_product_local(db, product_id) -> Product | None`
  - [ ] 3.6 Implement `delete_product(db, product_id) -> bool` — returns True if a row was deleted
  - [ ] 3.7 Implement `search_products_local(db, query, scope, limit) -> list[Product]`
    - SQLAlchemy: `select(Product).where(Product.name.ilike(f"%{query}%"))`
    - `scope="custom"` adds `.where(Product.source == "custom")`
    - `scope="catalog"` adds `.where(Product.source == "catalog")`
    - `scope="all"` no source filter
    - `.limit(limit)` applied last
  - [ ] 3.8 Implement `product_to_schema(p) -> ProductSchema` helper (one-liner using `model_validate`)
  - [ ] 3.9 Ensure all 6 tests pass (GREEN). Run ONLY `tests/test_service_products.py`

**Acceptance Criteria:**
- 6 tests pass
- `Base.metadata.create_all` picks up new table without migration tooling (test 3.1.1)
- `upsert_product` is genuinely idempotent on `id` (test 3.1.2)
- All five service helpers exist with documented signatures (spec §5)

---

### Task Group 4: FitatuClient Write Methods
**Dependencies:** 2
**Estimated Steps:** 6
**Spec ref:** §4.2

- [ ] 4.0 Add `create_product` / `get_product` / `delete_product` / `search_products` to FitatuClient
  - [ ] 4.1 Write 5 tests in `tests/test_fitatu_client.py` (append; RED) using `fake_session`
    - `test_create_product_posts_minimal_shape`: assert POST to `/api/products`, body contains required macros, no `-1` sentinels leaked
    - `test_create_product_returns_id_and_name_from_201`: mock 201 `{"id": 999, "name": "Test"}` → method returns `{"id": 999, "name": "Test"}`
    - `test_create_product_raises_runtime_error_on_non_201`: mock 400 → `RuntimeError` with status + body excerpt
    - `test_get_product_issues_get_with_id`: assert GET `/api/products/{id}`, returns parsed JSON
    - `test_delete_product_issues_delete_and_returns_body`: assert DELETE `/api/products/{id}`, returns `{"deleted": true}`
  - [ ] 4.2 Implement `create_product(payload: dict) -> dict`
    - Calls `self._request("POST", "/api/products", json=payload)`
    - Asserts 201; raises `RuntimeError(f"create_product failed: {resp.status_code} {resp.text[:200]}")` otherwise
    - Returns `resp.json()` (the `{id, name}` shape from Fitatu)
  - [ ] 4.3 Implement `get_product(product_id: int) -> dict`
    - Calls `self._request("GET", f"/api/products/{product_id}")`
    - Returns `resp.json()` on 200; raises `RuntimeError` otherwise
  - [ ] 4.4 Implement `delete_product(product_id: int) -> dict`
    - Calls `self._request("DELETE", f"/api/products/{product_id}")`
    - Returns `resp.json()` on 200; raises `RuntimeError` on 4xx
  - [ ] 4.5 Implement `search_products(query: str, scope: str, limit: int) -> list[dict] | None`
    - **Updated 2026-05-22**: search now goes to `BASE_URL_WRITE` (www.fitatu.com/api) per discovery. But the legacy Path B stub stays for Group 6 simplicity. This method returns `None` (signals "use scope='custom' local LIKE in service layer"). Path A (full upstream search via `search_food`) is implemented in Group 9.5 instead — see §4.5b below.
    - Documented stub. No HTTP call. Group 9 introduces the actual `search_food` client method.
  - [ ] 4.5b Implement `search_food(phrase: str, page: int = 1, limit: int = 40, access_types: list[str] | None = None) -> list[dict]` per spec §15.2
    - GET `{BASE_URL_WRITE}/search/food/user/{userId}` with query params `phrase`, `accessType[]` (FREE by default), `page`, `limit`
    - Calls `self._request("GET", path, params={...}, base_url=self.base_url_write)`
    - Returns parsed JSON list on 200
    - Raises `RuntimeError` on non-200
  - [ ] 4.6 Ensure 6 new tests pass (GREEN). Run ONLY `tests/test_fitatu_client.py` (now 10 tests total in this file)
    - Adds `test_search_food_get_request_shape`: assert GET to `/search/food/user/{userId}` on `BASE_URL_WRITE` host with the documented query params

**Acceptance Criteria:**
- 5 new tests pass (9 total in test_fitatu_client.py)
- All four methods route through `_request` (no manual header construction)
- `search_products` returns `None` (placeholder) — explicit, documented stub
- Error semantics match existing convention: `RuntimeError` for upstream failures

---

### Task Group 5: `build_app(env)` Factory Refactor + New Tool Registrations
**Dependencies:** 3, 4
**Estimated Steps:** 8
**Spec ref:** §7.0 (refactor), §7.1 (tools), §14 AC4, AC5
**Critical:** Refactor is prerequisite for testing conditional `delete_custom_product` registration without `importlib.reload`.

- [ ] 5.0 Refactor server.py into a build_app factory and register new tools
  - [ ] 5.1 Write 7 tests in `tests/test_server_tools.py` (RED)
    - `test_build_app_returns_fastapi_and_mcp`: signature smoke test
    - `test_existing_read_tools_still_registered`: assert `mcp_sync_day`, `mcp_get_day` (existing 5) appear in `mcp._tool_manager.list_tools()` (or equivalent FastMCP introspection)
    - `test_delete_absent_when_flag_false`: `build_app({"FITATU_ALLOW_DELETE": "false", ...})` → `mcp_delete_custom_product` NOT in tool catalog
    - `test_delete_present_when_flag_true`: `build_app({"FITATU_ALLOW_DELETE": "true", ...})` → `mcp_delete_custom_product` IN catalog
    - `test_create_custom_product_happy_path`: mock `FitatuClient.create_product` → returns id; `FitatuClient.get_product` → returns full record; call tool handler; assert envelope `{ok: True, product: {...}}` AND a row exists in DB after
    - `test_create_custom_product_post_get_failure_returns_local_cached_false`: mock create OK, get raises → tool returns envelope with `"local_cached": false`, tool does NOT raise
    - `test_get_product_cache_hit_skips_fitatu`: pre-seed `Product` row; assert tool returns envelope without calling `FitatuClient.get_product`
  - [ ] 5.2 Move FastMCP construction into `build_app(env: Mapping[str, str] = os.environ) -> tuple[FastAPI, FastMCP]`
    - Preserve module-level `app, mcp = build_app()` at bottom of file for uvicorn entrypoint
    - Move all 5 existing `@mcp.tool(...)` decorators into nested registrations inside `build_app`
    - This is a PURE REFACTOR — no behavior change for existing tools (test 5.1.2 enforces this)
    - **[Patch M4]** Read `FITATU_BASE_URL_READ` and `FITATU_BASE_URL_WRITE` env vars; pass to `FitatuClient(...)` constructor as `base_url_read=`/`base_url_write=` kwargs (default to module constants on missing)
  - [ ] 5.3 Register `mcp_create_custom_product` inside `build_app` (unconditional)
    - Plain Python signature per spec §2.1 (str, float, float, ... with `-1` sentinels for optional macros)
    - Validate: `name` non-empty after `.strip()`, max 200 chars (raise `ValueError`)
    - Validate: all required macros `>= 0` (raise `ValueError`)
    - Body: build payload (strip `-1` sentinels, treat `brand=""` as None) → `client.create_product` → on success `client.get_product` → `service.upsert_product` → `service.product_to_schema` → envelope
    - On post-create get failure: catch `RuntimeError`, return `{ok: True, product: {id, name}, local_cached: False}` (do NOT raise; spec §2.1 behavior 5)
  - [ ] 5.4 Register `mcp_get_product` inside `build_app` (unconditional)
    - Cache-first: `service.get_product_local(db, id)` → if hit, return envelope
    - On miss: `client.get_product(id)` → `service.upsert_product(db, payload, source="catalog" if not previously known else preserve)` → schema → envelope
    - Note: for MVP, assume miss-then-fetched products are `source="catalog"` unless caller knew otherwise. Caller-driven source is not in MVP scope.
  - [ ] 5.5 Register `mcp_search_products` inside `build_app` (unconditional)
    - Implementation deferred to Group 6 — register handler stub here that delegates to Group 6 logic
    - Validate: `query.strip()` length >= 2 (raise `ValueError`)
    - Validate: `scope in {"custom", "catalog", "all"}` (raise `ValueError`)
    - Validate: `1 <= limit <= 50` (raise `ValueError`)
  - [ ] 5.6 Register `mcp_delete_custom_product` inside `build_app` ONLY IF `env.get("FITATU_ALLOW_DELETE", "false").lower() == "true"`
    - Body: `client.delete_product(id)` → on `{"deleted": true}` → `service.delete_product(db, id)` → envelope `{ok: True, deleted: True, product_id: id}`
    - On 4xx: `RuntimeError` propagates; local row NOT removed (spec §2.2 behavior 3)
  - [ ] 5.7 Ensure all 7 tests pass (GREEN). Run ONLY `tests/test_server_tools.py`
    - CRITICAL: tests 5.1.3 and 5.1.4 (delete flag toggle) must both pass in the same `pytest` invocation — proves refactor success
  - [ ] 5.8 Confirm uvicorn entrypoint still works: `python -c "from server import app, mcp; print(len(mcp._tool_manager.list_tools()))"` — should print 8 (5 existing + 3 new, no delete)

**Acceptance Criteria:**
- 7 tests pass
- `build_app(env)` is idempotent and stateless w.r.t. global module state (test 5.1.3 + 5.1.4 same invocation)
- All 5 existing read tools still register (test 5.1.2)
- Module-level `app, mcp = build_app()` preserved (uvicorn keeps working)
- Per-item failures NOT swallowed into envelopes — writes raise; `local_cached: false` is the only soft-fail path (spec §7.1)

---

### Task Group 6: `search_products` Path B Implementation
**Dependencies:** 5
**Estimated Steps:** 5
**Spec ref:** §2.4 (Path B = ship; Path A = optional spike)
**Scope decision:** Path B only. Path A spike NOT in this plan — explicitly deferred to backlog.

- [ ] 6.0 Wire `mcp_search_products` to Path B behavior
  - [ ] 6.1 Write 4 tests in `tests/test_search_products.py` (RED)
    - `test_search_scope_custom_returns_local_like_matches`: seed 3 custom products, query → returns matches in envelope shape `{ok, query, scope, results}`
    - `test_search_scope_catalog_raises_runtime_error`: assert `RuntimeError` with message containing "Catalog search not yet wired"
    - `test_search_scope_all_returns_custom_with_warning`: returns local custom results + `"warnings": ["catalog search unavailable — payload contract pending"]`
    - `test_search_query_too_short_raises_value_error`: `query="a"` → `ValueError` (boundary validation from Group 5.5)
  - [ ] 6.2 Implement `mcp_search_products` body (replacing 5.5 stub)
    - `scope="custom"`: `service.search_products_local(db, query, "custom", limit)` → map to result dicts `{id, name, brand, energy, source}` → envelope
    - `scope="catalog"`: `raise RuntimeError("Catalog search not yet wired — payload contract pending. Use scope='custom' or pass exact product_id.")` (verbatim from spec §2.4)
    - `scope="all"`: call `search_products_local(db, query, "all", limit)` → envelope + `"warnings": ["catalog search unavailable — payload contract pending"]`
  - [ ] 6.3 Document Path A as deferred in `implementation/notes.md` (one-paragraph backlog entry):
    - "Path A spike: decode `PUT /api/products/search` envelope via mobile-traffic capture. If successful, replace `client.search_products` stub (Group 4.5) to forward queries; swap Path B fallback for catalog/all scopes. Out of scope for this PR."
  - [ ] 6.4 Ensure all 4 tests pass (GREEN). Run ONLY `tests/test_search_products.py`
  - [ ] 6.5 Cross-check: `search_products_local` from Group 3 is now exercised end-to-end by these tests (no dead service code)

**Acceptance Criteria:**
- 4 tests pass
- `scope="custom"` returns local LIKE matches without any Fitatu call (spec §14 AC6)
- `scope="catalog"` raises with the documented hint message (verbatim)
- `scope="all"` returns custom-only results plus the documented warnings array
- Deferred Path A captured in `notes.md`

---

### Task Group 7: Documentation
**Dependencies:** 5, 6
**Estimated Steps:** 4
**Spec ref:** §8, §9

- [ ] 7.0 Update user-facing docs and config templates
  - [ ] 7.1 Update `.env.example` — add the `FITATU_ALLOW_DELETE=false` block per spec §8 (verbatim including the comment line)
  - [ ] 7.2 Update `README.md` — new "Write tools" section
    - List the 4 new tools (3 unconditional + `delete_custom_product` annotated "opt-in, requires `FITATU_ALLOW_DELETE=true`")
    - Bump tool count: 5 → 9 (or 8 if delete disabled — call out both)
    - One-sentence note: meal-item writes (`add_meal_item` / `update_meal_item` / `delete_meal_item`) are planned but pending mobile-traffic discovery — link to spec §11
    - One-sentence note: `search_products(scope="catalog")` currently raises — payload contract pending
  - [ ] 7.3 Update `README.md` dev-setup section: mention `pip install -r requirements-dev.txt` for running `pytest tests/`
  - [ ] 7.4 Verify no other docs reference the old "5 tools" count (grep `README.md`, `.maister/` docs)

**Acceptance Criteria:**
- `.env.example` contains `FITATU_ALLOW_DELETE=false` with explanatory comment
- README lists all 4 new tools with delete clearly marked opt-in
- README mentions deferred meal-item scope with rationale link
- Tool count stale references purged

---

### Task Group 8: Test Review & Gap Analysis
**Dependencies:** All previous groups (1-7, 9)
**Estimated Steps:** 5
**Test budget:** Max 10 additional tests (per implementation-planner guidelines)

- [ ] 8.0 Review test coverage, fill critical gaps, run feature suite end-to-end
  - [ ] 8.1 Inventory existing tests (~30 total across Groups 1-6, 9):
    - Group 1: 2 bootstrap
    - Group 2: 4 client `_request`
    - Group 3: 6 service products
    - Group 4: 6 client writes (incl. search_food shape)
    - Group 5: 7 server tools
    - Group 6: 4 search_products
    - Group 9: 5 meal-item write tools
  - [ ] 8.2 Analyze gaps for THIS feature only (do NOT expand into pre-existing read tools):
    - Likely candidates: `create_custom_product` input validation edge cases (empty name after strip, negative macros, name > 200 chars), `delete_custom_product` end-to-end (Fitatu success + local row gone), `upsert_product` preserves `raw` JSON column, search envelope shape lock-in
  - [ ] 8.3 Write up to 10 strategic additional tests (NOT 10 by default — write only what's missing)
    - Suggested 5-8 strategic adds:
      - `test_create_validation_empty_name_after_strip`
      - `test_create_validation_negative_energy`
      - `test_create_validation_name_too_long`
      - `test_delete_custom_product_end_to_end_with_flag_on` (uses `FITATU_ALLOW_DELETE=true` env)
      - `test_upsert_product_persists_raw_json_full_response`
      - `test_search_envelope_shape_lock` (snapshot the exact dict keys for downstream agent consumers)
      - `test_get_product_falls_through_to_fitatu_on_local_miss`
  - [ ] 8.4 Run the full feature test suite: `pytest tests/` — expect 24-34 tests
    - Do NOT run any unrelated suites (none exist anyway)
    - All must pass
  - [ ] 8.5 Update implementation-plan.md "Expected New Tests" if final count differs from 24-34 range

**Acceptance Criteria:**
- `pytest tests/` returns 30-40 passed, 0 failed
- No more than 10 additional tests added beyond Groups 1-6 and 9
- Every spec §14 + §15.7 acceptance criterion maps to at least one test ID
- No tests touch live Fitatu HTTP

---

### Task Group 9: Meal-Item Write Tools (PRIMARY scope)
**Dependencies:** 4 (client `_request` + `post_day_items`/`delete_day_item`/`search_food`), 5 (`build_app` factory)
**Estimated Steps:** 10
**Spec ref:** §15 (all subsections), §15.7 (AC #9-#13)
**Why:** This is the user's actual goal (PRIMARY) — log/edit/delete what they ate.

- [ ] 9.0 Add meal-item HTTP methods + service helpers + 3 MCP tool registrations
  - [ ] 9.1 Write 5 tests in `tests/test_meal_items.py` (RED) per spec §15.6
    - `test_add_meal_item_happy_path`: mock `client.get_product` returns product with `measures[]`; mock `_request` for POST + sync; assert UUID v1 present in body, `meal` field = "breakfast", `measureId`/`measureQuantity` propagated, nutrition fields pro-rated from product macros (spec §15.4 formula), `sync_day_from_fitatu` called.
    - `test_add_meal_item_rejects_invalid_meal_key`: `meal_key="brunch"` → `ValueError` mentioning whitelist.
    - `test_update_meal_item_post_then_delete_order`: mock day fetch with one item; mock POST returns success; mock DELETE returns success. Assert order = (1) POST, (2) DELETE. Capture call order via `mock_calls` ordering.
    - `test_delete_meal_item_url_composition`: `delete_all_related_meals=True` → assert DELETE URL contains `?deleteAllRelatedMeals=true`.
    - `test_delete_meal_item_unregistered_when_flag_false`: `build_app({"FITATU_ALLOW_DELETE": "false", ...})` → `mcp_delete_meal_item` NOT in tool catalog.
  - [ ] 9.2 Implement `FitatuClient.post_day_items(date, items) -> requests.Response` per spec §15.2
    - `path = f"/diet-plan/{self.user_id}/day-items/{date}"`
    - Calls `self._request("POST", path, json={"items": items}, base_url=self.base_url_write)`
    - Returns raw `requests.Response`; caller checks status
  - [ ] 9.3 Implement `FitatuClient.delete_day_item(date, meal_key, plan_day_diet_item_id, delete_all_related_meals=False) -> requests.Response` per spec §15.2
    - `path = f"/diet-plan/{self.user_id}/day/{date}/{meal_key}/{plan_day_diet_item_id}"`
    - `params = {"deleteAllRelatedMeals": str(delete_all_related_meals).lower()}`
    - Calls `self._request("DELETE", path, params=params, base_url=self.base_url_write)`
  - [ ] 9.4 Add UUID v1 helper to `FitatuClient.__init__`:
    - `self._uuid_node = random.getrandbits(48) | (1 << 40)` (sets multicast bit per RFC 4122 §4.5)
    - Helper `self._gen_uuid() -> str`: `return str(uuid.uuid1(node=self._uuid_node))`
    - Per spec §15.4 L1 patch — avoids host MAC leak
  - [ ] 9.5 Implement `service.add_meal_item(db, client, date, meal_key, product_id, measure_id, measure_quantity) -> dict` per spec §15.3
    - Validate: `meal_key in MEAL_KEYS_VALID`, `measure_quantity > 0`, date matches `YYYY-MM-DD` regex
    - Resolve product (spec §15.1 step 1): local cache → fetch via `client.get_product(product_id)` → upsert → use it. 404 → `RuntimeError`
    - Find measure: `measure = next((m for m in product["measures"] if m["id"] == measure_id), None)`; if None → `ValueError("measure_id {id} not in product.measures[]")`
    - Compute nutrition (spec §15.4): `weight = measure["weightPerUnit"] * measure_quantity`; `value_X = product[X] * weight / 100` for energy/protein/fat/carbohydrate/fiber/sugars/salt
    - Build item dict per spec §15.4 shape (planDayDietItemId from `client._gen_uuid()`, itemId, foodType="PRODUCT", type="PRODUCT", measureId, measureQuantity, meal=meal_key, full nutrition)
    - Call `client.post_day_items(date, [item])`; on non-2xx → `RuntimeError("post_day_items failed: {status} {body[:200]}")`
    - Call `sync_day_from_fitatu(db, client, date)`
    - Return `{"ok": True, "plan_day_diet_item_id": uuid_str, "date": date, "meal_key": meal_key, "day": <fresh day envelope>}`
  - [ ] 9.6 Implement `service.update_meal_item(db, client, date, meal_key, plan_day_diet_item_id, new_measure_quantity) -> dict` per spec §15.1 + C3 patch
    - Validate `meal_key`, quantity > 0
    - Fetch existing day; find item by id; extract `product_id`, `measure_id` (from `productId` and `measureId`/`measureName` mapping — need to read item shape from existing `MealItem` schema or day GET)
    - POST new item via `add_meal_item` internal logic (reuse).
    - DELETE old: `client.delete_day_item(date, meal_key, plan_day_diet_item_id)`
    - If DELETE non-2xx → return envelope with `cleanup_failed: true, warnings: [...]` per spec §15.1 patch C3
    - Else → re-sync, return `{ok, plan_day_diet_item_id: new, replaced_from: old, date, meal_key, day, cleanup_failed: false, warnings: []}`
  - [ ] 9.7 Implement `service.delete_meal_item(db, client, date, meal_key, plan_day_diet_item_id, delete_all_related_meals=False) -> dict`
    - Validate meal_key
    - `client.delete_day_item(date, meal_key, plan_day_diet_item_id, delete_all_related_meals)`
    - On non-2xx → `RuntimeError`
    - `sync_day_from_fitatu(db, client, date)`
    - Return `{ok: True, deleted_plan_day_diet_item_id: id, day: <fresh>}`
  - [ ] 9.8 Register 3 MCP tools inside `build_app` (extends Group 5):
    - `mcp_add_meal_item(date, meal_key, product_id, measure_id, measure_quantity)` — unconditional
    - `mcp_update_meal_item(date, meal_key, plan_day_diet_item_id, new_measure_quantity)` — unconditional
    - `mcp_delete_meal_item(date, meal_key, plan_day_diet_item_id, delete_all_related_meals=False)` — ONLY IF `env.get("FITATU_ALLOW_DELETE", "false").lower() == "true"` (same gate as `delete_custom_product`)
    - Each tool: `_ensure_user_id(client)` → `with SessionLocal() as db: return service.<fn>(...)`. Error handling: ValueError → re-raise (FastMCP shows to caller); RuntimeError → re-raise.
  - [ ] 9.9 Ensure 5 new tests pass (GREEN). Run ONLY `tests/test_meal_items.py`
  - [ ] 9.10 Update test count expectation in Group 8 inventory

**Acceptance Criteria:**
- 5 tests pass
- 3 new MCP tools registered (2 unconditional + delete gated by flag)
- UUID v1 generation uses random node (not host MAC) — verifiable via test or grep
- `MEAL_KEYS_VALID` constant defined once and referenced from both `service.py` validation and any other consumers
- Nutrition fields computed via spec §15.4 formula (test 9.1 enforces)
- POST-then-DELETE order in `update_meal_item` (test 9.3 enforces)

---

## Execution Order

1. **Group 1**: Test Infrastructure Bootstrap (6 steps) — gate for all others
2. **Group 2**: `_request` helper refactor + BASE_URL_READ/WRITE (7 steps, depends on 1)
3. **Group 3**: Product table + service helpers (9 steps, depends on 1) — can parallel with Group 2
4. **Group 4**: FitatuClient write methods (product + search_food) (7 steps, depends on 2)
5. **Group 5**: `build_app` factory + tool registrations (8 steps, depends on 3, 4)
6. **Group 6**: `search_products` Path B (5 steps, depends on 5)
7. **Group 9**: Meal-item write tools (10 steps, depends on 4, 5) — **PRIMARY user goal**
8. **Group 7**: Docs (4 steps, depends on 5, 6, 9)
9. **Group 8**: Test review & gaps (5 steps, depends on all)

**Parallelization note:** Groups 2 and 3 touch disjoint files (`fitatu_client.py` vs `models.py`/`schemas.py`/`service.py`) and have no shared state. If executed by parallel agents, no merge conflicts expected. Group 9 cannot run in parallel with Group 6 (both edit `build_app` factory).

---

## Standards Compliance

Follow standards from `.maister/docs/standards/`:
- `global/` — always applicable
- Python/SQLAlchemy 2.0 conventions:
  - `Mapped[...]` + `mapped_column(...)` syntax (already used in models.py)
  - `datetime.now(timezone.utc)` — never `datetime.utcnow()` (commit 4e17c96)
- Pydantic v2 conventions:
  - `from_attributes = True` for ORM-backed schemas
  - `model_validate` over the v1 `from_orm`
- Existing repo conventions:
  - `ValueError` for input validation (server.py:104,108,119,122,146,189)
  - `RuntimeError` for upstream failures (fitatu_client.py:166)
  - Plain Python types at MCP tool boundary (no Pydantic) — pattern from `mcp_sync_day` (server.py:207-239)
  - Empty string `""` for optional string params; `-1` sentinel for optional float params (spec §2.1 rationale)

---

## Notes

- **TDD strict**: Every group writes its tests FIRST (RED), then implementation (GREEN). Do not invert the order.
- **Test scope discipline**: After each group, run ONLY that group's test file. Full suite runs only in Group 8.4.
- **Reuse first**: Group 4 reuses `_request` from Group 2. Group 5 reuses `upsert_product` from Group 3 and `create_product` from Group 4. Group 6 reuses `search_products_local` from Group 3.
- **No new dependencies in `requirements.txt`**: All new tests use `pytest` + `pytest-mock` + `httpx` declared in `requirements-dev.txt` only.
- **No Alembic**: `Base.metadata.create_all` handles `Product` table additively on startup (spec §3 + §14 AC2).
- **Path A spike is OPT-IN and OUT OF THIS PLAN**: If discovered during Group 4 implementation that mobile-traffic capture trivially yields the search envelope, raise a follow-up issue — do NOT bolt onto this PR.
- **`local_cached: false` is the only soft-fail envelope**: All other failures raise. Per-item failures NOT swallowed (writes are single-target; spec §7.1).
- **Mark progress**: Check off steps `[x]` as completed. Resume from first unchecked step on session re-entry.
