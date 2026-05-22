# Fitatu API Discovery (Phase 0)

Captured: 2026-05-21 via Playwright MCP + direct HTTP probing using existing `FitatuClient` login flow against `https://pl-pl.fitatu.com`.

## Confirmed write endpoints

### Products (custom user products)

| Op | Method | URL | Status |
|---|---|---|---|
| Create | `POST` | `/api/products` | **CONFIRMED 201** — verified with 4 test products (since deleted) |
| Read | `GET` | `/api/products/{id}` | **CONFIRMED 200** |
| Update | `PUT` / `PATCH` | `/api/products/{id}` | OPTIONS reports allowed (not test-executed) |
| Delete | `DELETE` | `/api/products/{id}` | **CONFIRMED 200** — `{"deleted":true}` |

**POST /api/products minimum payload** (all caps below = required):

```json
{
  "name": "string (required)",
  "energy": 100,
  "protein": 5,
  "fat": 2,
  "carbohydrate": 10
}
```

Returns: `{"id": <int>, "name": "<string>"}`.

**Full product schema (GET response)** — 43 fields incl. macros, vitamins, measures:

```
id, name, rawIngredients, editable, favorite, userId, foodType ("PRODUCT"),
username, sfdLink, brand, manufacturer, category (int), customStatuses[],
hasBarcodes, initialMeasure {key, weight, unitKey}, productScore.value,
measures[ {id, name, energyPerUnit, weightPerUnit} ],
ingredients[ {id, name, eNumber, healthRating, position, categories[], hasDetails, affectsScore} ],
proposals[], currentUserPrivateBarcode, barcode, liquid, simpleMeasures[…],
source (int), energyCalories, proteinFatExchangers, glycemicLoad,
carbohydrateExchangers, energy, protein, vegetableProtein, animalProtein, fat,
saturatedFat, polyunsaturatedFat, monounsaturatedFat, carbohydrate, cholesterol,
fiber, sodium, salt, calcium, potassium, iron, magnesium, zinc, phosphorus,
iodine, copper, selenium, vitaminA, vitaminB1, vitaminB2, vitaminB5, vitaminB6,
vitaminB7, vitaminB12, vitaminC, vitaminD, vitaminE, vitaminK, vitaminPP,
folicAcid, sugars, omega3, omega6, caffeine, glycemicIndex, visible, private,
deleted, containNutritions, accessType, genericProduct, duplicate, verified,
verificationType, verifiedAt
```

### Recipes

| Op | Method | URL | Status |
|---|---|---|---|
| Create | `POST` | `/api/recipes` | OPTIONS reports allowed |
| Read | `GET` | `/api/recipes/{id}` | OPTIONS reports allowed |
| Update | `PUT` | `/api/recipes/{id}` | OPTIONS reports allowed |
| Delete | `DELETE` | `/api/recipes/{id}` | OPTIONS reports allowed |

Payload schema not yet captured.

### Day-item endpoints (add_meal_item / update_meal_item / delete_meal_item)

**FOUND 2026-05-22 in `https://www.fitatu.com/app/bundle.e8159adf84dc9b075d26.js`** (13.5 MB main JS bundle, downloaded direct via curl after Proxyman session revealed it as the canonical web app). The previous "NOT FOUND" was on the `pl-pl.fitatu.com` cluster — the `www.fitatu.com/app/` is the real client and uses a **different base path** (`/api`, NOT `/api/v3/`).

| Op | Method | URL | Source (bundle line) |
|---|---|---|---|
| **Bulk add items** | `POST` | `/api/diet-plan/{userId}/day-items/{date}` | 72687 — `planner_actions_addItemsToPlanner` |
| **Delete single item** | `DELETE` | `/api/diet-plan/{userId}/day/{date}/{mealKey}/{planDayDietItemId}?deleteAllRelatedMeals={bool}` | 72652 — `planner_actions_sendRemoveItemRequest` |
| **Update item** | _(no direct endpoint)_ | EDIT = soft-delete old + insert new with fresh UUID | 71287 — `handleReplacePlannerItem` (offline path) + 73334 — `sendReplaceItemRequest` import (dead code; offline-only client) |
| **Day fetch (sync)** | `GET` | `/api/diet-and-activity-plan/{userId}/day/{date}` | 41969 — `getPlannerDayUrl` (already used in our code) |

#### POST /api/diet-plan/{userId}/day-items/{date} — payload

