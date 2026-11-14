"""Частичный индекс для сверки: она ищет только незавершённые платежи.

CREATE INDEX CONCURRENTLY не блокирует запись, но не работает внутри транзакции,
поэтому выполняем его в autocommit_block. Если построение упало, индекс останется
INVALID, и IF NOT EXISTS его не перестроит: перед повтором нужен DROP INDEX.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_open "
            "ON payments (updated_at) WHERE status IN ('new', 'awaiting')"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_payments_open")
