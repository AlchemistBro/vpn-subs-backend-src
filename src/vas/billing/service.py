"""Оплаты и автопродление.

Главные правила:
* статус платежа меняется только условным UPDATE (compare-and-set): «перевести в paid,
  если он ещё new/awaiting». Ноль строк — значит, кто-то уже обработал, выходим;
* период доступа добавляется через журнал с уникальным источником, второй раз не добавится;
* автопродление планируется от текущего paid_until: цикл — это конкретная дата окончания.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import DateTime, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vas.accounts.models import User
from vas.billing import money
from vas.billing.acquirer import Acquirer, Charge, parse_event
from vas.billing.ledger import add_period, lock_subscription
from vas.billing.models import (
    AccessPeriod,
    Payment,
    Plan,
    ProviderEvent,
    RenewalAttempt,
    Subscription,
)
from vas.billing.periods import RENEW_GRACE, retry_after
from vas.core.cache import remember
from vas.core.clock import Clock
from vas.core.db import transaction
from vas.core.metrics import PAYMENTS_APPLIED, PAYMENTS_HELD, RENEWALS
from vas.core.problems import Conflict, NotFound, Problem, UpstreamDown
from vas.events.outbox import emit
from vas.settings import Settings

log = structlog.get_logger()

OPEN = ("new", "awaiting")
STALE_AWAITING = timedelta(minutes=10)
ABANDONED = timedelta(hours=24)


@dataclass(frozen=True)
class CheckoutResult:
    payment_id: uuid.UUID
    status: str
    pay_url: str | None


class Billing:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        acquirer: Acquirer,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self.sessions = sessions
        self.redis = redis
        self.acquirer = acquirer
        self.clock = clock
        self.settings = settings

    # ------------------------------------------------------------------ тарифы

    async def plans(self) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            async with self.sessions() as s:
                rows = await s.scalars(select(Plan).where(Plan.on_sale).order_by(Plan.days))
                return [
                    {
                        "code": p.code,
                        "title": p.title,
                        "days": p.days,
                        "price": money.to_str(p.price_minor),
                        "currency": p.currency,
                    }
                    for p in rows
                ]

        result: list[dict[str, Any]] = await remember(self.redis, "plans:v1", 300, load)
        return result

    # ------------------------------------------------------------------ покупка

    async def checkout(
        self, *, user_id: int, plan_code: str, client_key: str, save_card: bool
    ) -> CheckoutResult:
        """Двухшаговая запись: сначала наш платёж (new), потом запрос в эквайринг.

        Повтор с тем же ключом клиента вернёт тот же платёж. Если эквайринг не ответил,
        платёж остаётся new, а повторный запрос клиента дойдёт до эквайринга с тем же ключом.
        """
        async with transaction(self.sessions) as s:
            plan = await s.get(Plan, plan_code)
            if plan is None or not plan.on_sale:
                raise NotFound("plan not found", plan=plan_code)
            await s.execute(
                insert(Payment)
                .values(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    plan_code=plan.code,
                    purpose="checkout",
                    client_key=client_key,
                    amount_minor=plan.price_minor,
                    currency=plan.currency,
                    save_card=save_card,
                )
                .on_conflict_do_nothing(index_elements=["user_id", "client_key"])
            )
            payment = await s.scalar(
                select(Payment).where(Payment.user_id == user_id, Payment.client_key == client_key)
            )
            assert payment is not None
            if payment.plan_code != plan_code:
                raise Conflict("idempotency key reused with another plan")
            title = plan.title

        if payment.status != "new":
            return CheckoutResult(payment.id, payment.status, payment.pay_url)

        charge = await self.acquirer.create_charge(
            order_id=str(payment.id),
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            title=title,
            remember_card=save_card,
        )
        async with transaction(self.sessions) as s:
            await s.execute(
                update(Payment)
                .where(Payment.id == payment.id, Payment.status == "new")
                .values(
                    status="awaiting",
                    provider_id=charge.id,
                    pay_url=charge.checkout_url,
                    updated_at=self.clock.now(),
                )
            )
            emit(s, "analytics", name="checkout_started", user_id=user_id, plan=plan_code)
        return CheckoutResult(payment.id, "awaiting", charge.checkout_url)

    async def payment(self, *, user_id: int, payment_id: uuid.UUID) -> Payment:
        async with self.sessions() as s:
            payment = await s.scalar(
                select(Payment).where(Payment.id == payment_id, Payment.user_id == user_id)
            )
        if payment is None:
            raise NotFound("payment not found")
        return payment

    async def set_autorenew(self, *, user_id: int, enabled: bool) -> Subscription:
        async with transaction(self.sessions) as s:
            sub = await lock_subscription(s, user_id)
            if enabled and sub.card_token is None:
                raise Conflict("no saved card", hint="pay once with save_card=true")
            sub.autorenew = enabled
            sub.updated_at = self.clock.now()
        return sub

    async def overview(self, user_id: int) -> tuple[Subscription, list[AccessPeriod]]:
        async with transaction(self.sessions) as s:
            sub = await lock_subscription(s, user_id)
            periods = (
                await s.scalars(
                    select(AccessPeriod)
                    .where(AccessPeriod.user_id == user_id)
                    .order_by(AccessPeriod.starts_at.desc())
                    .limit(20)
                )
            ).all()
        return sub, list(periods)

    # ------------------------------------------------------------------ события эквайринга

    async def accept_event(self, body: bytes, headers: dict[str, str]) -> str:
        """Вебхук. Запись события и применение платежа — одна транзакция.

        Если применение упало, откатится и запись события: провайдер повторит доставку,
        и мы попробуем снова. Повтор уже применённого события отсечёт уникальный event_id.
        """
        event = parse_event(
            self.settings.acquirer_hook_secret.get_secret_value(),
            body,
            {k.lower(): v for k, v in headers.items()},
            now_ts=self.clock.now().timestamp(),
        )
        async with transaction(self.sessions) as s:
            stored = await s.scalar(
                insert(ProviderEvent)
                .values(provider="acquirer", event_id=event.id, kind=event.kind, body=event.raw)
                .on_conflict_do_nothing(index_elements=["provider", "event_id"])
                .returning(ProviderEvent.id)
            )
            if stored is None:
                return "duplicate"
            return await self._settle(s, event.charge, origin="webhook")

    async def _settle(self, s: AsyncSession, charge: Charge, *, origin: str) -> str:
        payment = await s.scalar(select(Payment).where(Payment.id == _as_uuid(charge.order_id)))
        if payment is None:
            log.warning("charge_for_unknown_payment", charge=charge.id, order=charge.order_id)
            return "unknown_payment"
        if charge.status == "succeeded":
            return await self._paid(s, payment, charge, origin=origin)
        if charge.status == "failed":
            return await self._declined(s, payment, origin=origin)
        return "pending"

    async def _move(
        self,
        s: AsyncSession,
        payment_id: uuid.UUID,
        to: str,
        allowed: tuple[str, ...],
        **values: Any,
    ) -> bool:
        moved = await s.scalar(
            update(Payment)
            .where(Payment.id == payment_id, Payment.status.in_(allowed))
            .values(status=to, updated_at=self.clock.now(), **values)
            .returning(Payment.id)
        )
        return moved is not None

    async def _paid(self, s: AsyncSession, payment: Payment, charge: Charge, *, origin: str) -> str:
        now = self.clock.now()
        reason = money.mismatch(
            payment.amount_minor, payment.currency, charge.amount_minor, charge.currency
        )
        if payment.status == "declined":
            # Отказ уже обработан, а деньги всё-таки списались. Автоматически не решаем.
            reason = "paid_after_decline"
        if reason is not None:
            if await self._move(
                s, payment.id, "on_hold", (*OPEN, "declined", "expired"), hold_reason=reason
            ):
                PAYMENTS_HELD.labels(reason).inc()
                log.error("payment_on_hold", payment=str(payment.id), reason=reason)
            return "on_hold"

        # expired тоже разрешён: пользователь мог оплатить страницу, которую мы уже списали в архив
        if not await self._move(
            s, payment.id, "paid", (*OPEN, "expired"), paid_at=now, provider_id=charge.id
        ):
            return "already_settled"

        plan = await s.get(Plan, payment.plan_code)
        assert plan is not None
        await add_period(
            s,
            user_id=payment.user_id,
            source_type="payment",
            source_id=str(payment.id),
            days=plan.days,
            now=now,
        )
        sub = await lock_subscription(s, payment.user_id)
        sub.plan_code = plan.code
        if charge.card_token and (payment.save_card or payment.purpose == "renewal"):
            sub.card_token = charge.card_token
            sub.card_title = charge.card_title
            if payment.save_card:
                sub.autorenew = True
        if payment.renewal_id is not None:
            await s.execute(
                update(RenewalAttempt)
                .where(RenewalAttempt.id == payment.renewal_id)
                .values(state="charged")
            )
            RENEWALS.labels("charged").inc()

        user = await s.get(User, payment.user_id)
        if user is not None and user.invited_by is not None:
            # Бонус пригласившему один раз: источник — id приглашённого, а не платёж.
            await add_period(
                s,
                user_id=user.invited_by,
                source_type="referral",
                source_id=str(user.id),
                days=self.settings.referral_days,
                now=now,
            )
        emit(s, "notify", user_id=payment.user_id, template="paid", params={"days": plan.days})
        emit(s, "analytics", name="payment_paid", user_id=payment.user_id, origin=origin)
        PAYMENTS_APPLIED.labels(origin).inc()
        log.info("payment_paid", payment=str(payment.id), origin=origin)
        return "paid"

    async def _declined(self, s: AsyncSession, payment: Payment, *, origin: str) -> str:
        if not await self._move(s, payment.id, "declined", OPEN):
            return "already_settled"
        log.info("payment_declined", payment=str(payment.id), origin=origin)
        if payment.renewal_id is not None:
            await self._renewal_failed(s, payment.renewal_id, payment.user_id, note="declined")
        return "declined"

    # ------------------------------------------------------------------ автопродление

    async def plan_renewals(self) -> int:
        """Раз в час: создать попытки для подписок, которые скоро кончатся, и отдать готовые.

        Ключ попытки — (user_id, cycle_end, attempt). Повторный запуск cron ничего не задвоит,
        а если пользователь продлил сам, cycle_end поменяется и старая попытка станет лишней.
        """
        now = self.clock.now()
        window = timedelta(hours=self.settings.renew_window_hours)
        due = (
            select(
                Subscription.user_id,
                Subscription.paid_until,
                literal(1),
                literal(now, DateTime(timezone=True)),
            )
            .join(User, User.id == Subscription.user_id)
            .where(
                Subscription.autorenew,
                Subscription.card_token.is_not(None),
                User.status == "active",
                Subscription.paid_until > now - RENEW_GRACE,
                Subscription.paid_until <= now + window,
            )
        )
        async with transaction(self.sessions) as s:
            await s.execute(
                insert(RenewalAttempt)
                .from_select(["user_id", "cycle_end", "attempt", "run_after"], due)
                .on_conflict_do_nothing(index_elements=["user_id", "cycle_end", "attempt"])
            )
            ready = (
                await s.scalars(
                    update(RenewalAttempt)
                    .where(RenewalAttempt.state == "planned", RenewalAttempt.run_after <= now)
                    .values(state="sent")
                    .returning(RenewalAttempt.id)
                )
            ).all()
            for attempt_id in ready:
                emit(s, "renewal.charge", attempt_id=attempt_id)
        return len(ready)

    async def charge_renewal(self, attempt_id: int) -> str:
        """Задача Celery. Может прийти дважды (acks_late), поэтому сначала ищем свой платёж."""
        now = self.clock.now()
        async with transaction(self.sessions) as s:
            attempt = await s.scalar(
                select(RenewalAttempt).where(RenewalAttempt.id == attempt_id).with_for_update()
            )
            if attempt is None or attempt.state != "sent":
                return attempt.state if attempt else "missing"
            client_key = f"renewal:{attempt.id}"
            payment = await s.scalar(
                select(Payment).where(
                    Payment.user_id == attempt.user_id, Payment.client_key == client_key
                )
            )
            sub = await lock_subscription(s, attempt.user_id)
            if payment is None:
                skip = await self._skip_reason(s, sub, attempt)
                if skip is not None:
                    attempt.state = "skipped"
                    attempt.note = skip
                    RENEWALS.labels("skipped").inc()
                    return f"skipped:{skip}"
                plan = await s.get(Plan, sub.plan_code)
                assert plan is not None
                payment = Payment(
                    id=uuid.uuid4(),
                    user_id=attempt.user_id,
                    plan_code=plan.code,
                    purpose="renewal",
                    client_key=client_key,
                    amount_minor=plan.price_minor,
                    currency=plan.currency,
                    renewal_id=attempt.id,
                    status="new",
                )
                s.add(payment)
            elif payment.status not in OPEN:
                return payment.status
            card_token = sub.card_token

        assert card_token is not None
        try:
            charge = await self.acquirer.charge_card(
                order_id=str(payment.id),
                amount_minor=payment.amount_minor,
                currency=payment.currency,
                card_token=card_token,
            )
        except UpstreamDown:
            # Не знаем, списались ли деньги. Не повторяем вслепую: ответ найдёт сверка.
            async with transaction(self.sessions) as s:
                await self._move(s, payment.id, "awaiting", ("new",))
                await s.execute(
                    update(RenewalAttempt)
                    .where(RenewalAttempt.id == attempt_id)
                    .values(state="unknown", note="acquirer timeout")
                )
            RENEWALS.labels("unknown").inc()
            return "unknown"

        async with transaction(self.sessions) as s:
            await self._move(s, payment.id, "awaiting", ("new",), provider_id=charge.id)
            result = await self._settle(s, charge, origin="renewal")
        log.info("renewal_charged", attempt=attempt_id, result=result, at=now.isoformat())
        return result

    async def _skip_reason(
        self, s: AsyncSession, sub: Subscription, attempt: RenewalAttempt
    ) -> str | None:
        if sub.paid_until != attempt.cycle_end:
            return "cycle_changed"  # продлил сам или получил бонус: этот цикл уже не тот
        if not sub.autorenew:
            return "autorenew_off"
        if sub.card_token is None or sub.plan_code is None:
            return "no_card"
        user = await s.get(User, attempt.user_id)
        if user is None or user.status != "active":
            return "user_inactive"
        return None

    async def _renewal_failed(
        self, s: AsyncSession, attempt_id: int, user_id: int, *, note: str
    ) -> None:
        attempt = await s.get(RenewalAttempt, attempt_id)
        assert attempt is not None
        attempt.state = "declined"
        attempt.note = note
        RENEWALS.labels("declined").inc()
        delay = retry_after(attempt.attempt, self.settings.renew_retry_hours)
        if delay is not None:
            await s.execute(
                insert(RenewalAttempt)
                .values(
                    user_id=user_id,
                    cycle_end=attempt.cycle_end,
                    attempt=attempt.attempt + 1,
                    run_after=self.clock.now() + delay,
                )
                .on_conflict_do_nothing(index_elements=["user_id", "cycle_end", "attempt"])
            )
            emit(s, "notify", user_id=user_id, template="renewal_failed", params={})
            return
        sub = await lock_subscription(s, user_id)
        sub.autorenew = False
        emit(s, "notify", user_id=user_id, template="autorenew_stopped", params={})

    # ------------------------------------------------------------------ сверка

    async def reconcile(self, *, limit: int = 100) -> dict[str, int]:
        """Каждые 5 минут: спросить эквайринг про платежи, по которым не пришёл ответ."""
        now = self.clock.now()
        stats = {"checked": 0, "settled": 0, "expired": 0}
        async with self.sessions() as s:
            stale = (
                await s.scalars(
                    select(Payment)
                    .where(
                        Payment.status.in_(OPEN),
                        Payment.updated_at < now - STALE_AWAITING,
                    )
                    .order_by(Payment.updated_at)
                    .limit(limit)
                )
            ).all()
        for payment in stale:
            stats["checked"] += 1
            try:
                charge = (
                    await self.acquirer.get_charge(payment.provider_id)
                    if payment.provider_id
                    else await self.acquirer.find_charge(str(payment.id))
                )
            except UpstreamDown:
                log.warning("reconcile_acquirer_down")
                break
            async with transaction(self.sessions) as s:
                if charge is not None and charge.status in ("succeeded", "failed"):
                    result = await self._settle(s, charge, origin="reconcile")
                    stats["settled"] += result in ("paid", "declined")
                elif (
                    payment.created_at < now - ABANDONED
                    or (charge is None and payment.created_at < now - STALE_AWAITING * 6)
                ) and await self._move(s, payment.id, "expired", OPEN):
                    stats["expired"] += 1
                    if payment.renewal_id is not None:
                        await self._renewal_failed(
                            s, payment.renewal_id, payment.user_id, note="no answer"
                        )
        return stats

    # ------------------------------------------------------------------ ручное начисление

    async def grant(self, *, user_id: int, days: int, request_id: str) -> AccessPeriod | None:
        async with transaction(self.sessions) as s:
            if await s.get(User, user_id) is None:
                raise NotFound("user not found")
            return await add_period(
                s,
                user_id=user_id,
                source_type="grant",
                source_id=request_id,
                days=days,
                now=self.clock.now(),
            )

    async def held(self) -> list[Payment]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(Payment).where(Payment.status == "on_hold").order_by(Payment.updated_at)
            )
            return list(rows)


def _as_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise Problem("bad order id") from exc
