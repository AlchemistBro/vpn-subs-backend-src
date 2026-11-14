"""Transactional outbox: событие и изменение данных коммитятся вместе.

Relay забирает пачку с FOR UPDATE SKIP LOCKED и отдаёт в Celery. Доставка at-least-once,
поэтому все задачи-получатели идемпотентны.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vas.core.db import transaction
from vas.events.models import OutboxEvent

log = structlog.get_logger()


@dataclass(frozen=True)
class Route:
    task: str
    queue: str


ROUTES: dict[str, Route] = {
    "access.push": Route("access.push", "access"),
    "renewal.charge": Route("billing.charge_renewal", "billing"),
    "notify": Route("notify.send", "notify"),
    "analytics": Route("analytics.track", "analytics"),
}

Publish = Callable[[Route, dict[str, Any]], None]


def emit(session: AsyncSession, topic: str, **payload: Any) -> None:
    if topic not in ROUTES:
        raise ValueError(f"unknown topic {topic}")
    session.add(OutboxEvent(topic=topic, payload=payload))


async def relay(
    sessions: async_sessionmaker[AsyncSession], publish: Publish, *, now: datetime, batch: int = 200
) -> int:
    sent = 0
    async with transaction(sessions) as s:
        rows = (
            await s.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.sent_at.is_(None))
                .order_by(OutboxEvent.id)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            try:
                # send_task у Celery синхронный: уводим в поток, чтобы не стопорить event loop
                await asyncio.to_thread(publish, ROUTES[row.topic], row.payload)
            except Exception as exc:
                # Брокер недоступен: остальные тоже не уйдут. Отмечаем и ждём следующего тика.
                row.tries += 1
                row.last_error = repr(exc)[:256]
                log.warning("outbox_publish_failed", id=row.id, error=row.last_error)
                break
            row.sent_at = now
            sent += 1
    return sent


async def backlog(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions() as s:
        count = await s.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.sent_at.is_(None))
        )
        return int(count or 0)


async def purge_sent(sessions: async_sessionmaker[AsyncSession], *, before: datetime) -> None:
    async with transaction(sessions) as s:
        await s.execute(
            delete(OutboxEvent).where(
                OutboxEvent.sent_at.is_not(None), OutboxEvent.sent_at < before
            )
        )
