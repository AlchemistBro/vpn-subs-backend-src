import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from vas.core.db import Model, Timestamped


class Plan(Model):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(64))
    days: Mapped[int]
    price_minor: Mapped[int]  # копейки: деньги не храним во float
    currency: Mapped[str] = mapped_column(String(3), server_default="RUB")
    on_sale: Mapped[bool] = mapped_column(server_default="true")


class Subscription(Model):
    """Текущее состояние. История — в access_periods, это поле из неё выводится."""

    __tablename__ = "subscriptions"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    paid_until: Mapped[datetime | None]
    plan_code: Mapped[str | None] = mapped_column(ForeignKey("plans.code"))
    autorenew: Mapped[bool] = mapped_column(server_default="false")
    card_token: Mapped[str | None] = mapped_column(String(128))
    card_title: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class AccessPeriod(Timestamped, Model):
    """Журнал оплаченных периодов. Одна причина (оплата, триал, бонус) даёт ровно одну запись."""

    __tablename__ = "access_periods"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id"),
        CheckConstraint("days > 0 AND ends_at > starts_at", name="positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(16))  # trial | payment | referral | grant
    source_id: Mapped[str] = mapped_column(String(64))
    days: Mapped[int]
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]


class Payment(Timestamped, Model):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("user_id", "client_key"),
        CheckConstraint(
            "status IN ('new', 'awaiting', 'paid', 'declined', 'expired', 'on_hold')",
            name="known_status",
        ),
        Index(
            "ix_payments_open",
            "updated_at",
            postgresql_where=text("status IN ('new', 'awaiting')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan_code: Mapped[str] = mapped_column(ForeignKey("plans.code"))
    purpose: Mapped[str] = mapped_column(String(16))  # checkout | renewal
    client_key: Mapped[str] = mapped_column(String(80))  # ключ идемпотентности от клиента
    amount_minor: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3))
    # new → awaiting → paid | declined | expired;  при расхождении суммы → on_hold
    status: Mapped[str] = mapped_column(String(16), server_default="new")
    hold_reason: Mapped[str | None] = mapped_column(String(64))
    save_card: Mapped[bool] = mapped_column(server_default="false")
    provider_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    pay_url: Mapped[str | None] = mapped_column(String(512))
    renewal_id: Mapped[int | None] = mapped_column(ForeignKey("renewal_attempts.id"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    paid_at: Mapped[datetime | None]


class ProviderEvent(Model):
    """Входящие события эквайринга. Уникальность event_id отсекает повторную доставку."""

    __tablename__ = "provider_events"
    __table_args__ = (UniqueConstraint("provider", "event_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))
    event_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(64))
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class RenewalAttempt(Timestamped, Model):
    """Попытка продлить конкретный цикл. Цикл = значение paid_until, которое продлеваем."""

    __tablename__ = "renewal_attempts"
    __table_args__ = (
        UniqueConstraint("user_id", "cycle_end", "attempt"),
        Index("ix_renewal_attempts_due", "run_after", postgresql_where=text("state = 'planned'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    cycle_end: Mapped[datetime]
    attempt: Mapped[int]
    # planned → sent → charged | declined | skipped | unknown
    state: Mapped[str] = mapped_column(String(16), server_default="planned")
    run_after: Mapped[datetime]
    note: Mapped[str | None] = mapped_column(String(128))
