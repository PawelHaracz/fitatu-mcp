# Fitatu MCP — Domain Glossary

Single-context repo. Terms below are the canonical names for the concepts this MCP server exposes to LLM clients. Implementation details live elsewhere (spec.md, code).

## Core terms

### Product
A reusable food definition in the user's Fitatu catalog. Examples: "Greek yogurt 200g, 120kcal", "Homemade hummus". Created once, used many times. Owned by the user (custom) or by Fitatu (catalog).

A Product is a **building block**, not a record of eating.

### Meal item
A single entry in the user's diary, on a specific date and meal slot. Examples: "2026-05-22 breakfast: 150g of Greek yogurt". Has a date, a `meal_key`, a quantity/measure, and a reference to a Product or Recipe. Identified by `planDayDietItemId` (UUID v1).

A Meal item is a **record of eating**. "Logging what I already ate" means creating Meal items.

### Meal key
The slot within a day a Meal item belongs to. Confirmed values: `breakfast`, `second_breakfast`, `lunch`, `dinner`, `supper`, `snack`. Sourced from `/api/diet-plan/{uid}/settings/preferences/meal-schema/default`.

### Day
A `YYYY-MM-DD` date in the user's Fitatu account. Holds zero or more Meal items grouped by Meal key. Represented locally by `DailyNutrition` + `MealNutrition` + `MealItem`.

## Disambiguation

| User says... | Canonical term | Not... |
|---|---|---|
| "log what I ate" | **create Meal item** | NOT create Product |
| "add a new food" | **create Product** | NOT create Meal item |
| "I had Greek yogurt for breakfast" | **create Meal item** referencing existing Product | NOT both, unless the Product doesn't exist yet |
| "edit my breakfast" | **update Meal item** | NOT update Product |
| "delete that entry" | **delete Meal item** | NOT delete Product |
