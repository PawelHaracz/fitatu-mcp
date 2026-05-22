"""Group 6 tests: search_products — local (custom) + live Fitatu (catalog) wiring."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

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


async def _call_tool(mcp, name: str, args: dict):
    return await mcp.call_tool(name, args)


def _call_tool_sync(mcp, name: str, args: dict) -> dict:
    """Call a FastMCP tool and decode the JSON envelope from the first TextContent."""
    result = asyncio.run(_call_tool(mcp, name, args))
    # FastMCP returns list[TextContent] or tuple (list[TextContent], structured)
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    items = result[0] if isinstance(result, tuple) else result
    text = items[0].text
    return json.loads(text)


@pytest.fixture
def app_mcp(monkeypatch):
    """Build app/mcp with a SessionLocal bound to an in-memory engine seeded with products."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from mcp_server import database, server
    from mcp_server.models import Base

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    monkeypatch.setattr(server, "SessionLocal", TestSession)

    app, mcp = server.build_app(_base_env())
    # Pre-seed user_id so _ensure_user_id() short-circuits and skips real login
    app.state.fitatu_client.user_id = "42"
    app.state.fitatu_client.token = "tok"

    # Seed
    from mcp_server.service import upsert_product

    with TestSession() as db:
        upsert_product(db, {"id": 1, "name": "Homemade Hummus", "energy": 200, "protein": 8, "fat": 12, "carbohydrate": 15}, source="custom")
        upsert_product(db, {"id": 2, "name": "Greek Yogurt", "energy": 60, "protein": 10, "fat": 0, "carbohydrate": 4}, source="custom")
        upsert_product(db, {"id": 3, "name": "Catalog Apple", "energy": 50, "protein": 0, "fat": 0, "carbohydrate": 12}, source="catalog")
        db.commit()

    return app, mcp


def test_search_scope_custom_returns_local_like_matches(app_mcp):
    app, mcp = app_mcp
    envelope = _call_tool_sync(mcp, "search_products", {"query": "Yogurt", "scope": "custom", "limit": 10})
    assert envelope["ok"] is True
    assert envelope["scope"] == "custom"
    assert len(envelope["results"]) == 1
    assert envelope["results"][0]["name"] == "Greek Yogurt"


def test_search_scope_catalog_calls_fitatu_search_food(app_mcp):
    app, mcp = app_mcp
    fake_hits = [
        {"foodId": 555, "name": "Apple PRODUCT", "brand": "Acme", "energy": 52, "type": "PRODUCT"},
        {"foodId": 777, "name": "Apple pie RECIPE", "brand": "", "energy": 250, "type": "RECIPE"},
    ]
    with patch("mcp_server.fitatu_client.FitatuClient.search_food", return_value=fake_hits) as mock_search:
        envelope = _call_tool_sync(mcp, "search_products", {"query": "Apple", "scope": "catalog", "limit": 10})

    mock_search.assert_called_once()
    assert envelope["ok"] is True
    assert envelope["scope"] == "catalog"
    ids = [r["id"] for r in envelope["results"]]
    assert 555 in ids and 777 in ids
    apple = next(r for r in envelope["results"] if r["id"] == 555)
    assert apple["type"] == "PRODUCT"
    assert apple["source"] == "catalog"


def test_search_scope_all_merges_custom_then_catalog_dedup(app_mcp):
    app, mcp = app_mcp
    # `Greek Yogurt` is the custom hit (id=2). Catalog response shadows it with the same id
    # to prove dedup, and adds a fresh id.
    fake_hits = [
        {"foodId": 2, "name": "Greek Yogurt (catalog dup)", "brand": "", "energy": 60, "type": "PRODUCT"},
        {"foodId": 999, "name": "Greek Yogurt Drink", "brand": "Brand", "energy": 75, "type": "PRODUCT"},
    ]
    with patch("mcp_server.fitatu_client.FitatuClient.search_food", return_value=fake_hits):
        envelope = _call_tool_sync(mcp, "search_products", {"query": "Yogurt", "scope": "all", "limit": 10})

    assert envelope["ok"] is True
    assert envelope["scope"] == "all"
    ids = [r["id"] for r in envelope["results"]]
    # custom (id=2) appears once; id=999 is the fresh catalog hit
    assert ids.count(2) == 1
    assert 999 in ids
    # custom row comes first
    assert envelope["results"][0]["id"] == 2
    assert envelope["results"][0]["source"] == "custom"


def test_search_scope_catalog_upstream_failure_returns_empty(app_mcp):
    app, mcp = app_mcp
    with patch("mcp_server.fitatu_client.FitatuClient.search_food", side_effect=RuntimeError("upstream 500")):
        envelope = _call_tool_sync(mcp, "search_products", {"query": "Apple", "scope": "catalog", "limit": 10})
    assert envelope["ok"] is True
    assert envelope["results"] == []


def test_search_query_too_short_raises(app_mcp):
    app, mcp = app_mcp
    with pytest.raises(Exception) as exc_info:
        _call_tool_sync(mcp, "search_products", {"query": "a", "scope": "custom", "limit": 10})
    assert "at least 2 characters" in str(exc_info.value)
