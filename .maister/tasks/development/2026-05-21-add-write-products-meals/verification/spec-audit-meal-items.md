# Spec Audit — §0 + §15 (meal-item write tools)

**Date**: 2026-05-22
**Spec**: `spec.md` (focus §0 scope expansion + §15 meal-item addendum)
**Discovery**: `analysis/fitatu-api-discovery.md`
**Code examined**: `fitatu_client.py`, `service.py`, `server.py`, `models.py`, `schemas.py`, `CONTEXT.md`
**Approach**: Senior-auditor skepticism; cross-reference all §15 claims against discovery + code.

---

## Summary

§15 lands the right shape (POST add / DELETE remove / replace-via-delete+POST) but is materially under-specified in five places. Three Critical (broken code or 400-loops), three High (latent footguns / hidden divergence), rest Medium/Low polish.

**Verdict: ❌ Not ready (rework needed)**. With the patches below applied (all surgical), §15 becomes implementable. Estimated patch effort: 30–45 min spec editing, no new design work.

---

## Critical findings

### C1 — `measure_id=0` sentinel unbuildable; local schema lacks measures
**Spec**: `spec.md:441` — "Defaults to product's `initialMeasure` if `0` passed (lookup happens client-side via local cache or fetch)."
**Spec §3** (`spec.md:170-191`): `Product` ORM table has no `initial_measure_*` column, no `measures` relation, no JSON column except `raw` (forensic, not queryable).
**Discovery** (`fitatu-api-discovery.md:35-36`): GET product returns `initialMeasure {key, weight, unitKey}` and `measures[ {id, name, energyPerUnit, weightPerUnit} ]`.
**Existing code**: `models.py:1-93` — `MealItem` stores `measure_name`/`measure_quantity` only; no `measure_id`. `service.py:33-50` does not extract `measureId` from day GET either.

**Why critical**: `add_meal_item(measure_id=0)` resolution path is unbuildable as specified. Local cache cannot satisfy it. Falls back to "fetch" → every add → defeats local cache premise → parameter contract becomes a lie.

**Patch options** (pick one):

**Option A (recommended)** — Drop the sentinel, force explicit ID:
```diff
- | `measure_id` | int | yes | … Defaults to product's `initialMeasure` if `0` passed (lookup happens client-side via local cache or fetch). |
+ | `measure_id` | int | yes | One of the product's `measures[].id`. Caller MUST resolve this via `get_product` before calling. No auto-default in v1; defer to v1.1. |
```

**Option B** — Keep sentinel, document mandatory fetch:
```diff
+ | `measure_id` | int | yes | … When `0` is passed, `add_meal_item` issues unconditional `GET /api/products/{id}` to read `initialMeasure.key` and selects matching entry from `measures[]`. Local `Product` table does NOT cache measures in v1; every `measure_id=0` call costs one round-trip. |
```

**Option C** — Add `initial_measure_id` + `measures_json` columns to Product schema in §3.

---

### C2 — Re-sync after write assumes pl-pl ↔ www cluster consistency; not verified
**Spec**: `spec.md:466, 515, 564-568` — "Re-sync the day via `sync_day_from_fitatu(db, client, date)`".
**Existing code**: `fitatu_client.py:11` — `DAY_URL_TEMPLATE = "https://pl-pl.fitatu.com/api/diet-and-activity-plan/{user_id}/day/{date}"`. Read goes to `pl-pl`. Write goes to `www`.

**Why critical**: Dual-host strategy keeps reads on `pl-pl`, writes on `www`. Spec never establishes consistency between clusters. If writes don't propagate to read view within ms, post-write re-sync returns stale data → caller retries → duplicate items.

**Patch (Option 1, recommended)** — Add §15.0:
```markdown
### 15.0 Cluster-consistency assumption (must verify before merge)

Writes go to `https://www.fitatu.com/api/...`; reads currently go to `https://pl-pl.fitatu.com/api/...`. Spec assumes these clusters share a single backing store keyed by `userId` and writes are visible to read endpoint within one RTT. Verify with smoke test before merging §15:

1. POST item via `www.fitatu.com` add-items endpoint.
2. Immediately GET day via `pl-pl.fitatu.com`.
3. Confirm new `planDayDietItemId` is present.

