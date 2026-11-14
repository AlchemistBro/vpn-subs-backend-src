"""Клиент эквайринга. В репозитории он ходит в мок (fakes_upstream), API у мока своё, простое."""

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from vas.core import upstream
from vas.core.problems import Problem
from vas.core.secrets import same, sign_body

SIGNATURE_TTL = 300


@dataclass(frozen=True)
class Charge:
    id: str
    order_id: str
    status: str  # pending | succeeded | failed
    amount_minor: int
    currency: str
    checkout_url: str | None = None
    card_token: str | None = None
    card_title: str | None = None


@dataclass(frozen=True)
class Event:
    id: str
    kind: str
    charge: Charge
    raw: dict[str, Any]


class BadSignature(Problem):
    status = 401
    code = "bad_signature"


class Acquirer(Protocol):
    async def create_charge(
        self, *, order_id: str, amount_minor: int, currency: str, title: str, remember_card: bool
    ) -> Charge: ...

    async def charge_card(
        self, *, order_id: str, amount_minor: int, currency: str, card_token: str
    ) -> Charge: ...

    async def get_charge(self, charge_id: str) -> Charge | None: ...

    async def find_charge(self, order_id: str) -> Charge | None: ...


def parse_charge(data: dict[str, Any]) -> Charge:
    card = data.get("card") or {}
    return Charge(
        id=data["id"],
        order_id=data["order_id"],
        status=data["status"],
        amount_minor=int(data["amount_minor"]),
        currency=data["currency"],
        checkout_url=data.get("checkout_url"),
        card_token=card.get("token") if card.get("reusable") else None,
        card_title=card.get("title"),
    )


def parse_event(secret: str, body: bytes, headers: dict[str, str], *, now_ts: float) -> Event:
    ts = headers.get("x-acq-timestamp", "")
    signature = headers.get("x-acq-signature", "")
    if not ts.isdigit() or abs(now_ts - int(ts)) > SIGNATURE_TTL:
        raise BadSignature("stale or missing timestamp")
    if not same(sign_body(secret, ts, body), signature):
        raise BadSignature("signature mismatch")
    data = json.loads(body)
    return Event(id=data["id"], kind=data["type"], charge=parse_charge(data["data"]), raw=data)


class HttpAcquirer:
    name = "acquirer"

    def __init__(self, base_url: str, shop: str, key: str, return_url: str) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url, auth=(shop, key), timeout=upstream.TIMEOUTS
        )
        self._return_url = return_url

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, op: str, body: dict[str, Any], *, retry_timeouts: bool) -> Charge:
        # Ключ идемпотентности = наш id платежа: повтор запроса не создаст второй платёж.
        headers = {"Idempotency-Key": body["order_id"]}
        resp = await upstream.send(
            self.name,
            op,
            lambda: self._http.post("/charges", json=body, headers=headers),
            retry_timeouts=retry_timeouts,
        )
        resp.raise_for_status()
        return parse_charge(resp.json())

    async def create_charge(
        self, *, order_id: str, amount_minor: int, currency: str, title: str, remember_card: bool
    ) -> Charge:
        body = {
            "order_id": order_id,
            "amount_minor": amount_minor,
            "currency": currency,
            "title": title,
            "remember_card": remember_card,
            "return_url": self._return_url,
        }
        return await self._post("create", body, retry_timeouts=True)

    async def charge_card(
        self, *, order_id: str, amount_minor: int, currency: str, card_token: str
    ) -> Charge:
        body = {
            "order_id": order_id,
            "amount_minor": amount_minor,
            "currency": currency,
            "card_token": card_token,
        }
        return await self._post("charge_card", body, retry_timeouts=True)

    async def get_charge(self, charge_id: str) -> Charge | None:
        resp = await upstream.send(
            self.name, "get", lambda: self._http.get(f"/charges/{charge_id}")
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return parse_charge(resp.json())

    async def find_charge(self, order_id: str) -> Charge | None:
        resp = await upstream.send(
            self.name, "find", lambda: self._http.get("/charges", params={"order_id": order_id})
        )
        resp.raise_for_status()
        items = resp.json()["items"]
        return parse_charge(items[0]) if items else None
