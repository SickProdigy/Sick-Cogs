from copy import deepcopy
from typing import Any


MAINNET_LIMITS_DEFAULT = {
    "factory_deployments_per_day": 1,
    "token_deployments_per_day": 1,
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
    if normalized["factory_deployments_per_day"] != 1:
        raise TokenFactoryPolicyError("The one-time factory canary limit must remain one.")
    if normalized["token_deployments_per_day"] != 1:
        raise TokenFactoryPolicyError("The owner token canary limit must remain one per day.")
    if normalized["native_value_wei"] != 0:
        raise TokenFactoryPolicyError("TokenFactory native value must remain zero.")
    return normalized


def migrate_mainnet_limits(value: Any) -> dict[str, int]:
    """Add newly reviewed limits without accepting unknown or expanded policy."""

    if not isinstance(value, dict):
        raise TokenFactoryPolicyError("Base mainnet TokenFactory limits are incomplete.")
    unknown = set(value) - set(MAINNET_LIMITS_DEFAULT)
    if unknown:
        raise TokenFactoryPolicyError(
            "Base mainnet TokenFactory limits contain unknown fields."
        )
    return validate_mainnet_limits({**MAINNET_LIMITS_DEFAULT, **value})


def default_mainnet_limits() -> dict[str, int]:
    return deepcopy(MAINNET_LIMITS_DEFAULT)
