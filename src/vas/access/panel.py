"""Клиент VPN-панели с API в духе Remnawave: пользователь, срок, ссылка на подписку."""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx

from vas.core import upstream


@dataclass(frozen=True)
class PanelUser:
    uuid: uuid.UUID
    expire_at: datetime
    enabled: bool
    sub_url: str


class Panel(Protocol):
    async def get(self, user_uuid: uuid.UUID) -> PanelUser | None: ...

    async def create(
        self, user_uuid: uuid.UUID, *, username: str, expire_at: datetime, enabled: bool
    ) -> PanelUser: ...

    async def update(
        self, user_uuid: uuid.UUID, *, expire_at: datetime, enabled: bool
    ) -> PanelUser: ...


def _parse(data: dict[str, Any]) -> PanelUser:
    return PanelUser(
        uuid=uuid.UUID(data["uuid"]),
        expire_at=datetime.fromisoformat(data["expireAt"].replace("Z", "+00:00")),
        enabled=data["status"] == "ACTIVE",
        sub_url=data["subscriptionUrl"],
    )


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class HttpPanel:
    name = "panel"

    def __init__(self, base_url: str, key: str, *, parallel: int = 8) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=upstream.TIMEOUTS,
        )
        # Bulkhead: панель медленная, не даём ей съесть все соединения воркера.
        self._gate = asyncio.Semaphore(parallel)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, user_uuid: uuid.UUID) -> PanelUser | None:
        async with self._gate:
            resp = await upstream.send(
                self.name, "get", lambda: self._http.get(f"/api/users/{user_uuid}")
            )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return _parse(resp.json()["response"])

    async def create(
        self, user_uuid: uuid.UUID, *, username: str, expire_at: datetime, enabled: bool
    ) -> PanelUser:
        body = {
            "uuid": str(user_uuid),
            "username": username,
            "expireAt": _iso(expire_at),
            "status": "ACTIVE" if enabled else "DISABLED",
        }
        async with self._gate:
            resp = await upstream.send(
                self.name, "create", lambda: self._http.post("/api/users", json=body)
            )
        if resp.status_code == 409:
            # Прошлая попытка создала пользователя, но ответ потерялся. uuid наш, просто читаем.
            return await self.update(user_uuid, expire_at=expire_at, enabled=enabled)
        resp.raise_for_status()
        return _parse(resp.json()["response"])

    async def update(
        self, user_uuid: uuid.UUID, *, expire_at: datetime, enabled: bool
    ) -> PanelUser:
        body = {
            "uuid": str(user_uuid),
            "expireAt": _iso(expire_at),
            "status": "ACTIVE" if enabled else "DISABLED",
        }
        async with self._gate:
            resp = await upstream.send(
                self.name, "update", lambda: self._http.patch("/api/users", json=body)
            )
        resp.raise_for_status()
        return _parse(resp.json()["response"])