If reads on `pl-pl` lag writes on `www`, switch `BASE_URL` (existing `get_day` path) to `www.fitatu.com/api` and revalidate read suite. 14-line patch in `fitatu_client.py:11`.
```

**Patch (Option 2)** — Unify everything on `www.fitatu.com/api` upfront; smaller blast radius.

---

### C3 — POST-then-DELETE order contradicts discovery doc; needs explicit dependency call-out
**Spec** (`spec.md:483-489`): "Order chosen: POST-then-DELETE. Rationale: if DELETE succeeds and POST fails, the item is lost. Reverse order means a transient duplicate (acceptable since each item has unique UUID)…"
**Discovery** (`fitatu-api-discovery.md:119-122`): "1. DELETE … 2. POST …"

**Why critical**: Sources contradict. Spec's justification is the stronger argument BUT depends on server tolerating two items with same product+measure+date+meal_key but different UUIDs. **This is untested** (§15.8 Q5).

The bundle's `handleReplacePlannerItem` actually operates against PouchDB (offline) — the cited DELETE-then-POST order is artifact of offline-sync code, not server-imposed.

**Patch** — Add to §15.1 update_meal_item Implementation:
```markdown
**Note on discovery-doc divergence**: `analysis/fitatu-api-discovery.md:119-122` describes the bundle's offline branch (PouchDB) as DELETE-then-POST. That ordering is an artifact of offline-sync code, NOT a server-imposed requirement. We deliberately invert to POST-then-DELETE for online MCP path.

**Pre-condition**: server must tolerate two distinct `planDayDietItemId`s with same product+measure+meal+date present simultaneously for ~1 RTT. Verified by T9.3 against real account before §15 merges; if server rejects second POST with 409, swap to DELETE-then-POST and document lost-write risk.

**Failure handling**: If POST succeeds and DELETE fails (network/4xx/5xx), tool returns `{ok: true, replaced_from: <old uuid>, cleanup_failed: true, warnings: ["old item ... must be deleted manually"]}` rather than masking the leak.
```

---

## High findings

### H1 — `_request` signature drift between §4.1 and §15.2
**Spec §4.1** (`spec.md:208-216`): `def _request(self, method, path, *, json=None, params=None, accept_version="v3") -> requests.Response`
**Spec §15.2** (`spec.md:543-545`): `def _request(self, method, path, *, params=None, json_body=None, base_url=None, retry_on_401=True) -> dict | list`

Three differences: param name (`json` → `json_body`), return type (`requests.Response` → `dict | list`), added/removed kwargs.

**Why high**: §4.1 already audited; §15.2 retroactively changes contract. Implementer hits conflicting requirements.

**Patch** — Replace §15.2 _request snippet with extension of §4.1:
```python
def _request(
    self,
    method: str,
    path: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
    accept_version: str = "v3",
    base_url: str | None = None,   # NEW — defaults to module BASE_URL_READ
) -> requests.Response: ...
```

Rename §4.1's `BASE_URL` → `BASE_URL_READ` symmetric with `BASE_URL_WRITE`.

---

### H2 — "Minimum payload first, enrich on 400" doubles surface area
**Spec** (`spec.md:586`): "First implementation will send the minimum shape and observe server response; if rejected (400), enrich with computed nutrition fields…"

**Why high**: Pretends to be a fallback but is two code paths up-front (minimum builder, full-nutrition builder, 400-detection, retry orchestration, tests for both). Picking "minimum first" defers nothing — ships fork in production behavior.

**Patch** — Replace §15.4 "Unknown" paragraph:
```markdown
**Decision**: Mirror the web app — send full computed nutrition payload (`energy`, `protein`, `fat`, `carbohydrate`, `fiber`, `sugars`, `salt`, `weight`) populated from Product macros pro-rated by `measureQuantity × measure.weightPerUnit`. Required because:
1. Bundle's `generatePlannerItemDataFromProduct` always sends them.
2. Even if server accepts minimum shape, day GET returns whichever nutrition source the server cached, risking silent divergence between local cache and server view.
3. Post-write re-sync (§15.3 step 4) is safety net against any client/server compute drift.

If `measureId` not present in local Product cache, issue one `GET /api/products/{id}` to retrieve `measures[]` (one-time cost per new product). For v1, this fetch is unconditional when measures aren't cached.
```

---

### H3 — Product-not-in-local-cache case underspecified
**Spec** (`spec.md:462`): "Resolve product → get full product object (from local cache or `get_product` call) for nutrition fields."

**Gap**: If LLM calls `search_products` (returns catalog id) → `add_meal_item(product_id=that id)` without intermediate `get_product`, local Product table has no row. Spec doesn't say what happens.

**Patch** — Replace §15.1 add_meal_item Side effects step 1:
```markdown
1. Resolve product:
   a. Look up `Product` row by `product_id` in local SQLite.
   b. If miss, call `client.get_product(product_id)`, upsert local row, then use it.
   c. If 404 (catalog or user product no longer exists), raise `RuntimeError("Product {id} not found in Fitatu")`.
   Outcome is the source for nutrition pro-rating in §15.4.
