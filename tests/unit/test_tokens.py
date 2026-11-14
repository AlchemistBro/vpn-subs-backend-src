import uuid
from datetime import UTC, datetime, timedelta

import pytest

from vas.core.problems import Unauthorized
from vas.core.secrets import issue_access, read_access

KEY = "k" * 32
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_round_trip() -> None:
    dev = uuid.uuid4()
    token = issue_access(KEY, user_id=7, device_id=dev, now=NOW, minutes=15)
    assert read_access(KEY, token, now=NOW + timedelta(minutes=14)) == (7, dev)


def test_expired() -> None:
    token = issue_access(KEY, user_id=7, device_id=uuid.uuid4(), now=NOW, minutes=15)
    with pytest.raises(Unauthorized, match="expired"):
        read_access(KEY, token, now=NOW + timedelta(minutes=15))


def test_foreign_key() -> None:
    token = issue_access("x" * 32, user_id=7, device_id=uuid.uuid4(), now=NOW, minutes=15)
    with pytest.raises(Unauthorized):
        read_access(KEY, token, now=NOW)
