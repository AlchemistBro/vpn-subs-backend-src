"""Деньги в минимальных единицах (копейках). Строки от провайдера переводим через Decimal."""

from decimal import Decimal, InvalidOperation


def to_minor(value: str) -> int:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"not a money amount: {value!r}") from exc
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError(f"more than 2 decimal places: {value!r}")
    return int(amount * 100)


def to_str(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    rub, kop = divmod(abs(minor), 100)
    return f"{sign}{rub}.{kop:02d}"


def mismatch(
    expected_minor: int, expected_currency: str, got_minor: int, got_currency: str
) -> str | None:
    if got_currency.upper() != expected_currency.upper():
        return "currency_mismatch"
    if got_minor != expected_minor:
        return "amount_mismatch"
    return None
