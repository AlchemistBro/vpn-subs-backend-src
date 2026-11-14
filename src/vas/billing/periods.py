"""Арифметика периодов доступа. Чистые функции, без БД и часов."""

from datetime import datetime, timedelta

# Сколько после окончания периода ещё пробуем продлить: cron мог пропустить запуск.
RENEW_GRACE = timedelta(hours=6)


def next_period(now: datetime, paid_until: datetime | None, days: int) -> tuple[datetime, datetime]:
    """Новый период начинается с конца текущего, если он ещё идёт, иначе с сейчас."""
    if days <= 0:
        raise ValueError("days must be positive")
    start = paid_until if paid_until is not None and paid_until > now else now
    return start, start + timedelta(days=days)


def is_active(paid_until: datetime | None, now: datetime) -> bool:
    return paid_until is not None and paid_until > now


def renewal_due(paid_until: datetime | None, now: datetime, window: timedelta) -> bool:
    if paid_until is None:
        return False
    return now - RENEW_GRACE < paid_until <= now + window


def retry_after(failed_attempt: int, schedule: tuple[int, ...]) -> timedelta | None:
    """Через сколько повторить списание после неудачной попытки номер N (с 1). None — хватит."""
    if failed_attempt <= 0:
        raise ValueError("attempt numbers start at 1")
    if failed_attempt > len(schedule):
        return None
    return timedelta(hours=schedule[failed_attempt - 1])
