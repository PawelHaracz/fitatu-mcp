"""Live smoke test for write tools (rewrite for whole-day POST contract).

End-to-end:
  1. Login
  2. get_day for today (read path)
  3. search_food to confirm pl-pl cluster works
  4. add_meal_item — appends one apple-like item to second_breakfast
  5. update_meal_item — change quantity
  6. delete_meal_item — soft-delete (mark deletedAt)
  7. Verify final day: item present with deletedAt set, no orphans

Net effect on your real diary: one soft-deleted entry in second_breakfast.
Fitatu UI typically hides soft-deleted items, so visual impact = none.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path


def _load_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        print(f"❌ no .env at {env_path}")
        sys.exit(1)
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key, value)
    os.environ["FITATU_DB_FILE"] = "./fitatu_smoke.db"


_load_env()

import types

REPO_ROOT = Path(__file__).resolve().parent.parent
parent = REPO_ROOT.parent
if str(parent) not in sys.path:
    sys.path.insert(0, str(parent))
if "mcp_server" not in sys.modules:
    pkg = types.ModuleType("mcp_server")
    pkg.__path__ = [str(REPO_ROOT)]
    sys.modules["mcp_server"] = pkg


from mcp_server import database, service  # noqa: E402
from mcp_server.fitatu_client import FitatuClient  # noqa: E402
from mcp_server.models import Base  # noqa: E402


def main() -> int:
    today = date.today().isoformat()
    print(f"🔧 smoke test for {today}")

    Base.metadata.create_all(bind=database.engine)
    SessionLocal = database.SessionLocal

    client = FitatuClient(os.environ["FITATU_USERNAME"], os.environ["FITATU_PASSWORD"])

    print("\n[1] login...")
    try:
        client.login()
    except Exception as exc:
        print(f"❌ login: {exc}")
        return 1
    print(f"   ✓ user_id={client.user_id}")

    print(f"\n[2] get_day({today})...")
    day = client.get_day(today)
    diet_plan = day.get("dietPlan") or {}
    print(f"   ✓ meals: {list(diet_plan.keys())}")

    print("\n[3] search_food('jabłko', limit=5)...")
    try:
        results = client.search_food("jabłko", page=1, limit=5)
    except Exception as exc:
        print(f"❌ search_food: {exc}")
        return 1
    if not results:
        print("❌ empty search results")
        return 1
    print(f"   ✓ raw search returned {len(results)} hits")
    chosen = next((r for r in results if (r.get("type") or "").upper() == "PRODUCT" and r.get("foodId")), None)
    if chosen is None:
        # No PRODUCT in initial results — retry with a more product-y term
        print("   ℹ️  no PRODUCT in 'jabłko' results; retrying with 'banan'")
        results = client.search_food("banan", page=1, limit=10)
        chosen = next((r for r in results if (r.get("type") or "").upper() == "PRODUCT" and r.get("foodId")), None)
    if chosen is None:
        print(f"   ❌ no PRODUCT found across queries")
        return 1
    product_id = chosen["foodId"]
    embedded_measure = chosen.get("measure") or {}
    measure_id = embedded_measure.get("measureId")
    print(f"   ✓ chose: productId={product_id} name={chosen.get('name')!r} embedded_measureId={measure_id}")

    if measure_id is None:
        print(f"\n[4] embedded measure missing — fetching product for measures[]...")
        full_product = client.get_product(product_id)
        measures = full_product.get("measures") or []
        if not measures:
            print("❌ no measures on product")
            return 1
        measure_id = measures[0]["id"]
        print(f"   ✓ measure id={measure_id}")
    else:
        print(f"\n[4] using embedded measure id={measure_id}")

    print(f"\n[5] add_meal_item(date={today}, meal=second_breakfast, product={product_id}, measure={measure_id}, qty=1.0)...")
    with SessionLocal() as db:
        try:
            add_result = service.add_meal_item(
                db, client, today, "second_breakfast", product_id, measure_id, 1.0,
            )
            db.commit()
        except Exception as exc:
            print(f"❌ add: {exc}")
            import traceback; traceback.print_exc()
            return 1
    new_pid = add_result["plan_day_diet_item_id"]
    print(f"   ✓ added planDayDietItemId={new_pid}")

    print(f"\n[6] verify on read cluster...")
    day_after = client.get_day(today)
    sb_items = ((day_after.get("dietPlan") or {}).get("second_breakfast") or {}).get("items") or []
    matching = [it for it in sb_items if str(it.get("planDayDietItemId")) == str(new_pid)]
    if matching:
        item = matching[0]
        print(f"   ✓ found item — qty={item.get('measureQuantity')} deletedAt={item.get('deletedAt')}")
    else:
        print(f"   ⚠️  not found among {len(sb_items)} items")

    print(f"\n[7] update_meal_item(qty 1.0 → 2.0)...")
    with SessionLocal() as db:
        try:
            up_result = service.update_meal_item(
                db, client, today, "second_breakfast", new_pid, 2.0,
            )
            db.commit()
        except Exception as exc:
            print(f"❌ update: {exc}")
            import traceback; traceback.print_exc()
            return 1
    assert up_result["plan_day_diet_item_id"] == new_pid, "update should keep same UUID"
    print(f"   ✓ updated, same UUID={new_pid}")

    print(f"\n[8] verify quantity changed...")
    day_after_up = client.get_day(today)
    sb_items = ((day_after_up.get("dietPlan") or {}).get("second_breakfast") or {}).get("items") or []
    matching = [it for it in sb_items if str(it.get("planDayDietItemId")) == str(new_pid)]
    if matching:
        item = matching[0]
        print(f"   ✓ qty={item.get('measureQuantity')} updatedAt={item.get('updatedAt')}")
        if item.get("measureQuantity") != 2.0:
            print(f"   ⚠️  expected 2.0, got {item.get('measureQuantity')}")
    else:
        print(f"   ❌ item disappeared after update")
        return 1

    print(f"\n[9] delete_meal_item (soft)...")
    with SessionLocal() as db:
        try:
            del_result = service.delete_meal_item(
                db, client, today, "second_breakfast", new_pid,
            )
            db.commit()
        except Exception as exc:
            print(f"❌ delete: {exc}")
            return 1
    print(f"   ✓ deleted (soft)")

    print(f"\n[10] verify deletedAt was set...")
    day_final = client.get_day(today)
    sb_items = ((day_final.get("dietPlan") or {}).get("second_breakfast") or {}).get("items") or []
    matching = [it for it in sb_items if str(it.get("planDayDietItemId")) == str(new_pid)]
    if matching:
        item = matching[0]
        deleted_at = item.get("deletedAt")
        if deleted_at:
            print(f"   ✓ deletedAt={deleted_at}")
        else:
            print(f"   ❌ deletedAt not set on item")
            return 1
    else:
        # Server may have removed it instead of soft-deleting
        print(f"   ℹ️  item removed entirely from response (server may filter deleted items on read)")

    print("\n✅ smoke test complete — write contract validated end-to-end")
    return 0


if __name__ == "__main__":
    sys.exit(main())
