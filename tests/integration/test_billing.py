"""Оплата разово: создание, вебхуки, повторы, расхождения, сверка."""

import asyncio
from datetime import timedelta

import pytest

from tests.fakes import FakeAcquirer, FrozenClock
from tests.helpers import deliver, hook, payment, periods, signed_in, subscription
from vas.billing.acquirer import BadSignature
from vas.core.problems import UpstreamDown
from vas.runtime import Runtime


async def buy(rt: Runtime, user_id: int, *, key: str = "key-0001", save_card: bool = False):  # type: ignore[no-untyped-def]
    return await rt.billing.checkout(
        user_id=user_id, plan_code="m1", client_key=key, save_card=save_card
    )


async def test_double_delivered_webhook_adds_one_period(
    rt: Runtime, acquirer: FakeAcquirer
) -> None:
    me = await signed_in(rt)
    trial_end = (await subscription(rt, me.user_id)).paid_until
    assert trial_end is not None
    started = await buy(rt, me.user_id)
    charge = acquirer.settle(next(iter(acquirer.charges)), "succeeded")
    body, headers = hook(rt, charge)

    results = await asyncio.gather(*(rt.billing.accept_event(body, headers) for _ in range(5)))

    assert sorted(results) == ["duplicate"] * 4 + ["paid"]
    assert [p.source_type for p in await periods(rt, me.user_id)] == ["trial", "payment"]
    assert (await subscription(rt, me.user_id)).paid_until == trial_end + timedelta(days=30)
    assert (await payment(rt, started.payment_id)).status == "paid"


async def test_different_events_for_same_charge_apply_once(
    rt: Runtime, acquirer: FakeAcquirer
) -> None:
    """Провайдер может прислать про один платёж разные события. Спасает условный UPDATE."""
    me = await signed_in(rt)
    await buy(rt, me.user_id)
    charge = acquirer.settle(next(iter(acquirer.charges)), "succeeded")
    deliveries = [hook(rt, charge, event_id=f"ev-{i}") for i in range(4)]

    results = await asyncio.gather(*(rt.billing.accept_event(b, h) for b, h in deliveries))

    assert sorted(results) == ["already_settled"] * 3 + ["paid"]
    assert len(await periods(rt, me.user_id)) == 2


async def test_checkout_is_idempotent_and_survives_acquirer_outage(
    rt: Runtime, acquirer: FakeAcquirer
) -> None:
    me = await signed_in(rt)
    acquirer.down = True
    with pytest.raises(UpstreamDown):
        await buy(rt, me.user_id)
    acquirer.down = False

    first = await buy(rt, me.user_id)
    again = await buy(rt, me.user_id)

    assert first.payment_id == again.payment_id
    assert first.pay_url == again.pay_url
    assert len(acquirer.charges) == 1


async def test_amount_mismatch_puts_payment_on_hold(rt: Runtime, acquirer: FakeAcquirer) -> None:
    me = await signed_in(rt)
    started = await buy(rt, me.user_id)
    charge = acquirer.settle(next(iter(acquirer.charges)), "succeeded", amount_minor=100)

    result = await deliver(rt, charge)

    assert result == "on_hold"
    p = await payment(rt, started.payment_id)
    assert (p.status, p.hold_reason) == ("on_hold", "amount_mismatch")
    assert [x.source_type for x in await periods(rt, me.user_id)] == ["trial"]


async def test_late_failure_after_success_changes_nothing(
    rt: Runtime, acquirer: FakeAcquirer
) -> None:
    me = await signed_in(rt)
    started = await buy(rt, me.user_id)
    cid = next(iter(acquirer.charges))
    await deliver(rt, acquirer.settle(cid, "succeeded"))

    result = await deliver(rt, acquirer.settle(cid, "failed"))

    assert result == "already_settled"
    assert (await payment(rt, started.payment_id)).status == "paid"


async def test_money_after_decline_goes_to_manual_review(
    rt: Runtime, acquirer: FakeAcquirer
) -> None:
    me = await signed_in(rt)
    started = await buy(rt, me.user_id)
    cid = next(iter(acquirer.charges))
    await deliver(rt, acquirer.settle(cid, "failed"))

    await deliver(rt, acquirer.settle(cid, "succeeded"))

    p = await payment(rt, started.payment_id)
    assert (p.status, p.hold_reason) == ("on_hold", "paid_after_decline")


async def test_reconcile_applies_payment_whose_webhook_was_lost(
    rt: Runtime, acquirer: FakeAcquirer, clock: FrozenClock
) -> None:
    me = await signed_in(rt)
    started = await buy(rt, me.user_id)
    acquirer.settle(next(iter(acquirer.charges)), "succeeded")

    assert (await rt.billing.reconcile())["checked"] == 0  # ещё рано: вебхук может прийти
    clock.shift(minutes=11)
    stats = await rt.billing.reconcile()

    assert stats["settled"] == 1
    assert (await payment(rt, started.payment_id)).status == "paid"


async def test_abandoned_checkout_expires_and_late_payment_still_counts(
    rt: Runtime, acquirer: FakeAcquirer, clock: FrozenClock
) -> None:
    me = await signed_in(rt)
    started = await buy(rt, me.user_id)
    clock.shift(hours=25)
    assert (await rt.billing.reconcile())["expired"] == 1

    cid = next(iter(acquirer.charges))
    result = await deliver(rt, acquirer.settle(cid, "succeeded"))

    assert result == "paid"
    assert (await payment(rt, started.payment_id)).status == "paid"


async def test_referral_bonus_is_given_once(rt: Runtime, acquirer: FakeAcquirer) -> None:
    inviter, _ = await rt.accounts.start_from_bot(5001)
    friend, _ = await rt.accounts.start_from_bot(5002, f"inv{inviter.id}")
    for n in range(2):
        await buy(rt, friend.id, key=f"key-{n:04d}")
    for cid in list(acquirer.charges):
        await deliver(rt, acquirer.settle(cid, "succeeded"))

    sources = [p.source_type for p in await periods(rt, inviter.id)]
    assert sources == ["trial", "referral"]


async def test_forged_webhook_is_rejected(rt: Runtime, acquirer: FakeAcquirer) -> None:
    me = await signed_in(rt)
    await buy(rt, me.user_id)
    charge = acquirer.settle(next(iter(acquirer.charges)), "succeeded")
    body, headers = hook(rt, charge, secret="not-our-secret")

    with pytest.raises(BadSignature):
        await rt.billing.accept_event(body, headers)
