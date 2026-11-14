from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from vas.core.db import Model, Timestamped


class OutboxEvent(Timestamped, Model):
    """Событие пишется в той же транзакции, что и изменение. Отправку делает cron."""

    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_unsent", "id", postgresql_where=text("sent_at IS NULL")),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    topic: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sent_at: Mapped[datetime | None]
    tries: Mapped[int] = mapped_column(server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(256))
