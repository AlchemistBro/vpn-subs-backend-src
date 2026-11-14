"""Продуктовые события в ClickHouse. Копим в Redis и пишем пачками: ClickHouse не любит
вставки по одной строке, а потеря пары событий при аварии для аналитики допустима."""

import json
from typing import Any

import httpx
from redis.asyncio import Redis

from vas.core import upstream

BUFFER = "analytics:buffer"
BUFFER_MAX = 100_000


async def track(redis: Redis, event: dict[str, Any]) -> None:
    async with redis.pipeline(transaction=True) as pipe:
        pipe.rpush(BUFFER, json.dumps(event, default=str))
        pipe.ltrim(BUFFER, -BUFFER_MAX, -1)  # ClickHouse лежит долго — не съедаем всю память
        await pipe.execute()


async def flush(
    redis: Redis, http: httpx.AsyncClient, url: str | None, *, batch: int = 1000
) -> int:
    raw: list[bytes] | None = await redis.lpop(BUFFER, batch)  # type: ignore[assignment]
    if not raw:
        return 0
    if url is None:
        return len(raw)  # аналитика выключена: просто не копим
    rows = b"\n".join(raw)
    query = "INSERT INTO product_events (name, user_id, ts, props) FORMAT JSONEachRow"
    try:
        resp = await upstream.send(
            "clickhouse",
            "insert",
            lambda: http.post(
                url, params={"query": query, "date_time_input_format": "best_effort"}, content=rows
            ),
        )
        resp.raise_for_status()
    except Exception:
        await redis.lpush(BUFFER, *reversed(raw))  # вернём в начало и попробуем в следующий раз
        raise
    return len(raw)
