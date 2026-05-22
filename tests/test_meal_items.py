"""Group 9 tests: meal-item write tools (add/update/delete).

Spec §15 — the user's PRIMARY goal: log/edit/delete what they ate.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import MagicMock, patch

import pytest


def _base_env(**overrides) -> dict:
    env = {
        "FITATU_USERNAME": "u",
        "FITATU_PASSWORD": "p",
        "FITATU_API_SECRET": "s",
        "MCP_API_KEY": "mcp-key",
        "FITATU_DB_FILE": ":memory:",
        "FITATU_ALLOW_DELETE": "false",
    }
    env.update(overrides)
    return env


def _tool_names(mcp) -> list[str]:
    tools = asyncio.run(mcp.list_tools())
    return [t.name for t in tools]


# -- 9.1 add_meal_item happy path --


def test_add_meal_item_happy_path():
    """add_meal_item builds correct payload + UUID v1 + nutrition pro-rated + sync called."""
    from mcp_server import service
    from mcp_server.fitatu_client import FitatuClient

    client = FitatuClient("u", "p")
    client.token = "tok"
    client.user_id = "42"

    product = {
        "id": 555,
        "name": "Banana",
        "energy": 89,
        "protein": 1.1,
        "fat": 0.3,
        "carbohydrate": 22.8,
        "fiber": 2.6,
        "sugars": 12.2,
        "salt": 0.0,
        "measures": [{"id": 1, "name": "100g", "weightPerUnit": 100.0}],
    }

    captured: dict = {}

    def fake_post_day_items(date, items):
        captured["date"] = date
        captured["items"] = items
        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = {"ok": True}
        return resp

    def fake_sync(db, c, day):
        from mcp_server.schemas import DaySummarySchema
        return DaySummarySchema(user_id="42", day_date=day, meals=[])

    with patch.object(client, "get_product", return_value=product) as mock_get, \
         patch.object(client, "post_day_items", side_effect=fake_post_day_items) as mock_post, \
         patch("mcp_server.service.sync_day_from_fitatu", side_effect=fake_sync):

        db = MagicMock()
        db.get.return_value = None  # local cache miss, will trigger client.get_product
        result = service.add_meal_item(
            db, client,
            date="2026-05-22",
            meal_key="breakfast",
            product_id=555,
            measure_id=1,
            measure_quantity=1.5,
        )

    assert result["ok"] is True
    assert result["date"] == "2026-05-22"
    assert result["meal_key"] == "breakfast"
    # UUID v1 check
    parsed = uuid.UUID(result["plan_day_diet_item_id"])
    assert parsed.version == 1

    # Item shape
    item = captured["items"][0]
    assert item["meal"] == "breakfast"
    assert item["itemId"] == 555
    assert item["foodType"] == "PRODUCT"
    assert item["type"] == "PRODUCT"
    assert item["measureId"] == 1
    assert item["measureQuantity"] == 1.5
    assert item["planDayDietItemId"] == result["plan_day_diet_item_id"]

    # Nutrition pro-rated: weight = 100g × 1.5 = 150g; energy = 89 × 150 / 100 = 133.5
    assert item["weight"] == pytest.approx(150.0)
    assert item["energy"] == pytest.approx(133.5)
    assert item["protein"] == pytest.approx(1.65, rel=1e-3)
    assert item["carbohydrate"] == pytest.approx(34.2, rel=1e-3)

    mock_post.assert_called_once()


# -- 9.2 invalid meal_key --


def test_add_meal_item_rejects_invalid_meal_key():
    from mcp_server import service
    from mcp_server.fitatu_client import FitatuClient

    client = FitatuClient("u", "p")
    client.token = "tok"
    client.user_id = "42"

    db = MagicMock()
    with pytest.raises(ValueError, match="meal_key"):
        service.add_meal_item(db, client, "2026-05-22", "brunch", 555, 1, 1.0)


# -- 9.3 update POST-then-DELETE order --


def test_update_meal_item_post_then_delete_order():
    """update_meal_item issues POST first, then DELETE."""
    from mcp_server import service
    from mcp_server.fitatu_client import FitatuClient

    client = FitatuClient("u", "p")
    client.token = "tok"
    client.user_id = "42"

    # Existing day fetch returns one item to replace
    existing_day = {
        "dietPlan": {
            "breakfast": {
                "items": [
                    {
                        "planDayDietItemId": "old-uuid-abc",
                        "productId": 555,
                        "measureId": 1,
                        "measureName": "100g",
                        "measureQuantity": 1.0,
                    }
                ]
            }
        }
    }
    product = {
        "id": 555,
        "name": "Banana",
        "energy": 89, "protein": 1.1, "fat": 0.3, "carbohydrate": 22.8,
        "measures": [{"id": 1, "name": "100g", "weightPerUnit": 100.0}],
    }

    call_log: list[str] = []

    def fake_post(date, items):
        call_log.append("POST")
        resp = MagicMock(); resp.status_code = 201; resp.json.return_value = {}
        return resp

    def fake_delete(date, meal_key, pid, delete_all_related_meals=False):
        call_log.append("DELETE")
        resp = MagicMock(); resp.status_code = 200; resp.json.return_value = {"deleted": True}
        return resp

    def fake_sync(db, c, day):
        from mcp_server.schemas import DaySummarySchema
        return DaySummarySchema(user_id="42", day_date=day, meals=[])

    with patch.object(client, "get_day", return_value=existing_day), \
         patch.object(client, "get_product", return_value=product), \
         patch.object(client, "post_day_items", side_effect=fake_post), \
         patch.object(client, "delete_day_item", side_effect=fake_delete), \
         patch("mcp_server.service.sync_day_from_fitatu", side_effect=fake_sync):
        db = MagicMock()
        db.get.return_value = None
        result = service.update_meal_item(
            db, client,
            date="2026-05-22",
            meal_key="breakfast",
            plan_day_diet_item_id="old-uuid-abc",
            new_measure_quantity=2.0,
        )

    assert call_log == ["POST", "DELETE"], f"expected POST then DELETE; got {call_log}"
    assert result["ok"] is True
    assert result["replaced_from"] == "old-uuid-abc"
    assert result["cleanup_failed"] is False


# -- 9.4 delete URL composition --


def test_delete_meal_item_url_composition():
    """delete_meal_item passes deleteAllRelatedMeals query param correctly."""
    from mcp_server import service
    from mcp_server.fitatu_client import FitatuClient

    client = FitatuClient("u", "p")
    client.token = "tok"
    client.user_id = "42"

    captured: dict = {}

    def fake_delete(date, meal_key, pid, delete_all_related_meals=False):
        captured["date"] = date
        captured["meal_key"] = meal_key
        captured["pid"] = pid
        captured["delete_all"] = delete_all_related_meals
        resp = MagicMock(); resp.status_code = 200; resp.json.return_value = {"deleted": True}
        return resp

    def fake_sync(db, c, day):
        from mcp_server.schemas import DaySummarySchema
        return DaySummarySchema(user_id="42", day_date=day, meals=[])

    with patch.object(client, "delete_day_item", side_effect=fake_delete), \
         patch("mcp_server.service.sync_day_from_fitatu", side_effect=fake_sync):
        db = MagicMock()
        result = service.delete_meal_item(
            db, client, "2026-05-22", "breakfast", "uuid-zzz",
            delete_all_related_meals=True,
        )
    assert result["ok"] is True
    assert result["deleted_plan_day_diet_item_id"] == "uuid-zzz"
    assert captured["delete_all"] is True


# -- 9.5 delete_meal_item gated by FITATU_ALLOW_DELETE --


def test_delete_meal_item_unregistered_when_flag_false():
    from mcp_server.server import build_app

    _, mcp = build_app(_base_env(FITATU_ALLOW_DELETE="false"))
    names = _tool_names(mcp)
    assert "delete_meal_item" not in names


def test_delete_meal_item_registered_when_flag_true():
    from mcp_server.server import build_app

    _, mcp = build_app(_base_env(FITATU_ALLOW_DELETE="true"))
    names = _tool_names(mcp)
    assert "delete_meal_item" in names


def test_add_meal_item_and_update_meal_item_registered():
    """Both add and update are unconditional (not gated)."""
    from mcp_server.server import build_app

    _, mcp = build_app(_base_env(FITATU_ALLOW_DELETE="false"))
    names = _tool_names(mcp)
    assert "add_meal_item" in names
    assert "update_meal_item" in names
