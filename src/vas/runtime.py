"""Сборка зависимостей процесса. Каждый процесс (api, jobs, cron, bot) собирает свой Runtime.

Внешние системы можно подменить: так тесты работают с настоящими Postgres и Redis,
но с фейковыми эквайрингом и панелью.
"""

from dataclasses import dataclass, field
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from vas.access.panel import HttpPanel, Panel
from vas.access.service import Access
from vas.accounts.service import Accounts
from vas.billing.acquirer import Acquirer, HttpAcquirer
from vas.billing.service import Billing
from vas.core.clock import Clock, SystemClock
from vas.core.db import make_engine, make_sessions
from vas.notify.channels import HttpNotifier, Notifier
from vas.settings import Settings


@dataclass
class Runtime:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    redis: Redis
    clock: Clock
    acquirer: Acquirer
    panel: Panel
    notifier: Notifier
    accounts: Accounts
    billing: Billing
    access: Access
    _closers: list[Any] = field(default_factory=list)

    async def close(self) -> None:
        for item in self._closers:
            await item.aclose()
        await self.redis.aclose()
        await self.engine.dispose()


def build_runtime(
    settings: Settings,
    *,
    clock: Clock | None = None,
    acquirer: Acquirer | None = None,
    panel: Panel | None = None,
    notifier: Notifier | None = None,
) -> Runtime:
    engine = make_engine(settings)
    sessions = make_sessions(engine)
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    clock = clock or SystemClock()
    closers: list[Any] = []
    if acquirer is None:
        acquirer = HttpAcquirer(
            settings.acquirer_url,
            settings.acquirer_shop,
            settings.acquirer_key.get_secret_value(),
            settings.return_url,
        )
        closers.append(acquirer)
    if panel is None:
        panel = HttpPanel(
            settings.panel_url,
            settings.panel_key.get_secret_value(),
            parallel=settings.panel_parallel,
        )
        closers.append(panel)
    if notifier is None:
        notifier = HttpNotifier(settings)
        closers.append(notifier)
    return Runtime(
        settings=settings,
        engine=engine,
        sessions=sessions,
        redis=redis,
        clock=clock,
        acquirer=acquirer,
        panel=panel,
        notifier=notifier,
        accounts=Accounts(sessions, redis, notifier, clock, settings),
        billing=Billing(sessions, redis, acquirer, clock, settings),
        access=Access(sessions, redis, panel, clock),
        _closers=closers,
    )
