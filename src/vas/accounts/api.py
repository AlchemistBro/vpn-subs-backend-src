import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, EmailStr, Field

from vas.accounts.service import DeviceInfo, Tokens, invite_code
from vas.billing.periods import is_active
from vas.core.cache import allow
from vas.core.problems import TooManyRequests
from vas.deps import Me, Rt, client_ip

router = APIRouter(tags=["accounts"])


class DeviceIn(BaseModel):
    install_id: str = Field(min_length=8, max_length=64)
    platform: str = Field(pattern="^(ios|android|windows|macos|linux)$")
    title: str = Field(min_length=1, max_length=64)
    push_token: str | None = Field(default=None, max_length=512)
    replace: uuid.UUID | None = None

    def info(self) -> DeviceInfo:
        return DeviceInfo(self.install_id, self.platform, self.title, self.push_token, self.replace)


class BotCodeIn(BaseModel):
    code: str = Field(min_length=8, max_length=12)
    device: DeviceIn


class EmailStartIn(BaseModel):
    email: EmailStr


class EmailCodeIn(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")
    device: DeviceIn


class RefreshIn(BaseModel):
    refresh_token: str


class TokensOut(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    device_id: uuid.UUID

    @classmethod
    def of(cls, t: Tokens) -> "TokensOut":
        return cls(
            access_token=t.access,
            refresh_token=t.refresh,
            expires_in=t.expires_in,
            device_id=t.device_id,
        )


class MeOut(BaseModel):
    id: int
    email: str | None
    telegram_linked: bool
    invite_code: str
    paid_until: datetime | None
    active: bool
    autorenew: bool


class DeviceOut(BaseModel):
    id: uuid.UUID
    title: str
    platform: str
    last_seen_at: datetime | None
    current: bool


async def limit_by_ip(request: Request, rt: Rt) -> None:
    if not await allow(rt.redis, f"auth:{client_ip(request)}", limit=20, per_seconds=60):
        raise TooManyRequests("too many attempts from this address")


Limited = Depends(limit_by_ip)


@router.post("/auth/bot-code", dependencies=[Limited])
async def sign_in_with_bot_code(body: BotCodeIn, rt: Rt) -> TokensOut:
    return TokensOut.of(await rt.accounts.login_with_bot_code(body.code, body.device.info()))


@router.post("/auth/email/code", status_code=status.HTTP_202_ACCEPTED, dependencies=[Limited])
async def send_email_code(body: EmailStartIn, rt: Rt) -> dict[str, str]:
    await rt.accounts.send_email_code(body.email)
    return {"status": "sent"}


@router.post("/auth/email", dependencies=[Limited])
async def sign_in_with_email(body: EmailCodeIn, rt: Rt) -> TokensOut:
    tokens = await rt.accounts.login_with_email(body.email, body.code, body.device.info())
    return TokensOut.of(tokens)


@router.post("/auth/refresh", dependencies=[Limited])
async def refresh(body: RefreshIn, rt: Rt) -> TokensOut:
    return TokensOut.of(await rt.accounts.refresh(body.refresh_token))


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(me: Me, rt: Rt) -> None:
    await rt.accounts.logout(me.device_id)


@router.get("/me")
async def me(me: Me, rt: Rt) -> MeOut:
    user = await rt.accounts.user(me.user_id)
    sub, _ = await rt.billing.overview(me.user_id)
    return MeOut(
        id=user.id,
        email=user.email,
        telegram_linked=user.telegram_id is not None,
        invite_code=invite_code(user.id),
        paid_until=sub.paid_until,
        active=is_active(sub.paid_until, rt.clock.now()),
        autorenew=sub.autorenew,
    )


@router.get("/devices")
async def list_devices(me: Me, rt: Rt) -> list[DeviceOut]:
    return [
        DeviceOut(
            id=d.id,
            title=d.title,
            platform=d.platform,
            last_seen_at=d.last_seen_at,
            current=d.id == me.device_id,
        )
        for d in await rt.accounts.devices(me.user_id)
    ]


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_device(device_id: uuid.UUID, me: Me, rt: Rt) -> None:
    await rt.accounts.remove_device(me.user_id, device_id)
