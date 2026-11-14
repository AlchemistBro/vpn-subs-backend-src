"""Зависимости FastAPI: runtime процесса и текущий пользователь."""

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request

from vas.core import secrets
from vas.core.problems import Unauthorized
from vas.runtime import Runtime


def get_rt(request: Request) -> Runtime:
    rt: Runtime = request.app.state.rt
    return rt


Rt = Annotated[Runtime, Depends(get_rt)]


@dataclass(frozen=True)
class Caller:
    user_id: int
    device_id: uuid.UUID


async def get_caller(rt: Rt, authorization: Annotated[str | None, Header()] = None) -> Caller:
    if not authorization or not authorization.startswith("Bearer "):
        raise Unauthorized("missing bearer token")
    user_id, device_id = secrets.read_access(
        rt.settings.jwt_key.get_secret_value(), authorization[7:], now=rt.clock.now()
    )
    await rt.accounts.check_session(user_id, device_id)
    return Caller(user_id, device_id)


Me = Annotated[Caller, Depends(get_caller)]


async def internal_only(rt: Rt, x_internal_key: Annotated[str | None, Header()] = None) -> None:
    expected = rt.settings.internal_key.get_secret_value()
    if x_internal_key is None or not secrets.same(x_internal_key, expected):
        raise Unauthorized("internal key required")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
