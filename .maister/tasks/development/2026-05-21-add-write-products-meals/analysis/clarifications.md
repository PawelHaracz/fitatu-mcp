# Phase 1 Clarifications

## Decisions

| # | Question | Answer |
|---|---|---|
| 1 | Scope of write operations | All four: add_meal_item, update_meal_item, delete_meal_item, create_custom_product |
| 2 | How to obtain Fitatu write endpoints | Reverse-engineer via Playwright MCP on pl-pl.fitatu.com |
| 3 | Cache strategy after writes | Write to Fitatu, then re-sync the day via sync_day_from_fitatu(day_date) |
| 4 | Add search_products read tool | Yes — needed so LLMs can resolve product names → product_id |

## Implications

- **Tool set (MVP)**: 5 new tools — `add_meal_item`, `update_meal_item`, `delete_meal_item`, `create_custom_product`, `search_products`.
- **Discovery phase required**: Capture network traces (URLs, payloads, headers, response shapes) for all 5 ops before writing any production code. Store findings in `analysis/fitatu-api-discovery.md`.
- **Cache invariant**: After every successful Fitatu write that affects a date, call `sync_day_from_fitatu(db, client, day_date)`. The day cache is the system of record locally.
- **Custom product**: Creates a product owned by user in Fitatu catalog; returns product_id usable in `add_meal_item`. No local Product table needed (Fitatu = source of truth).
- **Safety**: DELETE/UPDATE tools take explicit ids (no name-based mutations).

## Risk level

medium — external API reverse-engineering is the dominant unknown.
