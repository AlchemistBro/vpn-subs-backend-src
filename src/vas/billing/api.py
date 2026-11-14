import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from vas.billing import money
from vas.deps import Me, Rt, internal_only

router = APIRouter(tags=["billing"])
hooks = APIRouter(tags=["webhooks"])
internal = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(internal_only)])


class CheckoutIn(BaseModel):
    plan: str = Field(max_length=32)
    save_card: bool = False


class CheckoutOut(BaseModel):
    payment_id: uuid.UUID
    status: str
    pay_url: str | None


class PaymentOut(BaseModel):
    id: uuid.UUID
    status: str
    plan: str
    amount: str
    currency: str
    created_at: datetime
    paid_at: datetime | None


class PeriodOut(BaseModel):
    source: str
    days: int
    starts_at: datetime
    ends_at: datetime


class SubscriptionOut(BaseModel):
    paid_until: datetime | None
    plan: str | None
    autorenew: bool
    card: str | None
    periods: list[PeriodOut]


class AutorenewIn(BaseModel):
    enabled: bool


class GrantIn(BaseModel):
    user_id: int
    days: int = Field(gt=0, le=365)
    request_id: str = Field(min_length=8, max_length=64)


@router.get("/plans")
async def plans(rt: Rt) -> list[dict[str, object]]:
    return await rt.billing.plans()


@router.post("/checkout")
async def checkout(
    body: CheckoutIn,
    me: Me,
    rt: Rt,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=64)],
) -> CheckoutOut:
    result = await rt.billing.checkout(
        user_id=me.user_id,
        plan_code=body.plan,
        client_key=idempotency_key,
        save_card=body.save_card,
    )
    return CheckoutOut(payment_id=result.payment_id, status=result.status, pay_url=result.pay_url)


@router.get("/payments/{payment_id}")
async def payment(payment_id: uuid.UUID, me: Me, rt: Rt) -> PaymentOut:
    p = await rt.billing.payment(user_id=me.user_id, payment_id=payment_id)
    return PaymentOut(
        id=p.id,
        status=p.status,
        plan=p.plan_code,
        amount=money.to_str(p.amount_minor),
        currency=p.currency,
        created_at=p.created_at,
        paid_at=p.paid_at,
    )


@router.get("/subscription")
async def subscription(me: Me, rt: Rt) -> SubscriptionOut:
    sub, periods = await rt.billing.overview(me.user_id)
    return SubscriptionOut(
        paid_until=sub.paid_until,
        plan=sub.plan_code,
        autorenew=sub.autorenew,
        card=sub.card_title,
        periods=[
            PeriodOut(source=p.source_type, days=p.days, starts_at=p.starts_at, ends_at=p.ends_at)
            for p in periods
        ],
    )


@router.put("/subscription/autorenew")
async def set_autorenew(body: AutorenewIn, me: Me, rt: Rt) -> dict[str, bool]:
    sub = await rt.billing.set_autorenew(user_id=me.user_id, enabled=body.enabled)
    return {"autorenew": sub.autorenew}


@hooks.post("/hooks/acquirer")
async def acquirer_hook(request: Request, rt: Rt) -> dict[str, str]:
    # Подпись считается от сырых байтов, поэтому тело читаем сами, а не через модель.
    body = await request.body()
    result = await rt.billing.accept_event(body, dict(request.headers))
    return {"result": result}


@internal.post("/grants")
async def grant(body: GrantIn, rt: Rt) -> dict[str, object]:
    period = await rt.billing.grant(
        user_id=body.user_id, days=body.days, request_id=body.request_id
    )
    return {"granted": period is not None, "ends_at": period.ends_at if period else None}


@internal.get("/payments/on-hold")
async def on_hold(rt: Rt) -> list[dict[str, object]]:
    return [
        {"id": str(p.id), "user_id": p.user_id, "reason": p.hold_reason, "amount": p.amount_minor}
        for p in await rt.billing.held()
    ]
