"""Тарифы. Это данные, поэтому отдельная миграция и sa.table вместо моделей:
модели со временем поменяются, а старая миграция должна работать как раньше."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

plans = sa.table(
    "plans",
    sa.column("code", sa.String),
    sa.column("title", sa.String),
    sa.column("days", sa.Integer),
    sa.column("price_minor", sa.Integer),
)


def upgrade() -> None:
    op.bulk_insert(
        plans,
        [
            {"code": "m1", "title": "Месяц", "days": 30, "price_minor": 19900},
            {"code": "m3", "title": "3 месяца", "days": 90, "price_minor": 49900},
            {"code": "y1", "title": "Год", "days": 365, "price_minor": 149000},
        ],
    )


def downgrade() -> None:
    op.execute(plans.delete().where(plans.c.code.in_(["m1", "m3", "y1"])))