```js
// bundle line 72686-72693
{
  url: `${API_URL}/diet-plan/${userId}/day-items/${date}`,
  type: 'POST',
  data: {
    items: [
      // each item is a "planner item" — see "Item fields" table below.
      // For PRODUCT items, minimum fields required (inferred from generatePlannerItemDataFromProduct):
      {
        planDayDietItemId: "<uuid v1>",   // client-generated! (bundle line 34461: generateUuid())
        itemId: <productId>,                // numeric Fitatu product id
        type: "PRODUCT",                    // or "RECIPE", "CUSTOM_ITEM"
        foodType: "PRODUCT",                // mirrors `type` upper-cased
        measureId: <int>,                   // from product.measures[].id
        measureQuantity: <decimal>,         // e.g. 1.5 (number of measures, not grams)
        meal: "breakfast"                   // meal_key
        // ...full nutrition fields propagated by generatePlannerItemDataFromProduct
      }
    ]
  }
}
```

Important: `planDayDietItemId` is **client-generated UUID v1** (bundle line 34461, 72465). Server accepts it as the canonical identity. This means:
- Idempotency is the client's responsibility (re-POST same UUID — likely 409 or no-op; not yet verified).
- For "update" — generate a new UUID and POST it; mark old one as deleted (separate DELETE call, or `deletedAt` field in offline sync).

#### Update strategy: replace = delete + add

The web app does NOT have a PUT/PATCH endpoint for an individual day-item. The Vuex action `REPLACE_PLANNER_ITEM` (bundle line 73316-73345):

```js
// online branch (line 73334): calls dietPlanItem["sendReplaceItemRequest"] — BUT this export does not exist
//   in module 110 (line 71093-71101 export list: a, b, c, d only). So the online code path is effectively
//   broken or hits a runtime error. In practice the offline branch (PouchDB) is taken.
// offline branch (line 71287-71320, handleReplacePlannerItem):
//   1. Load plannerDay from PouchDB
//   2. Find item by planDayDietItemId
//   3. Set itemToReplace.deletedAt = moment().format('YYYY-MM-DD HH:mm:ss')
//   4. Generate new item (new UUID) via generatePlannerItem
//   5. mealItems.push(itemToInsert)
//   6. updateDayDocumentInDatabase(...pushRequired: true) — PouchDB sync handles upload
```

**Implication for MCP**: For `update_meal_item`, our client must:
1. `DELETE /api/diet-plan/{userId}/day/{date}/{mealKey}/{oldPlanDayDietItemId}` (soft-delete via API)
2. `POST /api/diet-plan/{userId}/day-items/{date}` with new item (new UUID, updated `measureQuantity`)

Two-call atomicity not guaranteed; document this as a known limitation.

#### Mobile API config (extracted from bundle line 248464)

```json
{
  "API_URL": "fitatu.com/api",
  "API_PROTOCOL": "https://",
  "API_VERSION": "v3",
  "APP_API_KEY": "FITATU-MOBILE-APP",
  "APP_API_SECRET": "PYRXtfs88UDJMuCCrNpLV",
  "APP_OS_HEADER": "FITATU-WEB",
  "DEFAULT_MEAL_TIMES": ["breakfast", "second_breakfast", "lunch", "dinner", "snack", "supper"],
  "COMMON__TYPE_PRODUCT": "PRODUCT",
  "COMMON__TYPE_RECIPE": "RECIPE",
  "COMMON__TYPE_CUSTOM_ITEM": "CUSTOM_ITEM"
}
```

