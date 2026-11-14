import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from vas.core.db import Model, Timestamped


class PanelAccount(Timestamped, Model):
    """Наш пользователь в VPN-панели. uuid выдаём сами до первого запроса в панель."""

    __tablename__ = "panel_accounts"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    panel_uuid: Mapped[uuid.UUID] = mapped_column(unique=True, default=uuid.uuid4)
    sub_url: Mapped[str | None] = mapped_column(String(512))
    synced_until: Mapped[datetime | None]
    synced_at: Mapped[datetime | None]
