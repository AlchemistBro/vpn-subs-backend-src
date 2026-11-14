import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from vas.core.db import Model, Timestamped


class User(Timestamped, Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)  # всегда в lower()
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Device(Timestamped, Model):
    """Слот устройства. Слотов у пользователя ограниченное число."""

    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("user_id", "install_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    install_id: Mapped[str] = mapped_column(String(64))
    platform: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(64))
    push_token: Mapped[str | None] = mapped_column(String(512))
    last_seen_at: Mapped[datetime | None]


class LoginCode(Timestamped, Model):
    """Одноразовый код входа. Храним только HMAC от кода."""

    __tablename__ = "login_codes"
    __table_args__ = (
        Index(
            "uq_login_codes_live_bot_code",
            "code_hash",
            unique=True,
            postgresql_where=text("channel = 'bot' AND used_at IS NULL"),
        ),
        Index("ix_login_codes_lookup", "channel", "target"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel: Mapped[str] = mapped_column(String(8))  # bot | email
    target: Mapped[str] = mapped_column(String(320))  # id пользователя или адрес
    code_hash: Mapped[str] = mapped_column(String(64))
    tries: Mapped[int] = mapped_column(server_default="0")
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class RefreshToken(Timestamped, Model):
    """Refresh-токены одного входа образуют цепочку (chain_id). Повтор старого токена = кража."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    chain_id: Mapped[uuid.UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    rotated_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
