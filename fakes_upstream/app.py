"""Заглушки внешних систем для локального запуска и e2e: эквайринг и VPN-панель.

Ведут себя как настоящие в неприятных местах:
* эквайринг доставляет каждое событие дважды и иногда с задержкой;
* FAKE_DECLINE_CARDS=1 — все списания по сохранённой карте отклоняются;
* FAKE_PANEL_ERRORS=0.3 — 30% запросов к панели отвечают 503, FAKE_PANEL_DELAY_MS — задержка.

Ещё здесь почтовый ящик: письма с кодами входа можно прочитать через /mail/last.
"""

import asyncio
import json
import os
import random
import time
import uuid
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from vas.core.secrets import sign_body

app = FastAPI(title="fake upstreams")

HOOK_URL = os.getenv("FAKE_HOOK_URL", "http://localhost:8000/hooks/acquirer")
HOOK_SECRET = os.getenv("VAS_ACQUIRER_HOOK_SECRET", "acquirer-local-hook")
PANEL_KEY = os.getenv("VAS_PANEL_KEY", "panel-local-key")
PUBLIC_URL = os.getenv("FAKE_PUBLIC_URL", "http://localhost:8090")

charges: dict[str, dict[str, Any]] = {}
by_key: dict[str, str] = {}
cards: set[str] = set()
panel_users: dict[str, dict[str, Any]] = {}
mailbox: list[dict[str, Any]] = []


# ---------------------------------------------------------------- эквайринг


async def deliver(charge: dict[str, Any]) -> None:
    event = {
        "id": f"ev_{charge['id']}_{charge['status']}",
        "type": f"charge.{charge['status']}",
        "data": charge,
    }
    body = json.dumps(event).encode()
    async with httpx.AsyncClient(timeout=5) as client:
        for _copy in range(2):
            await asyncio.sleep(random.uniform(0, 0.5))
            for attempt in range(6):
                ts = str(int(time.time()))
                headers = {
                    "content-type": "application/json",
                    "x-acq-timestamp": ts,
                    "x-acq-signature": sign_body(HOOK_SECRET, ts, body),
                }
                try:
                    resp = await client.post(HOOK_URL, content=body, headers=headers)
                    if resp.status_code < 500:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(2**attempt)


@app.post("/acquirer/charges")
async def create_charge(
    request: Request,
    background: BackgroundTasks,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> dict[str, Any]:
    if idempotency_key in by_key:
        return charges[by_key[idempotency_key]]
    body = await request.json()
    cid = f"ch_{uuid.uuid4().hex[:14]}"
    charge: dict[str, Any] = {
        "id": cid,
        "order_id": body["order_id"],
        "amount_minor": body["amount_minor"],
        "currency": body["currency"],
        "remember_card": body.get("remember_card", False),
        "card": None,
    }
    if token := body.get("card_token"):
        ok = token in cards and os.getenv("FAKE_DECLINE_CARDS") != "1"
        charge["status"] = "succeeded" if ok else "failed"
        charge["card"] = {"token": token, "title": "Visa •• 4242", "reusable": True}
        background.add_task(deliver, charge)
    else:
        charge["status"] = "pending"
        charge["checkout_url"] = f"{PUBLIC_URL}/acquirer/pay/{cid}"
    charges[cid] = charge
    by_key[idempotency_key] = cid
    return charge


@app.get("/acquirer/charges/{cid}")
async def get_charge(cid: str) -> JSONResponse:
    if cid not in charges:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(charges[cid])


@app.get("/acquirer/charges")
async def find_charges(order_id: str) -> dict[str, Any]:
    return {"items": [c for c in charges.values() if c["order_id"] == order_id]}


@app.get("/acquirer/pay/{cid}", response_class=HTMLResponse)
async def pay_page(cid: str) -> str:
    charge = charges.get(cid) or {}
    rub = charge.get("amount_minor", 0) / 100
    return (
        f"<h3>Тестовая оплата: {rub:.2f} ₽</h3>"
        f"<form method=post action='/acquirer/pay/{cid}/succeeded'><button>Оплатить</button></form>"
        f"<form method=post action='/acquirer/pay/{cid}/failed'><button>Отказ банка</button></form>"
    )


@app.post("/acquirer/pay/{cid}/{outcome}")
async def pay(cid: str, outcome: str, background: BackgroundTasks) -> dict[str, Any]:
    charge = charges.get(cid)
    if charge is None or charge["status"] != "pending" or outcome not in ("succeeded", "failed"):
        raise HTTPException(404)
    charge["status"] = outcome
    if outcome == "succeeded" and charge["remember_card"]:
        token = f"card_{uuid.uuid4().hex[:12]}"
        cards.add(token)
        charge["card"] = {"token": token, "title": "Visa •• 4242", "reusable": True}
    background.add_task(deliver, charge)
    return charge


# ---------------------------------------------------------------- VPN-панель


async def panel_weather() -> None:
    if delay := int(os.getenv("FAKE_PANEL_DELAY_MS", "0")):
        await asyncio.sleep(delay / 1000)
    if random.random() < float(os.getenv("FAKE_PANEL_ERRORS", "0")):
        raise HTTPException(503, "panel is busy")


def panel_auth(authorization: str | None) -> None:
    if authorization != f"Bearer {PANEL_KEY}":
        raise HTTPException(401)


@app.get("/panel/api/users/{user_uuid}")
async def panel_get(user_uuid: str, authorization: str | None = Header(None)) -> JSONResponse:
    panel_auth(authorization)
    await panel_weather()
    if user_uuid not in panel_users:
        return JSONResponse({"message": "User not found"}, status_code=404)
    return JSONResponse({"response": panel_users[user_uuid]})


@app.post("/panel/api/users")
async def panel_create(request: Request, authorization: str | None = Header(None)) -> JSONResponse:
    panel_auth(authorization)
    await panel_weather()
    body = await request.json()
    if body["uuid"] in panel_users:
        return JSONResponse({"message": "User already exists"}, status_code=409)
    short = uuid.uuid4().hex[:10]
    panel_users[body["uuid"]] = {
        "uuid": body["uuid"],
        "shortUuid": short,
        "username": body["username"],
        "expireAt": body["expireAt"],
        "status": body.get("status", "ACTIVE"),
        "subscriptionUrl": f"{PUBLIC_URL}/sub/{short}",
    }
    return JSONResponse({"response": panel_users[body["uuid"]]}, status_code=201)


@app.patch("/panel/api/users")
async def panel_update(request: Request, authorization: str | None = Header(None)) -> JSONResponse:
    panel_auth(authorization)
    await panel_weather()
    body = await request.json()
    user = panel_users.get(body["uuid"])
    if user is None:
        return JSONResponse({"message": "User not found"}, status_code=404)
    user.update(expireAt=body["expireAt"], status=body["status"])
    return JSONResponse({"response": user})


@app.get("/panel/debug/users")
async def panel_dump() -> dict[str, Any]:
    return panel_users


# ---------------------------------------------------------------- почта


@app.post("/mail/send")
async def mail_send(request: Request) -> dict[str, str]:
    mailbox.append(await request.json())
    return {"status": "queued"}


@app.get("/mail/last")
async def mail_last(to: str) -> dict[str, Any]:
    for letter in reversed(mailbox):
        if letter["to"] == to:
            return letter
    raise HTTPException(404)
