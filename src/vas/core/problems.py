"""Ошибки, которые доходят до клиента. Код ошибки стабилен, текст можно менять."""

from typing import Any


class Problem(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str = "", **details: Any) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details = details


class NotFound(Problem):
    status = 404
    code = "not_found"


class Unauthorized(Problem):
    status = 401
    code = "unauthorized"


class Forbidden(Problem):
    status = 403
    code = "forbidden"


class Conflict(Problem):
    status = 409
    code = "conflict"


class TooManyRequests(Problem):
    status = 429
    code = "too_many_requests"


class UpstreamDown(Problem):
    """Внешняя система не ответила. Клиенту 503, повтор безопасен."""

    status = 503
    code = "upstream_unavailable"
