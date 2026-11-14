"""Celery-приложение. Запуск: celery -A vas.jobs.app worker -Q access,billing,notify,analytics

Код задач асинхронный. В каждом процессе воркера свой event loop (asyncio.Runner) и свой
Runtime: пул соединений нельзя делить между процессами после fork.
"""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from vas.core.log import setup_logging
from vas.runtime import Runtime, build_runtime
from vas.settings import get_settings

settings = get_settings()
celery = Celery("vas", broker=settings.broker_url)
celery.conf.update(
    task_acks_late=True,  # подтверждаем после выполнения: упал воркер — задача вернётся
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="notify",
    task_ignore_result=True,
    task_time_limit=120,
    task_soft_time_limit=90,
    broker_transport_options={"visibility_timeout": 600},
    broker_connection_retry_on_startup=True,
    include=["vas.jobs.tasks"],
)

_runner: asyncio.Runner | None = None
_rt: Runtime | None = None


@worker_process_init.connect
def _start(**_: Any) -> None:
    global _runner, _rt
    setup_logging(json=settings.log_json)
    _runner = asyncio.Runner()
    _rt = build_runtime(settings)


@worker_process_shutdown.connect
def _stop(**_: Any) -> None:
    if _runner is not None and _rt is not None:
        _runner.run(_rt.close())
        _runner.close()


def run[T](make: Callable[[Runtime], Coroutine[Any, Any, T]]) -> T:
    global _runner, _rt
    if _runner is None:  # eager-режим и запуск без prefork
        _start()
    assert _runner is not None
    assert _rt is not None
    return _runner.run(make(_rt))
