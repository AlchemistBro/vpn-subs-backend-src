import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

HIDDEN = {"token", "refresh_token", "access_token", "code", "card_token", "sub_url"}


def _hide(_: object, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in HIDDEN & event.keys():
        event[key] = "[hidden]"
    return event


def setup_logging(*, json: bool = True, level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _hide,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")
