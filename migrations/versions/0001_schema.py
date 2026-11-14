"""Схема: аккаунты, оплаты, журнал периодов, автопродление, панель, outbox."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_codes",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=8), nullable=False),
        sa.Column("target", sa.String(length=320), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("tries", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_codes")),
    )
    op.create_index("ix_login_codes_lookup", "login_codes", ["channel", "target"], unique=False)
    op.create_index(
        "uq_login_codes_live_bot_code",
        "login_codes",
        ["code_hash"],
        unique=True,
        postgresql_where=sa.text("channel = 'bot' AND used_at IS NULL"),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("topic", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tries", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
    )
    op.create_index(
        "ix_outbox_unsent",
        "outbox",
        ["id"],
        unique=False,
        postgresql_where=sa.text("sent_at IS NULL"),
    )
    op.create_table(
        "plans",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("price_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="RUB", nullable=False),
        sa.Column("on_sale", sa.Boolean(), server_default="true", nullable=False),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_plans")),
    )
    op.create_table(
        "provider_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_events")),
        sa.UniqueConstraint(
            "provider", "event_id", name=op.f("uq_provider_events_provider_event_id")
        ),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("invited_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"], ["users.id"], name=op.f("fk_users_invited_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.UniqueConstraint("telegram_id", name=op.f("uq_users_telegram_id")),
    )
    op.create_table(
        "access_periods",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "days > 0 AND ends_at > starts_at", name=op.f("ck_access_periods_positive")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_access_periods_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_access_periods")),
        sa.UniqueConstraint(
            "source_type", "source_id", name=op.f("uq_access_periods_source_type_source_id")
        ),
    )
    op.create_index(op.f("ix_access_periods_user_id"), "access_periods", ["user_id"], unique=False)
    op.create_table(
        "devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("install_id", sa.String(length=64), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("push_token", sa.String(length=512), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_devices_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_devices")),
        sa.UniqueConstraint("user_id", "install_id", name=op.f("uq_devices_user_id_install_id")),
    )
    op.create_index(op.f("ix_devices_user_id"), "devices", ["user_id"], unique=False)
    op.create_table(
        "panel_accounts",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("panel_uuid", sa.Uuid(), nullable=False),
        sa.Column("sub_url", sa.String(length=512), nullable=True),
        sa.Column("synced_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_panel_accounts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_panel_accounts")),
        sa.UniqueConstraint("panel_uuid", name=op.f("uq_panel_accounts_panel_uuid")),
    )
    op.create_table(
        "renewal_attempts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("cycle_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="planned", nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_renewal_attempts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_renewal_attempts")),
        sa.UniqueConstraint(
            "user_id",
            "cycle_end",
            "attempt",
            name=op.f("uq_renewal_attempts_user_id_cycle_end_attempt"),
        ),
    )
    op.create_index(
        "ix_renewal_attempts_due",
        "renewal_attempts",
        ["run_after"],
        unique=False,
        postgresql_where=sa.text("state = 'planned'"),
    )
    op.create_table(
        "subscriptions",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("paid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan_code", sa.String(length=32), nullable=True),
        sa.Column("autorenew", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("card_token", sa.String(length=128), nullable=True),
        sa.Column("card_title", sa.String(length=64), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["plan_code"], ["plans.code"], name=op.f("fk_subscriptions_plan_code_plans")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_subscriptions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_subscriptions")),
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_code", sa.String(length=32), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("client_key", sa.String(length=80), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="new", nullable=False),
        sa.Column("hold_reason", sa.String(length=64), nullable=True),
        sa.Column("save_card", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=True),
        sa.Column("pay_url", sa.String(length=512), nullable=True),
        sa.Column("renewal_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('new', 'awaiting', 'paid', 'declined', 'expired', 'on_hold')",
            name=op.f("ck_payments_known_status"),
        ),
        sa.ForeignKeyConstraint(
            ["plan_code"], ["plans.code"], name=op.f("fk_payments_plan_code_plans")
        ),
        sa.ForeignKeyConstraint(
            ["renewal_id"],
            ["renewal_attempts.id"],
            name=op.f("fk_payments_renewal_id_renewal_attempts"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_payments_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint("provider_id", name=op.f("uq_payments_provider_id")),
        sa.UniqueConstraint("user_id", "client_key", name=op.f("uq_payments_user_id_client_key")),
    )
    op.create_index(op.f("ix_payments_user_id"), "payments", ["user_id"], unique=False)
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("chain_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_refresh_tokens_device_id_devices"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_refresh_tokens_chain_id"), "refresh_tokens", ["chain_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_refresh_tokens_chain_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index(op.f("ix_payments_user_id"), table_name="payments")
    op.drop_table("payments")
    op.drop_table("subscriptions")
    op.drop_index(
        "ix_renewal_attempts_due",
        table_name="renewal_attempts",
        postgresql_where=sa.text("state = 'planned'"),
    )
    op.drop_table("renewal_attempts")
    op.drop_table("panel_accounts")
    op.drop_index(op.f("ix_devices_user_id"), table_name="devices")
    op.drop_table("devices")
    op.drop_index(op.f("ix_access_periods_user_id"), table_name="access_periods")
    op.drop_table("access_periods")
    op.drop_table("users")
    op.drop_table("provider_events")
    op.drop_table("plans")
    op.drop_index(
        "ix_outbox_unsent", table_name="outbox", postgresql_where=sa.text("sent_at IS NULL")
    )
    op.drop_table("outbox")
    op.drop_index(
        "uq_login_codes_live_bot_code",
        table_name="login_codes",
        postgresql_where=sa.text("channel = 'bot' AND used_at IS NULL"),
    )
    op.drop_index("ix_login_codes_lookup", table_name="login_codes")
    op.drop_table("login_codes")
