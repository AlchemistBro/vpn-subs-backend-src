"""Метрики Prometheus. В метках только то, у чего мало значений."""

from prometheus_client import Counter, Gauge, Histogram

API_REQUESTS = Counter("api_requests_total", "Запросы к API", ["route", "method", "code"])
API_LATENCY = Histogram(
    "api_latency_seconds",
    "Время ответа API",
    ["route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
UPSTREAM_REQUESTS = Counter(
    "upstream_requests_total", "Запросы во внешние системы", ["upstream", "op", "result"]
)
UPSTREAM_LATENCY = Histogram("upstream_latency_seconds", "Время внешних запросов", ["upstream"])
PAYMENTS_APPLIED = Counter(
    "payments_applied_total", "Оплаты, превращённые в период доступа", ["origin"]
)
PAYMENTS_HELD = Counter("payments_held_total", "Оплаты, отложенные на ручной разбор", ["reason"])
RENEWALS = Counter("renewal_attempts_total", "Попытки автопродления", ["result"])
ACCESS_LAG = Gauge("access_lag_users", "Пользователи, у которых панель отстаёт от оплаты")
OUTBOX_BACKLOG = Gauge("outbox_backlog", "Неотправленные события outbox")
CRON_LAST_OK = Gauge("cron_last_ok_timestamp", "Последний успешный запуск задачи cron", ["job"])
APP_BUILD = Gauge("app_build", "Версия сборки", ["sha"])
