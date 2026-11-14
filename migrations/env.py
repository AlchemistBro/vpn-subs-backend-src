import asyncio

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from vas.models import Model
from vas.settings import get_settings

target_metadata = Model.metadata


def run_sync(connection: Connection) -> None:
    # Не ждём лок дольше 5 секунд: миграция, вставшая в очередь за долгой транзакцией,
    # блокирует всех, кто придёт к таблице после неё.
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.execute("SET lock_timeout = '5s'")
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(get_settings().db_url)
    async with engine.connect() as conn:
        await conn.run_sync(run_sync)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=get_settings().db_url, target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_online())
