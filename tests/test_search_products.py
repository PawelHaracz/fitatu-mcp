"""Group 6 tests: search_products Path B (custom scope = local LIKE)."""

from __future__ import annotations

import asyncio
import json

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


def test_search_scope_catalog_raises(app_mcp):
    app, mcp = app_mcp
    with pytest.raises(Exception) as exc_info:
        _call_tool_sync(mcp, "search_products", {"query": "Apple", "scope": "catalog", "limit": 10})
    assert "Catalog search not yet wired" in str(exc_info.value)


def test_search_scope_all_returns_with_warnings(app_mcp):
    app, mcp = app_mcp
    envelope = _call_tool_sync(mcp, "search_products", {"query": "Yogurt", "scope": "all", "limit": 10})
    assert envelope["ok"] is True
    assert envelope["scope"] == "all"
    assert "warnings" in envelope
    assert any("catalog" in w.lower() for w in envelope["warnings"])


def test_search_query_too_short_raises(app_mcp):
    app, mcp = app_mcp
    with pytest.raises(Exception) as exc_info:
        _call_tool_sync(mcp, "search_products", {"query": "a", "scope": "custom", "limit": 10})
    assert "at least 2 characters" in str(exc_info.value)