```

---

## Medium findings

### M1 — §15.8 decisions not surfaced in §15.1 body
**Patch** — Add §15.0a "Decisions baked in":
```markdown
1. `add_meal_item` does NOT auto-create products.
2. `update_meal_item` does NOT support changing `meal_key`.
3. Nutrition fields ARE sent in POST body (mirror web app).
4. UUIDs are v1 (`uuid.uuid1()`).
5. POSTs are NOT auto-retried on transient failure in v1.
```

### M2 — Idempotency unresolved AND interacts with C3
**Patch** — Append to §15.1 add_meal_item Error cases:
```markdown
- Network failure with no response body: tool raises `RuntimeError("upstream request failed before response")`. Caller should NOT auto-retry. Resolve: call `get_day_summary`, check if item appears, re-call only if missing.
```

### M3 — `delete_all_related_meals` semantics unvalidated
**Patch**:
```markdown
| `delete_all_related_meals` | bool | no | Default `false`. Server-side feature; exact semantic scope NOT documented in bundle. v1 contract: tool forwards flag as-given; docstring warns callers `true` may delete more than single item. Discovery follow-up before recommending `true` to LLM callers. |
```

### M4 — Env var threading inconsistent with §7.0 build_app factory
**Spec §15.5** uses `FITATU_BASE_URL=https://www.fitatu.com` (no `/api` suffix vs `BASE_URL_WRITE="https://www.fitatu.com/api"`); module-import-time env read conflicts with `build_app(env=...)` factory.

**Patch**:
```bash
FITATU_BASE_URL_READ=https://pl-pl.fitatu.com   # NEW; overrides BASE_URL_READ
FITATU_BASE_URL_WRITE=https://www.fitatu.com    # NEW; "/api" appended by client
FITATU_ALLOW_DELETE=false
```

Add to §15.2: `FitatuClient.__init__` accepts optional `base_url_read`/`base_url_write` overrides (default to module constants). `build_app(env)` reads env vars and passes to constructor.

---

## Low findings

### L1 — UUID v1 leaks host MAC
`uuid.uuid1()` includes host MAC. Privacy leak (worse for self-hosted MCPs through residential ISPs).

**Patch**: `uuid.uuid1(node=<random 48-bit>)`, generate random node once at `FitatuClient.__init__`.

### L2 — `meal_key` whitelist not referenced as constant in §15.3
**Patch** — Top of §15.3:
```markdown
The `meal_key` whitelist is constant `MEAL_KEYS_VALID = frozenset({"breakfast", "second_breakfast", "lunch", "dinner", "snack", "supper"})` (referenced by §15.1, CONTEXT.md, validation logic). Defined once in `service.py` or `constants.py`.
```

### L3 — Acceptance #10 doesn't account for FITATU_ALLOW_DELETE gate
**Patch**:
```markdown
10. With `FITATU_ALLOW_DELETE=true`, `delete_meal_item` against real account removes item; subsequent `get_day` shows it gone. With `FITATU_ALLOW_DELETE=false`, tool absent from MCP catalog (see T9.5).
```

### L4 — `_range_envelope` deviation note in §2 not reconciled
§2 said meal-item write tools WILL use `_range_envelope` (deferred). §15.1 silently breaks promise.

**Patch** — Add to §15.1 add_meal_item Returns:
```markdown
**Envelope rationale**: Like product writes (§2 deviation), meal-item writes are single-target and not date-ranged. The `_range_envelope({start_date, end_date, day_count, days})` shape from D7 carries no signal. The §2 footnote "D7 still applies when meal-item write tools land" is hereby resolved — D7 does NOT apply.
```

### L5 — `replaced_from` field in update_meal_item not in JSON example for add_meal_item
Cosmetic. Add near-add_meal_item-envelope JSON example to update_meal_item.

---

## Cross-checks confirmed (no finding)

1. Endpoint URLs match discovery exactly.
2. Search endpoint matches discovery.
3. Item payload shape matches discovery.
4. UUID v1 client-generated identity matches discovery.
5. Meal-key set matches CONTEXT.md and discovery.
6. PRIMARY/SECONDARY framing consistent with CONTEXT.md.
7. `sync_day_from_fitatu` exists at `service.py:257`, signature matches.

---

## Compliance verdict

**❌ Not ready (rework needed)** — 3 Critical (C1, C2, C3), 3 High (H1, H2, H3) each carry real risk of broken-on-day-one or silent-corruption-on-week-one. All have surgical patches above.

With those merged (especially C1 Option A, C2 verification step, C3 failure-handling, H1 signature reconciliation), §15 becomes implementable.

After patching, recommend 5-min sanity re-read by spec owner before kicking off Group 5 (build_app factory) — §7.0 factory + new `BASE_URL_READ`/`BASE_URL_WRITE` env-threading from M4 will land in same code area, need sequencing.
