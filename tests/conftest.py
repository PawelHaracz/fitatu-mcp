"""Shared test fixtures. Env stubs live in root conftest.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def in_memory_engine():
    """Fresh in-memory SQLite engine with all tables created via Base.metadata.create_all."""
    from mcp_server.models import Base

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(in_memory_engine):
    """SessionLocal bound to the in-memory engine; rolls back at teardown."""
    SessionLocal = sessionmaker(bind=in_memory_engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        yield session
        session.rollback()
    finally:
        session.close()


@pytest.fixture
def fake_session():
    """MagicMock(spec=requests.Session) for FitatuClient injection."""
    return MagicMock(spec=requests.Session)
