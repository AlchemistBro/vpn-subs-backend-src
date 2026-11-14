"""Движок, фабрика сессий и общая база моделей."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from vas.settings import Settings

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Model(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: DateTime(timezone=True)}


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


def make_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.db_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"server_settings": {"application_name": "vas"}},
    )


def make_sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: после commit объекты остаются читаемыми без нового запроса
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def transaction(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessions() as session, session.begin():
        yield session
