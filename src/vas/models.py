"""Все модели в одном месте: для Alembic и для create_all в тестах не нужен."""

from vas.access.models import PanelAccount
from vas.accounts.models import Device, LoginCode, RefreshToken, User
from vas.billing.models import (
    AccessPeriod,
    Payment,
    Plan,
    ProviderEvent,
    RenewalAttempt,
    Subscription,
)
from vas.core.db import Model
from vas.events.models import OutboxEvent

__all__ = [
    "AccessPeriod",
    "Device",
    "LoginCode",
    "Model",
    "OutboxEvent",
    "PanelAccount",
    "Payment",
    "Plan",
    "ProviderEvent",
    "RefreshToken",
    "RenewalAttempt",
    "Subscription",
    "User",
]
