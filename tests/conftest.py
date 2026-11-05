import os

# Tests run against a dedicated database/redis index so they never touch dev
# data, and assume `docker compose up -d db redis` (or the full stack) is
# running with its ports published to localhost.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/support_reply_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")

import asyncpg
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base


async def _ensure_test_database() -> None:
    admin_conn = await asyncpg.connect(
        user="postgres", password="postgres", host="localhost", port=5432, database="postgres"
    )
    try:
        await admin_conn.execute("CREATE DATABASE support_reply_test")
    except asyncpg.DuplicateDatabaseError:
        pass
    finally:
        await admin_conn.close()


@pytest_asyncio.fixture
async def session_factory():
    await _ensure_test_database()
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(session_factory):
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def redis_client():
    client = Redis.from_url(settings.redis_url)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()
