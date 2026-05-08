from collections.abc import AsyncIterator

from prometheus_client import Gauge
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from bookcraft.infra.config import Settings

DB_POOL_CHECKED_OUT = Gauge(
    "db_pool_checked_out",
    "Database connections currently checked out.",
)


def create_engine(settings: Settings, database_url: str | None = None) -> AsyncEngine:
    engine = create_async_engine(
        database_url or settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )
    DB_POOL_CHECKED_OUT.set(0)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        async with session.begin():
            yield session
