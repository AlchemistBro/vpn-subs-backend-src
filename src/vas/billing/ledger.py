"""Запись периода доступа. Единственное место, где меняется subscriptions.paid_until."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from vas.billing.models import AccessPeriod, Subscription
from vas.billing.periods import next_period
from vas.events.outbox import emit


async def lock_subscription(session: AsyncSession, user_id: int) -> Subscription:
    """Строка подписки есть у каждого пользователя; создаём лениво и сразу берём на неё лок."""
    await session.execute(
        insert(Subscription)
        .values(user_id=user_id)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    sub = await session.scalar(
        select(Subscription).where(Subscription.user_id == user_id).with_for_update()
    )
    assert sub is not None
    return sub


async def add_period(
    session: AsyncSession,
    *,
    user_id: int,
    source_type: str,
    source_id: str,
    days: int,
    now: datetime,
) -> AccessPeriod | None:
    """Добавляет период, если за этот источник его ещё не было. Возвращает None для повтора.

    Порядок важен: сначала лок строки подписки, потом расчёт от текущего paid_until.
    Без лока две параллельные оплаты прочитают один paid_until, и один период потеряется.
    """
    sub = await lock_subscription(session, user_id)
    start, end = next_period(now, sub.paid_until, days)
    period_id = await session.scalar(
        insert(AccessPeriod)
        .values(
            user_id=user_id,
            source_type=source_type,
            source_id=source_id,
            days=days,
            starts_at=start,
            ends_at=end,
        )
        .on_conflict_do_nothing(index_elements=["source_type", "source_id"])
        .returning(AccessPeriod.id)
    )
    if period_id is None:
        return None
    sub.paid_until = end
    sub.updated_at = now
    emit(session, "access.push", user_id=user_id)
    emit(session, "analytics", name="period_added", user_id=user_id, source=source_type, days=days)
    return await session.get(AccessPeriod, period_id)