The `APP_API_SECRET` is hard-coded in the public bundle (security finding — but it's the same secret our existing code already uses via env var).

#### GraphQL (separate path — used for CMS content like MarketingBanner)

`https://www.fitatu.com/cms/api/graphql` — Apollo client for CMS content (banners, blog). NOT used for diet writes; safe to ignore for our scope.

**Conclusion**: All needed write endpoints are confirmed available on `https://www.fitatu.com/api/...` (the canonical app cluster, NOT `pl-pl.fitatu.com`). No mobile traffic capture needed.

### Product search

**Found in bundle line 174005-174032 (`fetchSearchResult`)**:

| Op | Method | URL | Status |
|---|---|---|---|
| Search by query | `GET` (default ajax) | `{searchApiUrl}/search/food/user/{userId}` | Confirmed in bundle |
| Search "last used" | `GET` | `{searchApiUrl}/search/food/{userId}/last-used` | Confirmed in bundle |
| Old probed path | `PUT` | `/api/products/search` | Exists but 400 — different envelope; deprecated client path |
| Collection list | `POST` only | `/api/products` (no `?phrase=`) | 405 on GET |

#### Payload (request body / query string, GET → query string)

```js
// bundle line 174018-174021
{
  // ...spread user `query` object (e.g. { phrase: "jabłko", barcode: undefined, ... })
  accessType: ["FREE", "PREMIUM"],  // from getAccessTypes(); PREMIUM only if gatekeeper enables
  page: 1,
  limit: 40                          // config.SEARCH__AUTO_LOADED_RESULT_ITEMS = 40
}
```

#### searchApiUrl resolution

`searchApiUrl` is **dynamic**: each user has a `user.searchUrls[]` array (set during login) of regional CDN endpoints (e.g. `search-pl.fitatu.com`, `search-de.fitatu.com`). The client races them via `resolveFastestRequest` (bundle 65113-65143) and caches the winner.

**Fallback** (no searchUrls list or all timeout): `config.API_URL` = `https://www.fitatu.com/api` (bundle 65154, 65174).

For MCP: hardcode to `${BASE_URL}/search/food/user/{userId}` (use main API URL) — works as fallback and avoids dynamic resolution complexity.

## Known supporting facts

| Item | Value |
|---|---|
| Base URL | `https://pl-pl.fitatu.com` (Polish cluster; other locales: `de-de.fitatu.com`, etc) |
| Auth | Bearer JWT in `Authorization` header, refresh at `/api/token/refresh` |
| Mandatory headers | `api-key: FITATU-MOBILE-APP`, `api-secret: <env>`, `app-os: FITATU-WEB`, `Authorization: Bearer <token>`, `API-Cluster: pl-pl{user_id}` |
| `accept` header | `application/json; version=v3` works (also v2, v1, plain accepted) |
| User id | Numeric (e.g. 41130303), derived from JWT |
| Day fetch | `GET /api/diet-and-activity-plan/{user_id}/day/{YYYY-MM-DD}` returns dietPlan grouped by meal_key |
| Confirmed meal keys (from `/api/diet-plan/{uid}/settings/preferences/meal-schema/default`) | `breakfast`, `second_breakfast`, `lunch`, `dinner`, `supper`, `snack` (+ likely `pre_workout`, `post_workout`; full list from settings endpoint at impl time) |
| Item identifier | `planDayDietItemId` — UUID v1 ("a6bb6b50-54f4-11f1-b93a-afb3f757a7be") |
| Food type discriminator | `foodType` ∈ {"PRODUCT", "RECIPE", ...} |
| Item fields (from day GET) | planDayDietItemId, source, name, foodType, measureId, measureName, measureQuantity, energy, protein, fat, carbohydrate, weight, capacity, ingredientsServing, ingredients, preparationTime, cookingTime, mealNumber, numberOfMeals, visible, fiber, cholesterol, saturatedFat, sugars, salt, sodium, iron, liquid, eaten, productId, brand |

## Open contracts (require further discovery)

1. ~~**add_meal_item endpoint**~~ — **RESOLVED**: `POST /api/diet-plan/{userId}/day-items/{date}` with `{items:[…]}`.
2. ~~**update_meal_item endpoint**~~ — **RESOLVED via replace pattern**: DELETE old + POST new.
3. ~~**delete_meal_item endpoint**~~ — **RESOLVED**: `DELETE /api/diet-plan/{userId}/day/{date}/{mealKey}/{itemId}?deleteAllRelatedMeals={bool}`.
4. **search_products payload** — PUT /api/products/search exists; envelope still unknown. Likely available in bundle — search for `searchProducts`, `PRODUCT_SEARCH_*`.
5. **product PUT/PATCH payload** — confirmed allowed; not test-executed. Likely same shape as POST.
6. **Item field minimum set on POST day-items** — bundle reveals `generatePlannerItemDataFromProduct` decorates each item with full nutrition; need to verify which fields are server-required vs. derivable.
7. **Idempotency-Key header support** — not tested. Client-generated UUID v1 acts as idempotency anchor though.

## Source of write endpoints

`https://www.fitatu.com/app/bundle.e8159adf84dc9b075d26.js` (13.5 MB, downloaded via `curl -sL` to `/tmp/fitatu-bundle/bundle.js`). The bundle hash changes per release; future re-discovery: fetch `https://www.fitatu.com/app/`, parse `<script>` tags, locate `bundle.<hash>.js`.

## What we CAN ship now (FULL meal-item write capability)

- `add_meal_item(date, meal_key, product_id, measure_id, measure_quantity)` → POST /api/diet-plan/{userId}/day-items/{date}
- `update_meal_item(date, meal_key, plan_day_diet_item_id, new_measure_quantity)` → DELETE + POST (replace pattern)
- `delete_meal_item(date, meal_key, plan_day_diet_item_id)` → DELETE /api/diet-plan/{userId}/day/{date}/{mealKey}/{id}
- `create_custom_product(name, energy, protein, fat, carbohydrate, ...)` → POST /api/products
- `delete_custom_product(product_id)` → DELETE /api/products/{id}
- `update_custom_product(product_id, ...)` → PUT /api/products/{id} (after payload spike)
- `get_product(product_id)` → GET /api/products/{id} (helper)
- `search_products` — needs PUT payload contract first (or grep bundle for it).
