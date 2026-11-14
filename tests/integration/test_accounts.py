"""Вход, устройства-слоты, refresh-токены."""

import asyncio

import pytest

from tests.fakes import FakeNotifier, FrozenClock
from tests.helpers import device, periods, signed_in
from vas.accounts.service import SlotsFull
from vas.core.problems import NotFound, TooManyRequests, Unauthorized
from vas.runtime import Runtime


async def test_parallel_starts_create_one_user_with_one_trial(rt: Runtime) -> None:
    results = await asyncio.gather(*(rt.accounts.start_from_bot(777) for _ in range(8)))

    assert len({user.id for user, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    assert [p.source_type for p in await periods(rt, results[0][0].id)] == ["trial"]


async def test_bot_code_is_single_use(rt: Runtime) -> None:
    code = await rt.accounts.bot_login_code(42)
    await rt.accounts.login_with_bot_code(code.lower(), device(1))  # регистр и дефис не важны

    with pytest.raises(Unauthorized):
        await rt.accounts.login_with_bot_code(code, device(2))


async def test_bot_code_expires(rt: Runtime, clock: FrozenClock) -> None:
    code = await rt.accounts.bot_login_code(42)
    clock.shift(minutes=6)
    with pytest.raises(Unauthorized):
        await rt.accounts.login_with_bot_code(code, device(1))


async def test_email_login_and_wrong_code_limit(
    rt: Runtime, notifier: FakeNotifier, clock: FrozenClock
) -> None:
    await rt.accounts.send_email_code("Nik@Example.com")
    code = notifier.last_email_code("nik@example.com")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        with pytest.raises(Unauthorized):
            await rt.accounts.login_with_email("nik@example.com", wrong, device(1))

    # Счётчик пережил откат: шестая попытка не проходит даже с верным кодом
    with pytest.raises(TooManyRequests):
        await rt.accounts.login_with_email("nik@example.com", code, device(1))


async def test_email_code_works_once_and_creates_trial(rt: Runtime, notifier: FakeNotifier) -> None:
    await rt.accounts.send_email_code("a@example.com")
    tokens = await rt.accounts.login_with_email(
        "a@example.com", notifier.last_email_code("a@example.com"), device(1)
    )
    assert [p.source_type for p in await periods(rt, tokens.user_id)] == ["trial"]


async def test_email_code_rate_limit(rt: Runtime) -> None:
    await rt.accounts.send_email_code("b@example.com")
    with pytest.raises(TooManyRequests):
        await rt.accounts.send_email_code("b@example.com")


async def test_parallel_logins_respect_device_slots(rt: Runtime) -> None:
    codes = [await rt.accounts.bot_login_code(9) for _ in range(5)]
    await rt.redis.flushdb()  # лимит — 5 кодов за 10 минут, вторую пятёрку выпускаем после сброса
    codes += [await rt.accounts.bot_login_code(9) for _ in range(5)]

    results = await asyncio.gather(
        *(rt.accounts.login_with_bot_code(c, device(n)) for n, c in enumerate(codes)),
        return_exceptions=True,
    )

    ok = [r for r in results if not isinstance(r, BaseException)]
    assert len(ok) == 5
    assert all(isinstance(r, SlotsFull) for r in results if isinstance(r, BaseException))
    assert len(await rt.accounts.devices(ok[0].user_id)) == 5


async def test_full_slots_can_be_freed_at_login(rt: Runtime) -> None:
    first = await signed_in(rt, 10, n=1)
    for n in range(2, 6):
        await signed_in(rt, 10, n=n)
    await rt.redis.flushdb()  # сбрасываем лимит на выпуск кодов
    code = await rt.accounts.bot_login_code(10)

    tokens = await rt.accounts.login_with_bot_code(code, device(6, replace=first.device_id))

    titles = [d.title for d in await rt.accounts.devices(tokens.user_id)]
    assert "Phone 1" not in titles
    assert "Phone 6" in titles
    with pytest.raises(Unauthorized):
        await rt.accounts.refresh(first.refresh)


async def test_refresh_rotates_and_reuse_kills_the_chain(rt: Runtime) -> None:
    login = await signed_in(rt)
    rotated = await rt.accounts.refresh(login.refresh)

    with pytest.raises(Unauthorized, match="reuse"):
        await rt.accounts.refresh(login.refresh)  # украденный старый токен
    with pytest.raises(Unauthorized):
        await rt.accounts.refresh(rotated.refresh)  # и легитимный тоже больше не работает


async def test_refresh_expires(rt: Runtime, clock: FrozenClock) -> None:
    login = await signed_in(rt)
    clock.shift(days=31)
    with pytest.raises(Unauthorized):
        await rt.accounts.refresh(login.refresh)


async def test_cannot_remove_someone_elses_device(rt: Runtime) -> None:
    alice = await signed_in(rt, 1)
    bob = await signed_in(rt, 2)
    with pytest.raises(NotFound):
        await rt.accounts.remove_device(bob.user_id, alice.device_id)
    assert len(await rt.accounts.devices(alice.user_id)) == 1


async def test_session_check_after_device_removal(rt: Runtime) -> None:
    me = await signed_in(rt)
    await rt.accounts.check_session(me.user_id, me.device_id)
    await rt.accounts.remove_device(me.user_id, me.device_id)
    with pytest.raises(Unauthorized):
        await rt.accounts.check_session(me.user_id, me.device_id)
