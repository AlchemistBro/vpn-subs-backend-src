import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from tests.fakes import FakeAcquirer, FakeNotifier, FakePanel, FrozenClock
from vas.runtime import Runtime, build_runtime
from vas.settings import Settings

ROOT = Path(__file__).resolve().parent.parent
DB = os.getenv("VAS_TEST_DB_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/vas_test")
REDIS = os.getenv("VAS_TEST_REDIS_URL", "redis://localhost:6379/15")
TABLES = (
    "users, devices, login_codes, refresh_tokens, subscriptions, access_periods, payments, "
    "provider_events, renewal_attempts, panel_accounts, outbox"
)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(db_url=DB, redis_url=REDIS, env="test", log_json=False)


@pytest.fixture(scope="session")
def schema(settings: Settings) -> None:
    """Схему создают настоящие миграции с нуля: заодно проверяем, что они применяются."""
    env = {**os.environ, "VAS_DB_URL": settings.db_url}
    # Базы vas_test может не быть (make dev создаёт только vas): создаём её через служебную.
    drop = (
        "import asyncio, asyncpg, os\n"
        "async def main():\n"
        "    url = os.environ['VAS_DB_URL'].replace('+asyncpg', '')\n"
        "    base, name = url.rsplit('/', 1)\n"
        "    admin = await asyncpg.connect(base + '/postgres')\n"
        "    if not await admin.fetchval('SELECT 1 FROM pg_database WHERE datname = $1', name):\n"
        "        await admin.execute(f'CREATE DATABASE \"{name}\"')\n"
        "    await admin.close()\n"
        "    c = await asyncpg.connect(url)\n"
        "    await c.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public')\n"
        "    await c.close()\n"
        "asyncio.run(main())\n"
    )
    subprocess.run([sys.executable, "-c", drop], env=env, check=True)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
    )


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def acquirer() -> FakeAcquirer:
    return FakeAcquirer()


@pytest.fixture
def panel() -> FakePanel:
    return FakePanel()


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
async def rt(
    schema: None,
    settings: Settings,
    clock: FrozenClock,
    acquirer: FakeAcquirer,
    panel: FakePanel,
    notifier: FakeNotifier,
) -> AsyncIterator[Runtime]:
    runtime = build_runtime(
        settings, clock=clock, acquirer=acquirer, panel=panel, notifier=notifier
    )
    async with runtime.engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    await runtime.redis.flushdb()
    yield runtime
    await runtime.close()


@pytest.fixture
async def api(rt: Runtime, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    from vas.web import create_app

    app = create_app(settings, rt=rt)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        yield client
