"""Redis: ограничение частоты, короткий лок, кеш."""

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from redis.asyncio import Redis

# INCR и EXPIRE в одном скрипте: если процесс упадёт между ними, ключ не станет вечным.
_COUNT_IN_WINDOW = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return n
"""

# Снимаем лок, только если он всё ещё наш: иначе снесём лок того, кто взял его после TTL.
_UNLOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


async def allow(redis: Redis, key: str, *, limit: int, per_seconds: int) -> bool:
    count = await redis.eval(_COUNT_IN_WINDOW, 1, f"rl:{key}", per_seconds)
    return int(count) <= limit


class Busy(Exception):
    pass


@asynccontextmanager
async def hold(redis: Redis, key: str, *, ttl_ms: int = 30_000) -> AsyncIterator[None]:
    token = uuid.uuid4().hex
    if not await redis.set(f"lock:{key}", token, nx=True, px=ttl_ms):
        raise Busy(key)
    try:
        yield
    finally:
        await redis.eval(_UNLOCK, 1, f"lock:{key}", token)


async def remember(redis: Redis, key: str, ttl: int, load: Callable[[], Awaitable[Any]]) -> Any:
    cached = await redis.get(key)
    if cached is not None:
        return json.loads(cached)
    value = await load()
    await redis.set(key, json.dumps(value, default=str), ex=ttl)
    return value
