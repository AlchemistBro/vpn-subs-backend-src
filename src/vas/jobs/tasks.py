"""Задачи Celery. Все идемпотентны: outbox и acks_late гарантируют «хотя бы один раз»."""

from typing import Any

import structlog

from vas.core.cache import Busy
from vas.core.problems import UpstreamDown
from vas.events import analytics
from vas.jobs.app import celery, run
from vas.notify.service import deliver

log = structlog.get_logger()

RETRY = {
    "autoretry_for": (UpstreamDown, Busy),
    "retry_backoff": 5,
    "retry_backoff_max": 600,
    "retry_jitter": True,
    "max_retries": 12,
}


@celery.task(name="access.push", queue="access", **RETRY)
def push_access(user_id: int) -> str:
    return run(lambda rt: rt.access.push(user_id))


@celery.task(name="billing.charge_renewal", queue="billing")
def charge_renewal(attempt_id: int) -> str:
    # Без автоповтора: неясный исход списания разбирает сверка, а не слепой повтор.
    return run(lambda rt: rt.billing.charge_renewal(attempt_id))


@celery.task(name="notify.send", queue="notify", **RETRY)
def send_notification(user_id: int, template: str, params: dict[str, Any]) -> str:
    return run(
        lambda rt: deliver(
            rt.sessions, rt.notifier, user_id=user_id, template=template, params=params
        )
    )


@celery.task(name="analytics.track", queue="analytics", ignore_result=True)
def track(name: str, user_id: int | None = None, **props: Any) -> None:
    def event(rt: Any) -> Any:
        return analytics.track(
            rt.redis,
            {
                "name": name,
                "user_id": user_id or 0,
                "ts": rt.clock.now().isoformat(),
                "props": props,
            },
        )

    run(event)
