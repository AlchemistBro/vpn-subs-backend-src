"""Периодические задачи на APScheduler. Запуск: python -m vas.cron.main

Можно поднять несколько экземпляров: работает только тот, кто держит advisory-лок
в Postgres. Лок сессионный, живёт пока живо соединение: упал процесс — лок отпустился,
и через несколько секунд его берёт другой экземпляр.
"""

import asyncio
import contextlib
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import httpx
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from celery import Celery
from prometheus_client import start_http_server
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from vas.core.log import setup_logging
from vas.core.metrics import ACCESS_LAG, CRON_LAST_OK, OUTBOX_BACKLOG
from vas.events import analytics, outbox
from vas.runtime import Runtime, build_runtime
from vas.settings import Settings, get_settings

log = structlog.get_logger()
LEADER_LOCK = 0x7661_7363  # произвольная константа, одинаковая у всех экземпляров


def celery_publisher(broker_url: str) -> outbox.Publish:
    producer = Celery("vas-cron", broker=broker_url)

    def publish(route: outbox.Route, payload: dict[str, Any]) -> None:
        producer.send_task(route.task, kwargs=payload, queue=route.queue, retry=False)

    return publish


def guarded(name: str, job: Callable[[], Awaitable[object]]) -> Callable[[], Awaitable[None]]:
    async def wrapper() -> None:
        started = time.perf_counter()
        try:
            result = await job()
        except Exception:
            log.exception("cron_job_failed", job=name)
            return
        CRON_LAST_OK.labels(name).set_to_current_time()
        if result:
            log.info(
                "cron_job_done",
                job=name,
                result=result,
                ms=int((time.perf_counter() - started) * 1000),
            )

    return wrapper


def schedule(rt: Runtime, publish: outbox.Publish, http: httpx.AsyncClient) -> AsyncIOScheduler:
    s = rt.settings

    async def relay() -> int:
        return await outbox.relay(rt.sessions, publish, now=rt.clock.now())

    async def gauges() -> None:
        OUTBOX_BACKLOG.set(await outbox.backlog(rt.sessions))
        ACCESS_LAG.set(await rt.access.lag())

    async def housekeeping() -> None:
        now = rt.clock.now()
        await outbox.purge_sent(rt.sessions, before=now - timedelta(days=7))
        await rt.accounts.purge_codes(before=now - timedelta(days=1))

    async def flush_analytics() -> int:
        return await analytics.flush(rt.redis, http, s.clickhouse_url)

    jobs: list[tuple[str, Callable[[], Awaitable[object]], dict[str, Any]]] = [
        ("outbox_relay", relay, {"seconds": 2}),
        ("gauges", gauges, {"seconds": 30}),
        ("renewals", rt.billing.plan_renewals, {"minutes": 60}),
        ("reconcile_payments", rt.billing.reconcile, {"minutes": 5}),
        ("reconcile_panel", rt.access.requeue_lagging, {"minutes": 5}),
        ("analytics_flush", flush_analytics, {"seconds": 15}),
        ("housekeeping", housekeeping, {"hours": 6}),
    ]
    scheduler = AsyncIOScheduler(timezone="UTC")
    for name, job, every in jobs:
        scheduler.add_job(
            guarded(name, job),
            "interval",
            id=name,
            name=name,
            max_instances=1,  # долгий запуск не накладывается на следующий
            coalesce=True,  # пропущенные запуски схлопываются в один
            misfire_grace_time=30,
            **every,
        )
    return scheduler


async def become_leader(rt: Runtime, stop: asyncio.Event) -> AsyncConnection | None:
    while not stop.is_set():
        conn = await rt.engine.connect()
        got = await conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LEADER_LOCK})
        # Лок сессионный и переживает commit. А без commit соединение висело бы
        # «idle in transaction» и мешало бы autovacuum.
        await conn.commit()
        if got:
            log.info("cron_leader_acquired")
            return conn
        await conn.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=10)
    return None


async def main(settings: Settings) -> None:
    setup_logging(json=settings.log_json)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)  # свой лог пишет guarded()
    start_http_server(9101)
    rt = build_runtime(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async with httpx.AsyncClient(timeout=10) as http:
        leader = await become_leader(rt, stop)
        if leader is not None:
            scheduler = schedule(rt, celery_publisher(settings.broker_url), http)
            scheduler.start()
            try:
                while not stop.is_set():
                    # Проверяем, что соединение с локом живо. Порвалось — выходим, нас перезапустят.
                    await leader.execute(text("SELECT 1"))
                    await leader.commit()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), timeout=15)
            finally:
                scheduler.shutdown(wait=False)
                await leader.close()
    await rt.close()


if __name__ == "__main__":
    asyncio.run(main(get_settings()))
