"""Вызовы внешних систем: таймауты, повторы с джиттером, метрики.

Повторяем только то, что безопасно повторить: сетевые ошибки, 5xx и 429.
Для создания платежа повтор безопасен, потому что у запроса есть ключ идемпотентности.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

import httpx
import structlog

from vas.core.metrics import UPSTREAM_LATENCY, UPSTREAM_REQUESTS
from vas.core.problems import UpstreamDown

log = structlog.get_logger()

TIMEOUTS = httpx.Timeout(connect=2.0, read=8.0, write=5.0, pool=2.0)
RETRY_STATUSES = {429, 500, 502, 503, 504}
sleep = asyncio.sleep  # отдельное имя, чтобы в тестах подменить паузы, не трогая asyncio


def pause_before(attempt: int, *, base: float = 0.4, cap: float = 6.0) -> float:
    """Full jitter: случайная пауза от 0 до base·2^attempt, не больше cap."""
    return random.uniform(0, min(cap, base * 2**attempt))  # noqa: S311


def result_label(response: httpx.Response | None, error: Exception | None) -> str:
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    if error is not None:
        return "network"
    assert response is not None
    if response.status_code >= 500:
        return "5xx"
    if response.status_code >= 400:
        return "4xx"
    return "ok"


async def send(
    upstream: str,
    op: str,
    call: Callable[[], Awaitable[httpx.Response]],
    *,
    attempts: int = 3,
    retry_timeouts: bool = True,
) -> httpx.Response:
    """Выполняет запрос и возвращает ответ, в том числе 4xx: их разбирает вызывающий."""
    for attempt in range(attempts):
        started = time.perf_counter()
        response: httpx.Response | None = None
        error: Exception | None = None
        try:
            response = await call()
        except httpx.HTTPError as exc:
            error = exc
        label = result_label(response, error)
        UPSTREAM_REQUESTS.labels(upstream, op, label).inc()
        UPSTREAM_LATENCY.labels(upstream).observe(time.perf_counter() - started)

        retryable = (
            (label == "network")
            or (label == "timeout" and retry_timeouts)
            or (response is not None and response.status_code in RETRY_STATUSES)
        )
        if not retryable:
            if response is None:
                raise UpstreamDown(f"{upstream} {op}: {label}") from error
            return response
        if attempt + 1 < attempts:
            delay = pause_before(attempt)
            log.warning("upstream_retry", upstream=upstream, op=op, result=label, delay=delay)
            await sleep(delay)
    raise UpstreamDown(f"{upstream} {op}: gave up after {attempts} attempts")
