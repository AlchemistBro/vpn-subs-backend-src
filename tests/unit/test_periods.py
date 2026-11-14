from datetime import UTC, datetime, timedelta

import pytest

from vas.billing.periods import next_period, renewal_due, retry_after

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def test_first_period_starts_now() -> None:
    assert next_period(NOW, None, 30) == (NOW, NOW + timedelta(days=30))


def test_period_continues_from_current_end() -> None:
    end = NOW + timedelta(days=5)
    assert next_period(NOW, end, 30) == (end, end + timedelta(days=30))


def test_expired_access_restarts_from_now() -> None:
    assert next_period(NOW, NOW - timedelta(days=40), 30)[0] == NOW


def test_days_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        next_period(NOW, None, 0)


@pytest.mark.parametrize(
    ("ends_in", "due"),
    [
        (timedelta(hours=30), False),
        (timedelta(hours=23), True),
        (timedelta(hours=-1), True),  # cron мог пропустить запуск — ещё пробуем
        (timedelta(hours=-7), False),  # слишком поздно, человек уже без доступа
    ],
)
def test_renewal_window(ends_in: timedelta, due: bool) -> None:
    assert renewal_due(NOW + ends_in, NOW, timedelta(hours=24)) is due


def test_retry_schedule() -> None:
    schedule = (24, 72)
    assert retry_after(1, schedule) == timedelta(hours=24)
    assert retry_after(2, schedule) == timedelta(hours=72)
    assert retry_after(3, schedule) is None
