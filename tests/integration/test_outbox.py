from typing import Any

from sqlalchemy import select

from tests.fakes import FrozenClock
from tests.helpers import signed_in
from vas.events import outbox
from vas.events.models import OutboxEvent
from vas.runtime import Runtime


async def test_relay_publishes_and_survives_broker_outage(rt: Runtime, clock: FrozenClock) -> None:
    await signed_in(rt)  # регистрация пишет события в outbox
    published: list[tuple[str, dict[str, Any]]] = []
    broker_up = False

    def publish(route: outbox.Route, payload: dict[str, Any]) -> None:
        if not broker_up:
            raise ConnectionError("broker is down")
        published.append((route.task, payload))

    assert await outbox.relay(rt.sessions, publish, now=clock.now()) == 0
    async with rt.sessions() as s:
        first = await s.scalar(select(OutboxEvent).order_by(OutboxEvent.id).limit(1))
    assert first is not None
    assert first.tries == 1
    assert first.sent_at is None

    broker_up = True
    total = await outbox.backlog(rt.sessions)
    assert await outbox.relay(rt.sessions, publish, now=clock.now()) == total
    assert await outbox.backlog(rt.sessions) == 0
    assert ("access.push", {"user_id": 1}) in published
