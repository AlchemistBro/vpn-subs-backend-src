from datetime import datetime

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from vas.deps import Me, Rt

router = APIRouter(tags=["access"])


class LinkOut(BaseModel):
    status: str  # ready | provisioning
    url: str | None
    active: bool
    paid_until: datetime | None


@router.get("/access/link")
async def access_link(me: Me, rt: Rt, response: Response) -> LinkOut:
    link = await rt.access.link(me.user_id)
    if not link.ready:
        # Аккаунт в панели ещё создаётся задачей. 202 — «приходите позже», клиент повторит.
        response.status_code = status.HTTP_202_ACCEPTED
        response.headers["Retry-After"] = "2"
    return LinkOut(
        status="ready" if link.ready else "provisioning",
        url=link.url,
        active=link.active,
        paid_until=link.paid_until,
    )
