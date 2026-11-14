"""Выбор канала и текст уведомления. Задача Celery вызывает deliver()."""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vas.accounts.models import Device, User
from vas.core.db import transaction
from vas.notify.channels import Notifier

TEXTS: dict[str, tuple[str, str]] = {
    "paid": ("Оплата прошла", "Доступ продлён на {days} дн. Спасибо!"),
    "renewal_failed": ("Не получилось продлить", "Не прошло списание с карты. Попробуем ещё раз."),
    "autorenew_stopped": (
        "Автопродление выключено",
        "Три попытки списания не прошли. Оплатите вручную, чтобы VPN не отключился.",
    ),
}


async def deliver(
    sessions: async_sessionmaker[AsyncSession],
    notifier: Notifier,
    *,
    user_id: int,
    template: str,
    params: dict[str, Any],
) -> str:
    title, body = TEXTS[template]
    body = body.format(**params)
    async with sessions() as s:
        user = await s.get(User, user_id)
        tokens = await _push_tokens(s, user_id)
    if user is None:
        return "no_user"
    if user.telegram_id is not None:
        await notifier.telegram(user.telegram_id, f"{title}\n{body}")
        return "telegram"
    if tokens:
        for device_id, token in tokens:
            if not await notifier.push(token, title, body):
                async with transaction(sessions) as s:
                    await s.execute(
                        update(Device).where(Device.id == device_id).values(push_token=None)
                    )
        return "push"
    if user.email is not None:
        await notifier.email(user.email, title, body)
        return "email"
    return "no_channel"


async def _push_tokens(s: AsyncSession, user_id: int) -> list[tuple[Any, str]]:
    rows = await s.execute(
        select(Device.id, Device.push_token).where(
            Device.user_id == user_id, Device.push_token.is_not(None)
        )
    )
    return [(r[0], r[1]) for r in rows]
