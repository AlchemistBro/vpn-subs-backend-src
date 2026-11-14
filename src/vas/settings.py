"""Конфигурация из переменных окружения с префиксом VAS_."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VAS_", env_file=".env", extra="ignore")

    env: str = "local"
    build_sha: str = "dev"
    log_json: bool = True

    db_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/vas"
    db_pool_size: int = 10
    db_pool_overflow: int = 5
    redis_url: str = "redis://localhost:6379/0"
    broker_url: str = "redis://localhost:6380/0"

    jwt_key: SecretStr = SecretStr("local-only-key-at-least-32-bytes-long")
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    code_pepper: SecretStr = SecretStr("local-only-pepper")

    acquirer_url: str = "http://localhost:8090/acquirer"
    acquirer_shop: str = "shop-local"
    acquirer_key: SecretStr = SecretStr("acquirer-local-key")
    acquirer_hook_secret: SecretStr = SecretStr("acquirer-local-hook")
    return_url: str = "http://localhost:8000/paid"

    panel_url: str = "http://localhost:8090/panel"
    panel_key: SecretStr = SecretStr("panel-local-key")
    panel_parallel: int = 8

    internal_key: SecretStr = SecretStr("internal-local-key")

    bot_token: SecretStr | None = None
    telegram_api: str = "https://api.telegram.org"
    email_api: str | None = None
    email_key: SecretStr | None = None
    fcm_project: str | None = None
    fcm_token: SecretStr | None = None
    clickhouse_url: str | None = None

    trial_days: int = 3
    referral_days: int = 7
    device_slots: int = 5
    renew_window_hours: int = 24
    renew_retry_hours: tuple[int, ...] = (24, 72)


@lru_cache
def get_settings() -> Settings:
    return Settings()
