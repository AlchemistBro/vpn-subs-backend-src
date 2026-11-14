# syntax=docker/dockerfile:1.7
# Один образ на все процессы: api, jobs, cron, bot, schema. Разница только в команде запуска.

FROM ghcr.io/astral-sh/uv:0.11.32-python3.13-trixie-slim AS deps
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/venv
WORKDIR /src
# Зависимости отдельным слоем: он пересобирается, только когда меняется uv.lock.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim-trixie
RUN groupadd --gid 10001 vas && useradd --uid 10001 --gid vas --no-create-home vas
COPY --from=deps /venv /venv
WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
COPY fakes_upstream ./fakes_upstream
ARG BUILD_SHA=dev
ENV PATH=/venv/bin:$PATH PYTHONUNBUFFERED=1 VAS_BUILD_SHA=$BUILD_SHA
USER vas
EXPOSE 8000
# exec-форма: SIGTERM получает сам uvicorn и спокойно доделывает начатые запросы.
CMD ["uvicorn", "vas.web:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-graceful-shutdown", "20"]
