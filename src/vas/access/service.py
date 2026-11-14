"""Доведение VPN-панели до состояния, которое записано у нас.

Источник правды — наша БД (subscriptions.paid_until). Панель только догоняет: задача
читает желаемое состояние заново при каждом запуске, поэтому повтор и порядок задач не важны.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from redis.asyncio import Redis
from sqlalchemy import Select, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vas.access.models import PanelAccount
from vas.access.panel import Panel
from vas.accounts.models import User
from vas.billing.models import Subscription
from vas.billing.periods import is_active
from vas.core.cache import hold
from vas.core.clock import Clock
from vas.core.db import transaction
from vas.events.outbox import emit

log = structlog.get_logger()


@dataclass(frozen=True)
class Link:
    ready: bool
    url: str | None
    active: bool
    paid_until: datetime | None


class Access:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        panel: Panel,
        clock: Clock,
    ) -> None:
        self.sessions = sessions
        self.redis = redis
        self.panel = panel
        self.clock = clock

    async def push(self, user_id: int) -> str:
        """Синхронизировать пользователя с панелью. Бросает Busy, если параллельно идёт другая."""
        async with hold(self.redis, f"panel:{user_id}", ttl_ms=30_000):
            async with transaction(self.sessions) as s:
                await s.execute(
                    insert(PanelAccount)
                    .values(user_id=user_id)
                    .on_conflict_do_nothing(index_elements=["user_id"])
                )
                account = await s.get(PanelAccount, user_id)
                user = await s.get(User, user_id)
                sub = await s.get(Subscription, user_id)
            assert account is not None
            assert user is not None
            now = self.clock.now()
            until = sub.paid_until if sub and sub.paid_until else now
            enabled = user.status == "active" and is_active(until, now)

            remote = await self.panel.get(account.panel_uuid)
            if remote is None:
                remote = await self.panel.create(
                    account.panel_uuid, username=f"vas{user_id}", expire_at=until, enabled=enabled
                )
                outcome = "created"
            elif remote.expire_at != until or remote.enabled != enabled:
                remote = await self.panel.update(
                    account.panel_uuid, expire_at=until, enabled=enabled
                )
                outcome = "updated"
            else:
                outcome = "in_sync"

            async with transaction(self.sessions) as s:
                row = await s.get(PanelAccount, user_id, with_for_update=True)
                assert row is not None
                row.sub_url = remote.sub_url
                row.synced_until = sub.paid_until if sub else None
                row.synced_at = now
        log.info("panel_push", user_id=user_id, outcome=outcome)
        return outcome

    async def link(self, user_id: int) -> Link:
        now = self.clock.now()
        async with self.sessions() as s:
            sub = await s.get(Subscription, user_id)
            account = await s.get(PanelAccount, user_id)
        paid_until = sub.paid_until if sub else None
        ready = account is not None and account.sub_url is not None
        return Link(
            ready=ready,
            url=account.sub_url if account else None,
            active=is_active(paid_until, now),
            paid_until=paid_until,
        )

    def _lagging(self, changed_before: datetime | None = None) -> Select[tuple[int]]:
        query = (
            select(Subscription.user_id)
            .outerjoin(PanelAccount, PanelAccount.user_id == Subscription.user_id)
            .where(
                Subscription.paid_until.is_not(None),
                or_(
                    PanelAccount.synced_until.is_(None),
                    PanelAccount.synced_until != Subscription.paid_until,
                ),
            )
        )
        if changed_before is not None:
            query = query.where(Subscription.updated_at < changed_before)
        return query

    async def lag(self) -> int:
        async with self.sessions() as s:
            count = await s.scalar(select(func.count()).select_from(self._lagging().subquery()))
        return int(count or 0)

    async def requeue_lagging(self, *, limit: int = 500) -> int:
        """Сверка с панелью: тем, у кого панель отстала дольше 5 минут, ещё раз ставим задачу.

        Свежие изменения не трогаем: их задача, скорее всего, ещё в очереди.
        """
        before = self.clock.now() - timedelta(minutes=5)
        async with transaction(self.sessions) as s:
            ids = (await s.scalars(self._lagging(before).limit(limit))).all()
            for user_id in ids:
                emit(s, "access.push", user_id=user_id)
        return len(ids)
