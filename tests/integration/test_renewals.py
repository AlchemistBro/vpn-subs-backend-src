"""Автопродление: план от paid_until, списание в задаче, повторы и отмены."""

from datetime import timedelta

from sqlalchemy import select, update

from tests.fakes import FakeAcquirer, FrozenClock
from tests.helpers import deliver, periods, signed_in, subscription
from vas.accounts.models import User
from vas.billing.models import RenewalAttempt
from vas.runtime import Runtime


async def paid_with_card(rt: Runtime, acquirer: FakeAcquirer) -> int:
    me = await signed_in(rt)
    await rt.billing.checkout(
        user_id=me.user_id, plan_code="m1", client_key="key-first", save_card=True
    )
    cid = next(iter(acquirer.charges))
    charge = acquirer.settle(cid, "succeeded", card="card_ok")
    assert await deliver(rt, charge) == "paid"
    sub = await subscription(rt, me.user_id)
    assert sub.autorenew
    assert sub.card_token == "card_ok"
    return me.user_id


async def near_end(rt: Runtime, clock: FrozenClock, user_id: int, hours: float = 12) -> None:
    sub = await subscription(rt, user_id)
    assert sub.paid_until is not None
    clock.current = sub.paid_until - timedelta(hours=hours)


async def attempts(rt: Runtime, user_id: int) -> list[tuple[int, str]]:
    async with rt.sessions() as s:
        rows = await s.execute(
            select(RenewalAttempt.attempt, RenewalAttempt.state)
            .where(RenewalAttempt.user_id == user_id)
            .order_by(RenewalAttempt.attempt)
        )
        return [(r[0], r[1]) for r in rows]


async def only_attempt_id(rt: Runtime) -> int:
    async with rt.sessions() as s:
        ids = list(await s.scalars(select(RenewalAttempt.id).where(RenewalAttempt.state == "sent")))
    assert len(ids) == 1
    return ids[0]


async def test_renewal_extends_from_current_end(
    rt: Runtime, acquirer: FakeAcquirer, clock: FrozenClock
) -> None:
    user_id = await paid_with_card(rt, acquirer)
    before = (await subscription(rt, user_id)).paid_until
    assert before is not None
    await near_end(rt, clock, user_id)

    assert await rt.billing.plan_renewals() == 1
    assert await rt.billing.plan_renewals() == 0  # повторный запуск cron ничего не задваивает
    result = await rt.billing.charge_renewal(await only_attempt_id(rt))

    assert result == "paid"
    assert (await subscription(rt, user_id)).paid_until == before + timedelta(days=30)
    assert await attempts(rt, user_id) == [(1, "charged")]


async def test_webhook_after_renewal_does_not_extend_twice(
    rt: Runtime, acquirer: FakeAcquirer, clock: FrozenClock
) -> None:
    user_id = await paid_with_card(rt, acquirer)
    await near_end(rt, clock, user_id)
    await rt.billing.plan_renewals()
    await rt.billing.charge_renewal(await only_attempt_id(rt))
    renewal_charge = list(acquirer.charges.values())[-1]

    result = await deliver(rt, renewal_charge)

    assert result == "already_settled"
    assert [p.source_type for p in await periods(rt, user_id)] == ["trial", "payment", "payment"]


async def test_redelivered_task_charges_once(
    rt: Runtime, acquirer: FakeAcquirer, clock: FrozenClock
) -> None:
    user_id = await paid_with_card(rt, acquirer)
    await near_end(rt, clock, user_id)
    await rt.billing.plan_renewals()
    attempt_id = await only_attempt_id(rt)

    await rt.billing.charge_renewal(attempt_id)
    second = await rt.billing.charge_renewal(attempt_id)

    assert second == "charged"
    assert acquirer.calls.count("charge_card") == 1


async def test_skips_cycle_that_user_already_extended(
    rt: Runtime, acquirer: FakeAcquirer, clock: FrozenClock
) -> None:
    user_id = await paid_with_card(rt, acquirer)
    await near_end(rt, clock, user_id)
    await rt.billing.plan_renewals()
    await rt.billing.grant(user_id=user_id, days=7, request_id="support-ticket-42")

    result = await rt.billing.charge_renewal(await only_attempt_id(rt))

    assert result == "skipped:cycle_changed"
    assert "charge_card" not in acquirer.calls


async def test_blocked_user_is_not_charged(
    rt: Runtime, acquirer: FakeAcquirer, clock: FrozenClock
) -> None:
    user_id = await paid_with_card(rt, acquirer)
    await near_end(rt, clock, user_id)
    async with rt.sessions() as s, s.begin():
        await s.execute(update(User).where(User.id == user_id).values(status="blocked"))

    assert await rt.billing.plan_renewals() == 0
    assert "charge_card" not in acquirer.calls


async def test_declines_retry_then_turn_autorenew_off(
    rt: Runtime, acquirer: FakeAcquirer, clock: FrozenClock
) -> None:
    user_id = await paid_with_card(rt, acquirer)
    acquirer.decline_cards = True
    await near_end(rt, clock, user_id)

    for hours in (0, 24, 72):
        clock.shift(hours=hours)
        assert await rt.billing.plan_renewals() == 1
        assert await rt.billing.charge_renewal(await only_attempt_id(rt)) == "declined"

    assert await attempts(rt, user_id) == [(1, "declined"), (2, "declined"), (3, "declined")]
    assert (await subscription(rt, user_id)).autorenew is False


async def test_lost_charge_response_is_resolved_by_reconcile(
    rt: Runtime, acquirer: FakeAcquirer, clock: FrozenClock
) -> None:
    user_id = await paid_with_card(rt, acquirer)
    before = (await subscription(rt, user_id)).paid_until
    assert before is not None
    await near_end(rt, clock, user_id)
    await rt.billing.plan_renewals()
    acquirer.lose_next_response = True

    assert await rt.billing.charge_renewal(await only_attempt_id(rt)) == "unknown"
    clock.shift(minutes=15)
    await rt.billing.reconcile()

    assert (await subscription(rt, user_id)).paid_until == before + timedelta(days=30)
    assert await attempts(rt, user_id) == [(1, "charged")]
    assert acquirer.calls.count("charge_card") == 1
