import json
import time

import pytest

from vas.billing.acquirer import BadSignature, parse_event
from vas.core.secrets import sign_body

SECRET = "hook-secret"
BODY = json.dumps(
    {
        "id": "ev_1",
        "type": "charge.succeeded",
        "data": {
            "id": "ch_1",
            "order_id": "6f1c0f1e-0000-4000-8000-000000000001",
            "status": "succeeded",
            "amount_minor": 19900,
            "currency": "RUB",
            "card": {"token": "card_1", "title": "Visa", "reusable": True},
        },
    }
).encode()


def headers(ts: int, body: bytes = BODY, secret: str = SECRET) -> dict[str, str]:
    return {"x-acq-timestamp": str(ts), "x-acq-signature": sign_body(secret, str(ts), body)}


def test_valid_event() -> None:
    now = time.time()
    event = parse_event(SECRET, BODY, headers(int(now)), now_ts=now)
    assert event.charge.card_token == "card_1"
    assert event.charge.amount_minor == 19900


def test_changed_body_is_rejected() -> None:
    now = time.time()
    forged = BODY.replace(b"19900", b"100")
    with pytest.raises(BadSignature):
        parse_event(SECRET, forged, headers(int(now)), now_ts=now)


def test_old_event_is_rejected_even_with_valid_signature() -> None:
    now = time.time()
    with pytest.raises(BadSignature, match="stale"):
        parse_event(SECRET, BODY, headers(int(now) - 3600), now_ts=now)


def test_wrong_secret() -> None:
    now = time.time()
    with pytest.raises(BadSignature):
        parse_event(SECRET, BODY, headers(int(now), secret="other"), now_ts=now)
