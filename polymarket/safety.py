"""Strict installation monetary limits for default-off Polymarket execution."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class SafetyLimitError(ValueError):
    """Raised when monetary safety limits are malformed or inconsistent."""


def _amount(value: Any, field: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SafetyLimitError(f"{field} must be a decimal pUSD amount") from exc
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -6:
        raise SafetyLimitError(f"{field} must be non-negative with at most 6 decimals")
    return amount


@dataclass(frozen=True)
class ProductionLimits:
    per_order_pusd: Decimal
    per_user_day_pusd: Decimal
    installation_day_pusd: Decimal

    def __post_init__(self) -> None:
        order = _amount(self.per_order_pusd, "per_order_pusd")
        user = _amount(self.per_user_day_pusd, "per_user_day_pusd")
        installation = _amount(self.installation_day_pusd, "installation_day_pusd")
        if (order == 0 or user == 0 or installation == 0) and any(
            value != 0 for value in (order, user, installation)
        ):
            raise SafetyLimitError("limits must be all zero or all positive")
        if order > user or user > installation:
            raise SafetyLimitError(
                "limits must satisfy per-order <= per-user-day <= installation-day"
            )
        object.__setattr__(self, "per_order_pusd", order)
        object.__setattr__(self, "per_user_day_pusd", user)
        object.__setattr__(self, "installation_day_pusd", installation)

    @property
    def execution_disabled(self) -> bool:
        return self.per_order_pusd == 0

    def permits(self, *, order_pusd: Any, user_day_pusd: Any, installation_day_pusd: Any) -> bool:
        order = _amount(order_pusd, "order_pusd")
        user = _amount(user_day_pusd, "user_day_pusd")
        installation = _amount(installation_day_pusd, "installation_day_pusd")
        return (
            not self.execution_disabled
            and order <= self.per_order_pusd
            and user + order <= self.per_user_day_pusd
            and installation + order <= self.installation_day_pusd
        )

    def to_record(self) -> dict[str, str]:
        return {
            "per_order_pusd": str(self.per_order_pusd),
            "per_user_day_pusd": str(self.per_user_day_pusd),
            "installation_day_pusd": str(self.installation_day_pusd),
        }

    @classmethod
    def from_record(cls, record: Any) -> "ProductionLimits":
        expected = {"per_order_pusd", "per_user_day_pusd", "installation_day_pusd"}
        if not isinstance(record, dict) or set(record) != expected:
            raise SafetyLimitError("production limits record is invalid")
        return cls(**record)
