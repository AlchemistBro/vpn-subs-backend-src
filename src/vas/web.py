"""HTTP-приложение: сборка роутеров, middleware, единый формат ошибок, health и метрики."""

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vas.access.api import router as access_router
from vas.accounts.api import router as accounts_router
from vas.billing.api import hooks, internal
from vas.billing.api import router as billing_router
from vas.core.log import setup_logging
from vas.core.metrics import API_LATENCY, API_REQUESTS, APP_BUILD
from vas.core.problems import Problem
from vas.runtime import Runtime, build_runtime
from vas.settings import Settings, get_settings

log = structlog.get_logger()


def route_template(scope: Scope) -> str:
    """Шаблон роута (/payments/{payment_id}), а не путь: иначе у метрики миллион меток.
    Роутер кладёт найденный маршрут в scope, middleware читает его после ответа."""
    return str(getattr(scope.get("route"), "path", None) or "unmatched")


class RequestTelemetry:
    """Чистый ASGI-middleware: request id, лог запроса, метрики. Не буферизует тело ответа."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        request_id = headers.get(b"x-request-id", b"").decode() or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal code
            if message["type"] == "http.response.start":
                code = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = route_template(scope)
            elapsed = time.perf_counter() - started
            if route not in ("/metrics", "/health/live", "/health/ready"):
                API_REQUESTS.labels(route, scope["method"], str(code)).inc()
                API_LATENCY.labels(route).observe(elapsed)
                log.info(
                    "http", method=scope["method"], route=route, code=code, ms=int(elapsed * 1000)
                )
            structlog.contextvars.clear_contextvars()


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


async def on_problem(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, Problem)
    return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status)


async def on_validation(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    fields = [
        {"field": ".".join(str(p) for p in e["loc"][1:]), "problem": e["type"]}
        for e in exc.errors()
    ]
    return JSONResponse(
        error_body("validation_failed", "request is not valid", {"fields": fields}), status_code=422
    )


async def on_crash(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error", error=repr(exc))
    return JSONResponse(error_body("internal", "something went wrong"), status_code=500)


def create_app(settings: Settings | None = None, *, rt: Runtime | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        own = rt is None
        app.state.rt = rt or build_runtime(settings)
        APP_BUILD.labels(settings.build_sha).set(1)
        yield
        if own:
            await app.state.rt.close()

    if rt is None:
        setup_logging(json=settings.log_json)
    public = settings.env == "prod"  # на проде схему и /docs не показываем
    app = FastAPI(
        title="vpn-access-service",
        version=settings.build_sha,
        lifespan=lifespan,
        docs_url=None if public else "/docs",
        redoc_url=None,
        openapi_url=None if public else "/openapi.json",
    )
    app.add_middleware(RequestTelemetry)
    app.add_exception_handler(Problem, on_problem)
    app.add_exception_handler(RequestValidationError, on_validation)
    app.add_exception_handler(Exception, on_crash)
    for router in (accounts_router, billing_router, access_router, hooks, internal):
        app.include_router(router)

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready(request: Request) -> JSONResponse:
        runtime: Runtime = request.app.state.rt
        checks: dict[str, str] = {}
        try:
            async with runtime.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["postgres"] = "ok"
        except Exception as exc:
            checks["postgres"] = type(exc).__name__
        try:
            await runtime.redis.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = type(exc).__name__
        healthy = all(v == "ok" for v in checks.values())
        return JSONResponse(checks, status_code=200 if healthy else 503)

    @app.get("/version", include_in_schema=False)
    async def version() -> dict[str, str]:
        return {"sha": settings.build_sha}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
