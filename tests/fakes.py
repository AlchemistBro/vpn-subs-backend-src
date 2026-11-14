"""Подмены внешних систем. Postgres и Redis в тестах настоящие."""

import asyncio
import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from vas.access.panel import PanelUser
from vas.billing.acquirer import Charge
from vas.core.problems import UpstreamDown
from vas.core.secrets import sign_body


class FrozenClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime.now(UTC).replace(microsecond=0)

    def now(self) -> datetime:
        return self.current

    def shift(self, **delta: float) -> None:
        self.current += timedelta(**delta)


@dataclass
class FakeAcquirer:
    charges: dict[str, Charge] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    decline_cards: bool = False
    down: bool = False
    lose_next_response: bool = False  # списание прошло, а ответ до нас не дошёл

    async def create_charge(
        self, *, order_id: str, amount_minor: int, currency: str, title: str, remember_card: bool
    ) -> Charge:
        self.calls.append("create")
        if self.down:
            raise UpstreamDown("acquirer is down")
        for c in self.charges.values():
            if c.order_id == order_id:
                return c  # тот же ключ идемпотентности — тот же платёж
        charge = Charge(
            id=f"ch_{uuid.uuid4().hex[:10]}",
            order_id=order_id,
            status="pending",
            amount_minor=amount_minor,
            currency=currency,
            checkout_url=f"https://pay.test/{order_id}",
        )
        self.charges[charge.id] = charge
        return charge

    async def charge_card(
        self, *, order_id: str, amount_minor: int, currency: str, card_token: str
    ) -> Charge:
        self.calls.append("charge_card")
        if self.down:
            raise UpstreamDown("acquirer timeout")
        for c in self.charges.values():
            if c.order_id == order_id:
                return c
        charge = Charge(
            id=f"ch_{uuid.uuid4().hex[:10]}",
            order_id=order_id,
            status="failed" if self.decline_cards else "succeeded",
            amount_minor=amount_minor,
            currency=currency,
            card_token=card_token,
            card_title="Visa •• 4242",
        )
        self.charges[charge.id] = charge
        if self.lose_next_response:
            self.lose_next_response = False
            raise UpstreamDown("read timeout")
        return charge

    async def get_charge(self, charge_id: str) -> Charge | None:
        if self.down:
            raise UpstreamDown("acquirer is down")
        return self.charges.get(charge_id)

    async def find_charge(self, order_id: str) -> Charge | None:
        if self.down:
            raise UpstreamDown("acquirer is down")
        return next((c for c in self.charges.values() if c.order_id == order_id), None)

    def settle(self, charge_id: str, status: str, *, card: str | None = None, **kw: Any) -> Charge:
        """Пользователь заплатил (или банк отказал) на странице эквайринга."""
        charge = replace(
            self.charges[charge_id],
            status=status,
            card_token=card,
            card_title="Visa •• 4242" if card else None,
            **kw,
        )
        self.charges[charge_id] = charge
        return charge


@dataclass
class FakePanel:
    users: dict[uuid.UUID, PanelUser] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    fail_next: int = 0
    lose_create_response: bool = False

    def _maybe_fail(self) -> None:
        if self.fail_next:
            self.fail_next -= 1
            raise UpstreamDown("panel 503")

    async def get(self, user_uuid: uuid.UUID) -> PanelUser | None:
        self.calls.append("get")
        self._maybe_fail()
        return self.users.get(user_uuid)

    async def create(
        self, user_uuid: uuid.UUID, *, username: str, expire_at: datetime, enabled: bool
    ) -> PanelUser:
        self.calls.append("create")
        self._maybe_fail()
        if user_uuid in self.users:
            return await self.update(user_uuid, expire_at=expire_at, enabled=enabled)
        user = PanelUser(user_uuid, expire_at, enabled, f"https://sub.test/{user_uuid.hex[:8]}")
        self.users[user_uuid] = user
        if self.lose_create_response:
            self.lose_create_response = False
            raise UpstreamDown("panel timeout after create")
        return user

    async def update(
        self, user_uuid: uuid.UUID, *, expire_at: datetime, enabled: bool
    ) -> PanelUser:
        self.calls.append("update")
        self._maybe_fail()
        user = replace(self.users[user_uuid], expire_at=expire_at, enabled=enabled)
        self.users[user_uuid] = user
        return user


@dataclass
class FakeNotifier:
    sent: list[tuple[str, Any, str]] = field(default_factory=list)

    async def telegram(self, chat_id: int, text: str) -> None:
        self.sent.append(("telegram", chat_id, text))

    async def email(self, to: str, subject: str, text: str) -> None:
        self.sent.append(("email", to, text))

    async def push(self, device_token: str, title: str, body: str) -> bool:
        self.sent.append(("push", device_token, body))
        return True

    def last_email_code(self, to: str) -> str:
        text = next(t for ch, addr, t in reversed(self.sent) if ch == "email" and addr == to)
        return "".join(c for c in text.split(":")[1] if c.isdigit())[:6]


def signed_event(
    secret: str, charge: Charge, *, at: datetime, event_id: str | None = None
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {
            "id": event_id or f"ev_{charge.id}_{charge.status}",
            "type": f"charge.{charge.status}",
            "data": {
                "id": charge.id,
                "order_id": charge.order_id,
                "status": charge.status,
                "amount_minor": charge.amount_minor,
                "currency": charge.currency,
                "card": {"token": charge.card_token, "title": charge.card_title, "reusable": True}
                if charge.card_token
                else None,
            },
        }
    ).encode()
    ts = str(int(at.timestamp()))
    return body, {"x-acq-timestamp": ts, "x-acq-signature": sign_body(secret, ts, body)}


async def gather(*aws: Any) -> list[Any]:
    return list(await asyncio.gather(*aws, return_exceptions=True))
