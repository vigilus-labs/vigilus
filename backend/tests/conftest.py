"""Shared pytest fixtures for the Vigilus test suite."""

from __future__ import annotations

import os

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

os.environ.setdefault("VIGILUS_SECRET", "test-secret-key-for-vigilus-testing-1234")
os.environ.setdefault("VIGILUS_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from vigilus.db import models  # noqa: F401, E402 – register models on Base
from vigilus.db.base import Base, get_engine, get_session_factory  # noqa: E402


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Provide a session on the app's global engine so services that build
    their own sessions (e.g. ToolRegistry) see the same in-memory database."""
    engine = get_engine()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = get_session_factory()

    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def async_client(db_session: AsyncSession):
    from httpx import ASGITransport, AsyncClient

    from vigilus.api.deps import require_user
    from vigilus.db.base import get_db
    from vigilus.db.models import User
    from vigilus.main import create_app

    app = create_app()

    async def override_get_db():
        yield db_session

    async def override_require_user():
        return User(id="test-user", username="test", password_hash="x", token_version=0)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_user] = override_require_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def unauthenticated_client(db_session: AsyncSession):
    """Client without the require_user override — for auth endpoint tests."""
    from httpx import ASGITransport, AsyncClient

    from vigilus.db.base import get_db
    from vigilus.main import create_app

    app = create_app()

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
