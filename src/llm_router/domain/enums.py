from __future__ import annotations

from enum import StrEnum


class ProviderProtocol(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class ProviderType(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class RecordStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ChangeType(StrEnum):
    TOPUP = "topup"
    CHARGE = "charge"
    ADJUST = "adjust"
    REFUND = "refund"
