"""Синхронизация с VPN-панелью."""

import pytest

from tests.fakes import FakePanel, FrozenClock
from tests.helpers import outbox_topics, signed_in
from vas.access.models import PanelAccount
from vas.core.cache import Busy, hold
from vas.core.problems import UpstreamDown
from vas.runtime import Runtime


async def test_new_user_gets_panel_account_with_our_uuid(rt: Runtime, panel: FakePanel) -> None:
    me = await signed_in(rt)
    assert (await rt.access.link(me.user_id)).ready is False

    assert await rt.access.push(me.user_id) == "created"

    async with rt.sessions() as s:
        account = await s.get(PanelAccount, me.user_id)
    assert account is not None
    assert account.panel_uuid in panel.users
    link = await rt.access.link(me.user_id)
    assert link.ready
    assert link.active
    assert link.url == panel.users[account.panel_uuid].sub_url


async def test_lost_create_response_does_not_duplicate_user(rt: Runtime, panel: FakePanel) -> None:
    me = await signed_in(rt)
    panel.lose_create_response = True
    with pytest.raises(UpstreamDown):
        await rt.access.push(me.user_id)

    # uuid выдали мы, поэтому повтор находит уже созданного пользователя, а не создаёт второго
    assert await rt.access.push(me.user_id) == "in_sync"
    assert len(panel.users) == 1
    assert (await rt.access.link(me.user_id)).ready


async def test_second_push_is_noop(rt: Runtime, panel: FakePanel) -> None:
    me = await signed_in(rt)
    await rt.access.push(me.user_id)
    assert await rt.access.push(me.user_id) == "in_sync"


async def test_expired_access_is_disabled_in_panel(
    rt: Runtime, panel: FakePanel, clock: FrozenClock
) -> None:
    me = await signed_in(rt)
    await rt.access.push(me.user_id)
    clock.shift(days=4)  # триал кончился
    await rt.access.push(me.user_id)
    assert next(iter(panel.users.values())).enabled is False


async def test_parallel_push_for_same_user_is_refused(rt: Runtime) -> None:
    me = await signed_in(rt)
    async with hold(rt.redis, f"panel:{me.user_id}"):
        with pytest.raises(Busy):
            await rt.access.push(me.user_id)


async def test_lagging_users_are_requeued_after_a_while(rt: Runtime, clock: FrozenClock) -> None:
    me = await signed_in(rt)
    assert await rt.access.lag() == 1
    assert await rt.access.requeue_lagging() == 0  # свежие не трогаем
    clock.shift(minutes=6)
    before = (await outbox_topics(rt)).count("access.push")

    assert await rt.access.requeue_lagging() == 1
    assert (await outbox_topics(rt)).count("access.push") == before + 1
    await rt.access.push(me.user_id)
    assert await rt.access.lag() == 0
