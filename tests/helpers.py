import uuid
from typing import Any

from sqlalchemy import select

from tests.fakes import signed_event
from vas.accounts.service import DeviceInfo, Tokens
from vas.billing.acquirer import Charge
from vas.billing.models import AccessPeriod, Payment, Subscription
from vas.events.models import OutboxEvent
from vas.runtime import Runtime


def device(n: int = 1, **kw: Any) -> DeviceInfo:
    return DeviceInfo(install_id=f"install-{n:04d}", platform="android", title=f"Phone {n}", **kw)


def device_json(n: int = 1) -> dict[str, Any]:
    return {"install_id": f"install-{n:04d}", "platform": "android", "title": f"Phone {n}"}


async def signed_in(rt: Runtime, telegram_id: int = 1001, n: int = 1) -> Tokens:
    code = await rt.accounts.bot_login_code(telegram_id)
    return await rt.accounts.login_with_bot_code(code, device(n))


def bearer(tokens: Tokens) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens.access}"}


async def subscription(rt: Runtime, user_id: int) -> Subscription:
    async with rt.sessions() as s:
        sub = await s.get(Subscription, user_id)
    assert sub is not None
    return sub


async def periods(rt: Runtime, user_id: int) -> list[AccessPeriod]:
    async with rt.sessions() as s:
        rows = await s.scalars(
            select(AccessPeriod).where(AccessPeriod.user_id == user_id).order_by(AccessPeriod.id)
        )
        return list(rows)


async def payment(rt: Runtime, payment_id: uuid.UUID) -> Payment:
    async with rt.sessions() as s:
        p = await s.get(Payment, payment_id)
    assert p is not None
    return p


async def outbox_topics(rt: Runtime) -> list[str]:
    async with rt.sessions() as s:
        return list(await s.scalars(select(OutboxEvent.topic).order_by(OutboxEvent.id)))


def hook(
    rt: Runtime, charge: Charge, *, event_id: str | None = None, secret: str | None = None
) -> tuple[bytes, dict[str, str]]:
    key = secret or rt.settings.acquirer_hook_secret.get_secret_value()
    return signed_event(key, charge, at=rt.clock.now(), event_id=event_id)


async def deliver(rt: Runtime, charge: Charge, *, event_id: str | None = None) -> str:
    return await rt.billing.accept_event(*hook(rt, charge, event_id=event_id))
