import pytest

from vas.billing.money import mismatch, to_minor, to_str


def test_round_trip() -> None:
    assert to_minor("199.00") == 19900
    assert to_str(19900) == "199.00"
    assert to_str(5) == "0.05"


def test_float_garbage_is_not_accepted() -> None:
    # 0.1 + 0.2 в float = 0.30000000000000004: такие суммы должны падать, а не округляться
    with pytest.raises(ValueError, match="decimal places"):
        to_minor(str(0.1 + 0.2))


def test_mismatch() -> None:
    assert mismatch(19900, "RUB", 19900, "rub") is None
    assert mismatch(19900, "RUB", 100, "RUB") == "amount_mismatch"
    assert mismatch(19900, "RUB", 19900, "USD") == "currency_mismatch"
