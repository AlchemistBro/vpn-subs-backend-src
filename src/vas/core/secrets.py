"""JWT, хеши одноразовых кодов и токенов, подпись вебхуков."""

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta

import jwt

from vas.core.problems import Unauthorized

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # без 0/O и 1/I/L


def digest(value: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()


def random_digits(n: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(n))


def random_code(n: int = 8) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(n))


def opaque_token() -> str:
    return secrets.token_urlsafe(32)


def issue_access(
    key: str, *, user_id: int, device_id: uuid.UUID, now: datetime, minutes: int
) -> str:
    claims = {
        "sub": str(user_id),
        "dev": str(device_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(claims, key, algorithm="HS256")


def read_access(key: str, token: str, *, now: datetime) -> tuple[int, uuid.UUID]:
    try:
        # Время берём своё, а не системное: так тесты с замороженными часами честные.
        claims = jwt.decode(
            token, key, algorithms=["HS256"], options={"verify_exp": False, "require": ["exp"]}
        )
    except jwt.PyJWTError as exc:
        raise Unauthorized("bad token") from exc
    if claims["exp"] <= now.timestamp():
        raise Unauthorized("token expired")
    return int(claims["sub"]), uuid.UUID(claims["dev"])


def sign_body(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
