# Specification: Add MCP write capability for products and meal items

**Status**: audited 2026-05-22. Products body (B1/B2/B3/M1–M7 inline). Meal-item addendum §15 (C1/C2/C3/H1/H2/H3/M1/M2/M3/M4/L1-L5 inline). Both passes complete. Ready for plan rework + implementation. Audit reports: `verification/spec-audit-meal-items.md`.
**Created**: 2026-05-22
**Scope decision (2026-05-22 update)**: Meal items are **PRIMARY** (user's actual goal: log/edit/delete what they ate). Products are **SECONDARY** (create-on-the-fly when logging something not yet in Fitatu catalog).

---

## 0. Scope expansion 2026-05-22 — meal items unblocked

**What changed**: Original spec deferred meal-item writes because day-item endpoints were unreachable via `pl-pl.fitatu.com` probing. On 2026-05-22, the canonical web app cluster `www.fitatu.com` was identified (via Proxyman session), and its JS bundle (`bundle.e8159adf84dc9b075d26.js`, 13.5 MB) was downloaded and grepped. Day-item write endpoints, payload shape, and search endpoint are now documented in `analysis/fitatu-api-discovery.md` (sections "Day-item endpoints" and "Product search").

**Endpoints unlocked** (all on `https://www.fitatu.com/api`, NOT `pl-pl.fitatu.com`):
- `POST /diet-plan/{userId}/day-items/{date}` — bulk add items; client generates `planDayDietItemId` (UUID v1).
- `DELETE /diet-plan/{userId}/day/{date}/{mealKey}/{planDayDietItemId}?deleteAllRelatedMeals={bool}` — delete one item.
- `GET /search/food/user/{userId}` with query `{phrase, accessType[], page, limit}` — product search.
- No PUT/PATCH for individual day-items. **Update = delete-old + add-new** (matches the web app's `handleReplacePlannerItem` flow, bundle line 71287).

**Tool surface change**:
- **Added** (PRIMARY): `add_meal_item`, `update_meal_item`, `delete_meal_item`.
- **Retained** (SECONDARY): `create_custom_product`, `get_product`, `delete_custom_product`, `search_products`.

**Base URL caveat**: Existing code reads from `pl-pl.fitatu.com`. New writes must go to `www.fitatu.com/api`. Plan: keep both — existing reads stay on `pl-pl.fitatu.com` (no regression), new writes hit `www.fitatu.com`. Document the dual-host fact in `fitatu_client.py` constants.

**Audit complete (2026-05-22)**: §15 audited via `/maister:reviews-spec-audit`; 11 findings patched inline. See `verification/spec-audit-meal-items.md`.

**The original spec body (§1 onward) describes the products portion as audited.** Read it as the products portion of the expanded scope. The meal-item portion is in §15 at the bottom.

---

## 1. Goal

Extend the Fitatu MCP server with write tools so an LLM agent can:

1. Create a custom product in the user's Fitatu catalog.
2. Delete a custom product the user owns.
3. Retrieve a single product by id (helper used by other tools and by humans verifying writes).
4. Search products (custom + catalog) so an LLM can resolve product names → product_id before downstream operations (`add_meal_item` once unblocked, or just exploration).
5. Persist a local copy of custom products in SQLite so the cache layer can serve product metadata without a Fitatu round-trip when known.

Excluded from this MVP: writing meal items (add/update/delete), updating products (PUT/PATCH), creating recipes.

---

## 2. Tools (MCP surface)

All tools register unconditionally at startup. No `FITATU_WRITE_ENABLED` flag (D6). Destructive tool (`delete_custom_product`) is gated by env flag `FITATU_ALLOW_DELETE` — when `false` (default), the tool is **not registered** in the MCP catalog (D4).

**Envelope deviation from D7**: D7 specified the multi-day `_range_envelope` shape for write tools. Product writes are **not date-scoped**, so the `{start_date, end_date, day_count, days}` shape would carry zero signal. These tools use simpler `{ok, product}` / `{ok, results}` envelopes instead. D7 still applies when meal-item write tools land (deferred — see §11).

### 2.1 `create_custom_product`

**Purpose**: Create a user-owned product in Fitatu, persist locally, return canonical id.

**Parameters** (plain Python types, MCP-tool style — no Pydantic at the boundary):

| Name | Type | Required | Notes |
|---|---|---|---|
| `name` | str | yes | Non-empty. Trimmed. Max length 200 chars. |
| `energy` | float | yes | kcal per 100g. >= 0. |
| `protein` | float | yes | g per 100g. >= 0. |
| `fat` | float | yes | g per 100g. >= 0. |
| `carbohydrate` | float | yes | g per 100g. >= 0. |
| `brand` | str | no | "" treated as None. |
| `fiber` | float | no | g per 100g. -1 sentinel → omit. Default -1. |
| `sodium` | float | no | mg per 100g. -1 sentinel → omit. Default -1. |
| `salt` | float | no | g per 100g. -1 sentinel → omit. Default -1. |
| `saturated_fat` | float | no | g per 100g. -1 sentinel → omit. Default -1. |
| `sugars` | float | no | g per 100g. -1 sentinel → omit. Default -1. |
| `cholesterol` | float | no | mg per 100g. -1 sentinel → omit. Default -1. |

Rationale for `-1` sentinel: MCP tool parameters can't be `Optional[float]` cleanly in the current registration pattern (see `mcp_sync_day` template); existing tools use `""` for optional strings and the same convention extended to floats keeps boundary types simple.

**Returns** (envelope shape mirrors existing read tools — D7):

```json
{
  "ok": true,
  "product": {
    "id": 146048293,
    "name": "Homemade Hummus",
    "energy": 320,
    "protein": 8,
    "fat": 18,
    "carbohydrate": 30,
    "source": "fitatu",
    "created_at": "2026-05-22T14:33:21Z"
  }
}
```

**Behavior**:
1. Validate all inputs (server-side, fail fast with `ValueError`).
2. POST `https://pl-pl.fitatu.com/api/products` with minimum-shape payload + any provided optional macros.
3. On 201 with `{id, name}`:
   a. `client.get_product(product_id)` to capture the full 43-field record (Fitatu enriches defaults).
   b. Upsert into local `Product` ORM table.
   c. Return envelope above.
4. On non-201: raise `RuntimeError` with status code + body excerpt. No retry. (Matches `fitatu_client.py:166` convention; `ValueError` reserved for input validation per `server.py:104,108,119,122`.)
5. On post-create `GET` failure: still return the create envelope but mark `"local_cached": false`. The product exists in Fitatu — don't fail the tool.

### 2.2 `delete_custom_product`

Registered only when `FITATU_ALLOW_DELETE=true`. Otherwise tool is absent from the catalog.

**Parameters**:
| Name | Type | Required | Notes |
|---|---|---|---|
| `product_id` | int | yes | Must be a user-owned product. |

**Returns**:
```json
{ "ok": true, "deleted": true, "product_id": 146048293 }
```

**Behavior**:
1. DELETE `https://pl-pl.fitatu.com/api/products/{id}`.
2. On 200 `{"deleted":true}`: remove row from local `Product` table if present.
3. On 4xx: raise `RuntimeError`. No local deletion if Fitatu rejects.

### 2.3 `get_product`

**Purpose**: Helper. Read-only. Cache-first.

**Parameters**:
| Name | Type | Required | Notes |
|---|---|---|---|
| `product_id` | int | yes | |

**Returns**: `{ "ok": true, "product": { ... } }` with the local cached record if present, else fetched from Fitatu (and cached).

### 2.4 `search_products`

**Purpose**: Resolve product names to ids for downstream operations or exploration.

**Parameters** (D8 — scope param added):
| Name | Type | Required | Notes |
|---|---|---|---|
| `query` | str | yes | Phrase. Trimmed. Min length 2. |
| `scope` | str | no | One of `"custom"`, `"catalog"`, `"all"`. Default `"all"`. |
| `limit` | int | no | 1..50. Default 20. |

**Returns**:
```json
{
  "ok": true,
  "query": "hummus",
  "scope": "all",
  "results": [
    { "id": 146048293, "name": "Homemade Hummus", "brand": null, "energy": 320, "source": "custom" },
    ...
  ]
}
```

**Behavior — Path B is the ship; Path A is a timeboxed upgrade gate**:

- **Path B (MVP, unconditional ship)**:
  - `scope="custom"`: local SQLite `LIKE %query%` on `Product.name` (cached custom products only). Case-insensitive.
  - `scope="catalog"`: raise `RuntimeError("Catalog search not yet wired — payload contract pending. Use scope='custom' or pass exact product_id.")`.
  - `scope="all"`: return custom LIKE result with an additional `"warnings": ["catalog search unavailable — payload contract pending"]` field.
- **Path A (optional 30-min spike during implementation)**: attempt to decode `PUT /api/products/search` envelope with mobile-traffic capture or fresh payload guessing. If a 200-returning envelope is found within 30 minutes, upgrade `scope="catalog"` and `scope="all"` to forward to Fitatu. **If the spike fails, Path B is final for this PR.** No further payload guessing.

---

## 3. Local schema (`Product` table) — D1

New SQLAlchemy model in `models.py`:

```python
class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)  # Fitatu product id
    name: Mapped[str] = mapped_column(String(255), index=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    energy: Mapped[float] = mapped_column(Float)
    protein: Mapped[float] = mapped_column(Float)
    fat: Mapped[float] = mapped_column(Float)
    carbohydrate: Mapped[float] = mapped_column(Float)
    fiber: Mapped[float | None] = mapped_column(Float, nullable=True)
    sodium: Mapped[float | None] = mapped_column(Float, nullable=True)
    salt: Mapped[float | None] = mapped_column(Float, nullable=True)
    saturated_fat: Mapped[float | None] = mapped_column(Float, nullable=True)
    sugars: Mapped[float | None] = mapped_column(Float, nullable=True)
    cholesterol: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw: Mapped[str | None] = mapped_column(String, nullable=True)  # JSON dump of full Fitatu response for forensics; SQLite String has no length cap
    source: Mapped[str] = mapped_column(String(16), default="custom")  # custom | catalog
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
```

`food_type` column dropped from MVP (YAGNI — only `PRODUCT` written today; will revisit when recipes land). `Float`, `Integer`, `DateTime`, `String` all already imported in `models.py:3`.

DDL applied via existing `Base.metadata.create_all(engine)` at server startup. No Alembic. Existing DBs auto-upgrade — `create_all` is idempotent and additive.

---

## 4. FitatuClient changes

### 4.1 Pre-factor: `_request` helper (D5)

Extract the login/401-refresh-retry loop from `get_day()` into a fully-internalized helper. Also introduce `BASE_URL = "https://pl-pl.fitatu.com"` constant (replacing the three hardcoded URL constants).

```python
BASE_URL = "https://pl-pl.fitatu.com"

def _request(
    self,
    method: str,
    path: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
    accept_version: str = "v3",
) -> requests.Response:
    """All authenticated calls go through here.

    - Builds full URL from BASE_URL + path.
    - Builds headers from BASE_HEADERS + per-call Authorization (Bearer self.token) + API-Cluster (pl-pl{self.user_id}).
    - Lazy-logs in if self.token is None.
    - On 401: refresh-then-retry once; if refresh fails, re-login and retry once; final 401 raises RuntimeError.
    - Returns raw requests.Response so callers handle status-specific decoding.
    """
```

Refactor `get_day()` to call `_request("GET", f"/api/diet-and-activity-plan/{uid}/day/{date}")`. **Behavioral equivalence is the bar** — the public method signature does not change; only the internal call path. Callers no longer build headers manually.

`FitatuAuthError` is raised only by `login()` (terminal credential failure). `_request` terminal 401 (after refresh AND re-login) raises `RuntimeError("Authenticated request failed: 401 after refresh+relogin")`.

Add a unit test that exercises 401-refresh-retry against a mocked `requests.Session`.

### 4.2 New methods

```python
def create_product(self, payload: dict) -> dict: ...   # POST /api/products → {id, name}
def get_product(self, product_id: int) -> dict: ...    # GET /api/products/{id}
def delete_product(self, product_id: int) -> dict: ... # DELETE /api/products/{id}
def search_products(self, query: str, scope: str, limit: int) -> list[dict] | None:
    # Returns None if Fitatu search contract still unresolved; caller falls back to local search.
```

All four use `_request`.

---

## 5. service.py changes

New helpers:

```python
def upsert_product(db: Session, payload: dict, source: str = "custom") -> Product: ...
def delete_product(db: Session, product_id: int) -> bool: ...
def get_product_local(db: Session, product_id: int) -> Product | None: ...
def search_products_local(db: Session, query: str, scope: str, limit: int) -> list[Product]: ...
def product_to_schema(p: Product) -> ProductSchema: ...
```

The MCP tool handlers compose: client call → service upsert → schema → envelope.

---

## 6. schemas.py changes

```python
class ProductSchema(BaseModel):
    id: int
    name: str
    brand: str | None = None
    energy: float
    protein: float
    fat: float
    carbohydrate: float
    fiber: float | None = None
    sodium: float | None = None
    salt: float | None = None
    saturated_fat: float | None = None
    sugars: float | None = None
    cholesterol: float | None = None
    source: str
    created_at: datetime
```

Already-existing `MacroTotals` is unaffected.

---

## 7. server.py changes

### 7.0 Refactor: `build_app(env=os.environ)` factory

`mcp` is currently created at module import (`server.py:53-60`) and all `@mcp.tool` decorators fire at import time. Conditional registration of `delete_custom_product` based on `FITATU_ALLOW_DELETE` is not testable without `importlib.reload` gymnastics.

**Refactor**: move FastMCP construction and tool registration into a `build_app(env: Mapping[str, str] = os.environ) -> tuple[FastAPI, FastMCP]` factory. Module-level `app, mcp = build_app()` preserves the entrypoint for uvicorn. Tests call `build_app({...})` directly with different env to verify catalog membership.

This is a pure refactor of existing read tools; no behavior change. Acceptance: `pytest tests/test_server_tools.py::test_delete_absent_when_flag_false` and `::test_delete_present_when_flag_true` both pass in the same `pytest` invocation.

### 7.1 New tool registrations

Four (or three, if delete disabled) new `@mcp.tool(...)` registrations following the `mcp_sync_day` template:

- `mcp_create_custom_product`
- `mcp_delete_custom_product` (registered conditionally on `FITATU_ALLOW_DELETE`)
- `mcp_get_product`
- `mcp_search_products`

Each:
- Plain Python type signature (no Pydantic at boundary).
- `with SessionLocal() as db: _ensure_user_id() ...` body shape identical to existing tools.
- Returns dict matching envelopes in §2.
- Errors: `ValueError` for input validation (matches existing tools at server.py:104,108,119,122,146,189); `RuntimeError` for upstream Fitatu failures (matches fitatu_client.py:166). No new error class introduced. Per-item failures are NOT swallowed into envelopes (writes are single-target, unlike read-day envelopes).

---

## 8. Config (`.env.example`)

Add:

```
# Allow destructive product operations (delete_custom_product). Default false.
FITATU_ALLOW_DELETE=false
```

No new secrets. No new feature flags beyond delete-guard.

---

## 9. README update

- New "Write tools" section listing the 4 new tools (with delete annotated as opt-in).
- Note that meal-item writes are **planned but pending mobile-traffic discovery**.
- Update tool count from 5 → 9 (or 8 if delete disabled).

---

## 10. Tests

### 10.0 Test infrastructure bootstrap

`tests/` directory does not yet exist. This PR creates it. Required additions:

- `requirements-dev.txt` (new): `pytest`, `pytest-mock`, `httpx` (FastAPI TestClient transitive).
- `tests/__init__.py` (empty).
- `tests/conftest.py`:
  - Sets env stubs BEFORE importing the package: `FITATU_USERNAME`, `FITATU_PASSWORD`, `FITATU_API_SECRET`, `MCP_API_KEY`, `FITATU_DB_FILE=:memory:` (or per-test tmpfile).
  - Fixture that overrides `database.engine` to an in-memory SQLite + calls `Base.metadata.create_all(engine)`.
  - Fixture that yields a fresh `SessionLocal()` per test.
- Tests run via `pytest tests/`.

### 10.1 Test cases (priority order)

1. **`tests/test_fitatu_client.py`** — fake `requests.Session`:
   - `_request` performs login on first call.
   - `_request` retries once on 401 after refresh.
   - `create_product` posts the documented minimal shape.
   - `delete_product` issues DELETE and returns body.

2. **`tests/test_service_products.py`** — in-memory SQLite:
   - `upsert_product` insert + update idempotent.
   - `search_products_local` matches case-insensitive substring on `name`.
   - `delete_product` removes the row.

3. **`tests/test_server_tools.py`** — TestClient against FastAPI app, FitatuClient mocked:
   - `create_custom_product` happy path returns envelope; row appears in DB.
   - `delete_custom_product` not registered when `FITATU_ALLOW_DELETE=false`.
   - `search_products` falls back to local on Fitatu None.

No live Fitatu HTTP in tests.

---

## 11. Deferred scope (post 2026-05-22 scope expansion)

After meal-item endpoints were unlocked (see §0 and §15), the following remain deferred:

- `create_recipe` / `update_recipe` / `delete_recipe` — payload schema not yet captured.
- `update_custom_product` (PUT/PATCH) — endpoint allowed but payload not test-executed.
- Recipe-typed meal items — §15 covers PRODUCT-typed only for v1. Recipes use the same POST `day-items` endpoint but require fetching recipe metadata first; adds complexity for follow-up.

`add_meal_item`, `update_meal_item`, `delete_meal_item` are **NOT deferred** — see §15.

---

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| `PUT /api/products/search` contract still unknown at implementation time | Path B fallback to local SQLite LIKE for `scope=custom`; raise informative error for `catalog`/`all` with hint. |
| Fitatu silently changes product POST payload | Test creates one product and reads it back end-to-end in a smoke test (manual, not CI — needs real creds). |
| Local `Product` table drifts from Fitatu reality | MVP accepts staleness — custom products rarely change post-create. Force-refresh path is not exposed; users can `delete_custom_product` + `create_custom_product` to reset. |
| Refactor of `get_day` 401 path introduces regression | Unit test pinning login + 401-refresh-retry behavior on the new `_request` helper. |
| Race: another client deletes a product between local insert and `get_product` enrichment | `create_custom_product` returns the create envelope regardless; `local_cached: false` signals the gap. |

---

## 13. Reusability analysis

| Reuse from existing code | How |
|---|---|
| `BASE_HEADERS` + login/refresh state in `FitatuClient` | All new HTTP methods consume the same auth context via `_request`. |
| `_ensure_user_id` pattern in `server.py` | Used by every new tool. |
| `SessionLocal` context manager | Standard `with SessionLocal() as db:`. |
| `_range_envelope`-style return shape | Adapted to single-product / single-result envelopes (`{ok, product}` / `{ok, results}`). |
| `ValueError` / `RuntimeError` | Same error classes as existing tools (no new error type). |
| `Base.metadata.create_all` startup hook | Picks up the new `Product` table without migration tooling. |

No new dependencies. No new infrastructure. Strictly additive to the existing layering.

---

## 14. Acceptance criteria

1. `FitatuClient._request` exists, `BASE_URL` constant introduced, and `get_day` routes through it. Unit test asserts post-refactor `get_day` issues an HTTP request with identical URL + headers as the pre-refactor implementation (against a mocked `requests.Session`).
2. `Product` table is created on fresh start and on an existing DB without migration.
3. `create_custom_product` against a real Fitatu account creates the product, returns the expected envelope, and the product is visible via `get_product` from the local cache.
4. `delete_custom_product` is **absent** from the MCP catalog when `FITATU_ALLOW_DELETE=false`.
5. With `FITATU_ALLOW_DELETE=true`, `delete_custom_product` removes the Fitatu product and its local row.
6. `search_products(scope="custom")` returns local LIKE matches even without Fitatu search support.
7. All four new tools have unit tests covering happy path + one error.
8. README documents the four new tools and the deferred meal-item scope.

---

## 15. Meal-item write tools (PRIMARY scope, added 2026-05-22)

**Status**: audited 2026-05-22 — verdict ❌ Not ready (3 Critical, 3 High, 4 Medium, 5 Low). Patches applied inline 2026-05-22. See `verification/spec-audit-meal-items.md` for original findings.

### 15.0 Cluster-consistency assumption (must verify before merge)

**[Patch C2]** Writes go to `https://www.fitatu.com/api/...`; reads currently go to `https://pl-pl.fitatu.com/api/...`. Spec assumes these clusters share a single backing store keyed by `userId` and writes are visible to the read endpoint within one RTT. Verify with manual smoke test before merging §15:

1. POST item via `www.fitatu.com` add-items endpoint.
2. Immediately GET day via `pl-pl.fitatu.com`.
3. Confirm new `planDayDietItemId` is present.

If reads on `pl-pl` lag writes on `www`, switch `BASE_URL_READ` (existing `get_day` path) to `www.fitatu.com/api` and revalidate the existing read suite. Single-line patch in `fitatu_client.py:11`.

### 15.0a Decisions baked in (resolved from §15.8 — no follow-up required)

**[Patch M1]**

1. `add_meal_item` does NOT auto-create products. LLM caller chains explicitly: `search_products` → (if miss) `create_custom_product` → `add_meal_item`.
2. `update_meal_item` does NOT support changing `meal_key`. Caller does `delete_meal_item` + `add_meal_item` in different slot.
3. Nutrition fields ARE sent in POST body (mirror web app behavior). See §15.4.
4. UUIDs are v1 — `uuid.uuid1(node=<random per-process 48-bit>)` to avoid leaking host MAC (§15.4).
5. POSTs are NOT auto-retried on transient failure in v1. Caller re-issues if needed.

### 15.1 Tool surface

#### `add_meal_item`

**Purpose**: Log that the user ate X amount of product Y on a given date in a given meal slot.

**Parameters**:

| Name | Type | Required | Notes |
|---|---|---|---|
| `date` | str | yes | `YYYY-MM-DD`. Server timezone interpretation. |
| `meal_key` | str | yes | One of `breakfast`, `second_breakfast`, `lunch`, `dinner`, `snack`, `supper`. Validated against constant set. |
| `product_id` | int | yes | Fitatu product id (custom or catalog). Use `search_products` first to resolve a name. |
| `measure_id` | int | yes | **[Patch C1]** One of the product's `measures[].id`. Caller MUST resolve this via `get_product` (or `search_products` result) before calling. No auto-default in v1 (local `Product` schema does not cache `measures[]`; sentinel-based fallback would defeat the local-cache premise). |
| `measure_quantity` | float | yes | Number of measures (e.g. 1.5 servings, NOT grams). >0. |

**Returns**:
```json
{
  "ok": true,
  "plan_day_diet_item_id": "<uuid>",
  "date": "2026-05-22",
  "meal_key": "breakfast",
  "day": <DailyNutrition envelope after re-sync>
}
```

**[Patch L4]** **Envelope rationale**: Like product writes (§2 deviation), meal-item writes are single-target and not date-ranged. The `_range_envelope({start_date, end_date, day_count, days})` shape from D7 would carry no signal. The §2 footnote "D7 still applies when meal-item write tools land" is hereby resolved — D7 does NOT apply to meal-item write tools.

**Error cases** (ValueError unless noted):
- Invalid `meal_key` → ValueError.
- `measure_quantity <= 0` → ValueError.
- Product not found in Fitatu (404 on enrichment) → RuntimeError.
- Upstream POST fails → RuntimeError with status code.
- **[Patch M2]** Network failure with no response body → `RuntimeError("upstream request failed before response")`. Caller should NOT auto-retry (item state on server is undefined). To resolve: call `get_day_summary`, check if item appears, re-call only if missing.

**Side effects**:
1. **[Patch H3]** Resolve product for nutrition fields:
   a. Look up `Product` row by `product_id` in local SQLite.
   b. If miss, call `client.get_product(product_id)`, upsert local row, then use it.
   c. If 404 (catalog or user product no longer exists) → raise `RuntimeError("Product {id} not found in Fitatu")`.
   Outcome is the source for nutrition pro-rating in §15.4.
2. **[Patch L1]** Generate UUID v1 client-side as `planDayDietItemId` — `uuid.uuid1(node=<random per-process 48-bit>)` to avoid leaking host MAC. Random node generated once at `FitatuClient.__init__`.
3. POST to `{BASE_URL_WRITE}/diet-plan/{userId}/day-items/{date}` with `{items: [<one item payload>]}`.
4. Re-sync the day via `sync_day_from_fitatu(db, client, date)` (existing helper).
5. Return the new item's id + fresh day envelope.

#### `update_meal_item`

**Purpose**: Change the quantity (and only the quantity) of an existing meal item. For product replacement, user should call delete + add.

**Parameters**:

| Name | Type | Required | Notes |
|---|---|---|---|
| `date` | str | yes | YYYY-MM-DD; must match item's existing date. |
| `meal_key` | str | yes | Item's current meal slot. |
| `plan_day_diet_item_id` | str | yes | UUID of the item to update. |
| `new_measure_quantity` | float | yes | >0. |

**Returns** **[Patch L5]**:
```json
{
  "ok": true,
  "plan_day_diet_item_id": "<new uuid>",
  "replaced_from": "<old uuid>",
  "date": "2026-05-22",
  "meal_key": "breakfast",
  "day": <DailyNutrition envelope after re-sync>,
  "cleanup_failed": false,
  "warnings": []
}
```

**Implementation**: 2-call replace (atomic at the local level, NOT atomic on Fitatu):
1. Fetch the existing day to locate the item (need its `product_id`, `measure_id` for the new POST).
2. POST new item (new UUID, same product/measure, new quantity).
3. DELETE old item.
4. Re-sync day.

Order chosen: POST-then-DELETE. Rationale: if DELETE succeeds and POST fails, the item is lost. Reverse order means a transient duplicate (acceptable since each item has unique UUID), and worst case is two items if cleanup fails — still recoverable. Document this in the tool docstring.

**[Patch C3]** **Note on discovery-doc divergence**: `analysis/fitatu-api-discovery.md:119-122` describes the bundle's offline branch (PouchDB) as DELETE-then-POST. That ordering is an artifact of the offline-sync code path, NOT a server-imposed requirement. We deliberately invert to POST-then-DELETE for the online MCP path.

**[Patch C3]** **Pre-condition**: Server must tolerate two distinct `planDayDietItemId`s with the same `product+measure+meal+date` present simultaneously for ~1 RTT. **Verified by T9.3 against a real account before §15 merges.** If the server rejects the second POST with 409, swap to DELETE-then-POST and document the lost-write risk.

**[Patch C3]** **Failure handling**: If POST succeeds and DELETE fails (network, 4xx, 5xx), the tool returns `{ok: true, plan_day_diet_item_id: <new uuid>, replaced_from: <old uuid>, cleanup_failed: true, warnings: ["old item <uuid> must be deleted manually via delete_meal_item"], day: <synced day>}` rather than masking the leak.

**Error cases**: Same as `add_meal_item` plus item-not-found (RuntimeError if `plan_day_diet_item_id` absent from current day).

#### `delete_meal_item`

**Purpose**: Remove a logged meal item.

**Parameters**:

| Name | Type | Required | Notes |
|---|---|---|---|
| `date` | str | yes | YYYY-MM-DD. |
| `meal_key` | str | yes | Item's meal slot (server requires it in URL). |
| `plan_day_diet_item_id` | str | yes | UUID. |
| `delete_all_related_meals` | bool | no | **[Patch M3]** Default `false`. Server-side feature; exact semantic scope ("related" = which items?) NOT documented in the bundle. v1 contract: tool forwards the flag as-given; tool docstring warns callers `true` may delete more than the single specified item and recommends `false` unless user has been explicitly told what "related" means. Discovery follow-up needed before recommending `true` to LLM callers. |

**Returns**:
```json
{
  "ok": true,
  "deleted_plan_day_diet_item_id": "<uuid>",
  "day": <DailyNutrition envelope after re-sync>
}
```

**Implementation**: DELETE `{BASE_URL_WRITE}/diet-plan/{userId}/day/{date}/{meal_key}/{plan_day_diet_item_id}?deleteAllRelatedMeals={bool}`. Then re-sync the day.

**Safety**: Like `delete_custom_product`, gated by `FITATU_ALLOW_DELETE` (default `false` → tool not registered).

### 15.2 New FitatuClient methods (writes module)

**[Patch H1]** Renames §4.1's `BASE_URL` → `BASE_URL_READ` to symmetric-pair with the new write constant:

```python
# fitatu_client.py
BASE_URL_READ = "https://pl-pl.fitatu.com"        # was BASE_URL in §4.1
BASE_URL_WRITE = "https://www.fitatu.com/api"     # canonical app cluster for writes

def post_day_items(self, date: str, items: list[dict]) -> requests.Response: ...
    # POST {BASE_URL_WRITE}/diet-plan/{userId}/day-items/{date}
    # body: {"items": items}

def delete_day_item(self, date: str, meal_key: str, plan_day_diet_item_id: str,
                    delete_all_related_meals: bool = False) -> requests.Response: ...
    # DELETE {BASE_URL_WRITE}/diet-plan/{userId}/day/{date}/{meal_key}/{plan_day_diet_item_id}
    #        ?deleteAllRelatedMeals={bool}

def search_food(self, phrase: str, page: int = 1, limit: int = 40,
                access_types: list[str] | None = None) -> requests.Response: ...
    # GET {BASE_URL_WRITE}/search/food/user/{userId}?phrase=…&accessType[]=FREE&accessType[]=PREMIUM&page=…&limit=…
    # Note: search lives under www.fitatu.com/api; supersedes earlier PUT /api/products/search 400-blocked path
```

**[Patch H1]** Reuse `_request` helper (§4.1). Extend §4.1 signature with one new kwarg, keeping `requests.Response` return type and existing param names:

```python
def _request(
    self,
    method: str,
    path: str,
    *,
    json: dict | None = None,             # SAME as §4.1
    params: dict | None = None,           # SAME as §4.1
    accept_version: str = "v3",           # SAME as §4.1
    base_url: str | None = None,          # NEW — defaults to module BASE_URL_READ when None
) -> requests.Response: ...
```

When `base_url` is `None`, uses `BASE_URL_READ` (existing `pl-pl.fitatu.com`). When provided, uses given base. New write methods pass `base_url=BASE_URL_WRITE`. Return type stays `requests.Response`; write methods handle status checking the same way `create_product` does in §4.2.

### 15.3 New service helpers

**[Patch L2]** The `meal_key` whitelist is the constant `MEAL_KEYS_VALID = frozenset({"breakfast", "second_breakfast", "lunch", "dinner", "snack", "supper"})` (also referenced by §15.1, CONTEXT.md, discovery doc). Defined once in `service.py` (or new `constants.py`) and reused by all three meal-item tools.

```python
# service.py
MEAL_KEYS_VALID = frozenset({"breakfast", "second_breakfast", "lunch", "dinner", "snack", "supper"})

def add_meal_item(db: Session, client: FitatuClient, date: str, meal_key: str,
                  product_id: int, measure_id: int, measure_quantity: float) -> dict: ...

def update_meal_item(db: Session, client: FitatuClient, date: str, meal_key: str,
                     plan_day_diet_item_id: str, new_measure_quantity: float) -> dict: ...

def delete_meal_item(db: Session, client: FitatuClient, date: str, meal_key: str,
                     plan_day_diet_item_id: str, delete_all_related_meals: bool = False) -> dict: ...
```

Each helper:
1. Validates inputs (meal_key whitelist, date format, positive quantity).
2. For add: builds the item payload (see §15.4 below).
3. Calls the corresponding `FitatuClient` method.
4. Calls `sync_day_from_fitatu(db, client, date)` to refresh local cache.
5. Returns the envelope.

### 15.4 Item payload shape (POST day-items body)

From bundle reverse-engineering (`generatePlannerItemDataFromProduct`, bundle line 71148-71172 + measure helpers), the PRODUCT-typed meal item shape:

```json
{
  "planDayDietItemId": "<uuid v1, client-generated>",
  "itemId": 12345,
  "foodType": "PRODUCT",
  "type": "PRODUCT",
  "measureId": 1,
  "measureQuantity": 1.5,
  "meal": "breakfast",

  // Nutrition fields (sent — see Decision below)
  "weight": 150.0,
  "energy": 89.0,
  "protein": 1.1,
  "fat": 0.3,
  "carbohydrate": 22.8,
  "fiber": 2.4,
  "sugars": 12.2,
  "salt": 0.0
}
```

**[Patch H2]** **Decision**: Mirror the web app — send the FULL computed nutrition payload (`energy`, `protein`, `fat`, `carbohydrate`, `fiber`, `sugars`, `salt`, `weight`) populated from the Product's per-100g macros pro-rated by `measureQuantity × measure.weightPerUnit`. Required because:

1. The web bundle's `generatePlannerItemDataFromProduct` always sends them (`fitatu-api-discovery.md:91, 203`).
2. Even if the server accepts a minimum shape, the day GET returns whichever nutrition source the server cached, risking a silent divergence between local cache and server view.
3. The post-write re-sync (§15.3 step 4) is the safety net against any client/server compute drift.

**Computation**: For each nutrition field `X`, send `value_X = product.X_per_100g × (measure.weightPerUnit × measureQuantity) / 100`. The `measure.weightPerUnit` comes from `get_product(product_id).measures[].weightPerUnit` (one fetch if not in local cache; see §15.1 Side effects step 1).

**[Patch L1]** **UUID v1** generation: `uuid.uuid1(node=<random 48-bit>)`. Bundle uses node uuid v1 (`generateUuid`); standard Python `uuid.uuid1()` includes the host MAC, leaking it to Fitatu. Compute a random node once at `FitatuClient.__init__` and pass it on every `uuid1(node=…)` call. Mirrors v1 shape (timestamp + node) without privacy leak.

### 15.5 Configuration

**[Patch M4]** Add to `.env.example`; thread through `build_app(env)` factory (§7.0) NOT module-import-time reads:

```bash
# Optional overrides; defaults baked into fitatu_client.py module constants.
FITATU_BASE_URL_READ=https://pl-pl.fitatu.com    # overrides BASE_URL_READ default; "/api" appended by client
FITATU_BASE_URL_WRITE=https://www.fitatu.com     # overrides BASE_URL_WRITE default; "/api" appended by client (DO NOT include suffix)
FITATU_ALLOW_DELETE=false                        # gates both delete_custom_product AND delete_meal_item
```

`FitatuClient.__init__` accepts optional `base_url_read` and `base_url_write` kwargs (default to module constants `BASE_URL_READ` / `BASE_URL_WRITE`). `build_app(env)` reads the env vars and passes them to the constructor. This keeps `fitatu_client.py` env-pure and unit-testable.

### 15.6 Tests (minimum)

Group 9 (NEW — append to plan):

- T9.1: `add_meal_item` happy path. Mock `_request` to capture URL + body. Assert `planDayDietItemId` is a valid UUID v1, body shape matches §15.4, `sync_day_from_fitatu` was called.
- T9.2: `add_meal_item` rejects invalid `meal_key`.
- T9.3: `update_meal_item` issues POST-then-DELETE in that order.
- T9.4: `delete_meal_item` URL composition with `delete_all_related_meals=true`.
- T9.5: `delete_meal_item` is unregistered when `FITATU_ALLOW_DELETE=false`.

### 15.7 Acceptance criteria (in addition to §14)

9. `add_meal_item` against a real Fitatu account creates a meal item visible in the next `get_day` call.
10. **[Patch L3]** With `FITATU_ALLOW_DELETE=true`, `delete_meal_item` against a real Fitatu account removes the item; subsequent `get_day` shows it gone. With `FITATU_ALLOW_DELETE=false`, the tool is absent from the MCP catalog (see T9.5).
11. `update_meal_item` results in one item with the new quantity (NOT two items) after re-sync. Test verifies POST-then-DELETE order and server tolerance of the brief two-item state (§15.1 pre-condition).
12. README documents all 7 new tools (4 product + 3 meal-item).
13. **[Patch C2]** Manual smoke test confirms a meal item POSTed to `www.fitatu.com` is visible in immediate `pl-pl.fitatu.com` GET (cluster-consistency verification per §15.0).

### 15.8 Decisions log (post-audit 2026-05-22 — all resolved, summarized in §15.0a)

1. ✅ **RESOLVED**: `add_meal_item` does NOT auto-create products. LLM chains explicitly. (§15.0a item 1)
2. ✅ **RESOLVED**: `update_meal_item` does NOT support `meal_key` change in v1. Caller does delete+add. (§15.0a item 2)
3. ✅ **RESOLVED** (Patch H2): Send FULL nutrition fields in POST body. Mirror web app. (§15.4)
4. ✅ **RESOLVED**: UUID v1 with random node (not host MAC). (§15.0a item 4, §15.4)
5. ✅ **RESOLVED**: No auto-retry on transient POST failures in v1. Caller re-issues manually after `get_day_summary` check. (§15.0a item 5, §15.1 error cases)

**Remaining post-merge follow-ups** (not blockers):
- Idempotency contract: re-POST same `planDayDietItemId` — actual server behavior (409/200-noop/duplicate). Capture during first manual smoke test.
- Server response shape for POST `day-items` — confirm shape includes useful echo data or just 200/201.
- `delete_all_related_meals=true` semantics (M3 finding). Discovery follow-up before recommending to LLM.
- Recipe-typed meal items (post-v1).
