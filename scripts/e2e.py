"""Сквозной сценарий против поднятого стенда (make stack или процессы локально).

Вход по почте → ссылка на VPN появляется сама → оплата на странице эквайринга →
вебхук приходит дважды → у пользователя ровно два периода, панель знает новый срок.
"""

import asyncio
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx

API = os.getenv("E2E_API", "http://localhost:8000")
FAKES = os.getenv("E2E_FAKES", "http://localhost:8090")


async def until[T](what: str, probe: Callable[[], Awaitable[T | None]], seconds: float = 30) -> T:
    async with asyncio.timeout(seconds):
        while True:
            if (value := await probe()) is not None:
                print(f"  ok: {what}")
                return value
            await asyncio.sleep(0.5)


async def main() -> None:
    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
    device = {"install_id": uuid.uuid4().hex, "platform": "linux", "title": "e2e"}
    async with (
        httpx.AsyncClient(base_url=API, timeout=10) as api,
        httpx.AsyncClient(base_url=FAKES, timeout=10) as fakes,
    ):
        print("1. вход по почте")
        (await api.post("/auth/email/code", json={"email": email})).raise_for_status()
        letter = (await fakes.get("/mail/last", params={"to": email})).json()
        code = letter["text"].split(":")[1].strip()[:6]
        tokens = (
            await api.post("/auth/email", json={"email": email, "code": code, "device": device})
        ).json()
        auth = {"Authorization": f"Bearer {tokens['access_token']}"}

        print("2. триал и ссылка на VPN")

        async def link_ready() -> dict[str, object] | None:
            resp = await api.get("/access/link", headers=auth)
            return resp.json() if resp.status_code == 200 else None

        link = await until("панель создала пользователя", link_ready)
        assert link["active"], link

        print("3. оплата")
        headers = {**auth, "Idempotency-Key": uuid.uuid4().hex}
        checkout = (
            await api.post("/checkout", json={"plan": "m1", "save_card": True}, headers=headers)
        ).json()
        charge_id = checkout["pay_url"].rsplit("/", 1)[1]
        (await fakes.post(f"/acquirer/pay/{charge_id}/succeeded")).raise_for_status()

        async def paid() -> dict[str, object] | None:
            resp = await api.get(f"/payments/{checkout['payment_id']}", headers=auth)
            return resp.json() if resp.json()["status"] == "paid" else None

        await until("вебхук применён", paid)
        await asyncio.sleep(2)  # вторая копия вебхука
        sub = (await api.get("/subscription", headers=auth)).json()
        sources = sorted(p["source"] for p in sub["periods"])
        assert sources == ["payment", "trial"], sources
        assert sub["autorenew"] is True, sub
        print("  ok: ровно два периода, автопродление включено")

        print("4. панель догнала новый срок")
        paid_until = datetime.fromisoformat(sub["paid_until"])

        async def panel_synced() -> bool | None:
            users = (await fakes.get("/panel/debug/users")).json()
            for user in users.values():
                expire = datetime.fromisoformat(user["expireAt"].replace("Z", "+00:00"))
                if expire == paid_until:
                    return True
            return None

        await until("срок в панели совпадает с оплаченным", panel_synced)
    print("e2e: всё прошло")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (AssertionError, TimeoutError, httpx.HTTPError) as exc:
        print(f"e2e: упало — {exc!r}", file=sys.stderr)
        sys.exit(1)
