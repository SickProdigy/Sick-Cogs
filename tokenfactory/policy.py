from copy import deepcopy
from typing import Any


MAINNET_LIMITS_DEFAULT = {
    "max_token_supply_atomic": 10**27,
    "factory_gas_limit": 2_000_000,
    "token_gas_limit": 1_500_000,
    "max_gas_fee_wei": 2 * 10**15,
    "native_value_wei": 0,
}


class TokenFactoryPolicyError(RuntimeError):
    """TokenFactory mainnet policy is incomplete or unsafe."""


def validate_mainnet_limits(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != set(MAINNET_LIMITS_DEFAULT):
        raise TokenFactoryPolicyError("Base mainnet TokenFactory limits are incomplete.")
    normalized: dict[str, int] = {}
    for key, ceiling in MAINNET_LIMITS_DEFAULT.items():
        try:
            current = int(value[key])
        except (TypeError, ValueError) as exc:
            raise TokenFactoryPolicyError(
                f"Base mainnet TokenFactory limit {key} is invalid."
            ) from exc
        if current < 0 or current > ceiling:
            raise TokenFactoryPolicyError(
                f"Base mainnet TokenFactory limit {key} exceeds its reviewed ceiling."
            )
        normalized[key] = current
    if normalized["native_value_wei"] != 0:
        raise TokenFactoryPolicyError("TokenFactory native value must remain zero.")
    return normalized


def migrate_mainnet_limits(value: Any) -> dict[str, int]:
    """Migrate reviewed limits and drop unenforced pre-release daily fields."""

    if not isinstance(value, dict):
        raise TokenFactoryPolicyError("Base mainnet TokenFactory limits are incomplete.")
    legacy_fields = {"factory_deployments_per_day", "token_deployments_per_day"}
    unknown = set(value) - set(MAINNET_LIMITS_DEFAULT) - legacy_fields
    if unknown:
        raise TokenFactoryPolicyError(
            "Base mainnet TokenFactory limits contain unknown fields."
        )
    current = {key: item for key, item in value.items() if key not in legacy_fields}
    return validate_mainnet_limits({**MAINNET_LIMITS_DEFAULT, **current})


def default_mainnet_limits() -> dict[str, int]:
    return deepcopy(MAINNET_LIMITS_DEFAULT)
