"""Composed fail-closed boundary for future TokenFactory mainnet provider calls."""
from typing import Any, Mapping
from .constants import BASE_MAINNET_NETWORK_KEY
from .mainnet_approval import MainnetCanaryApproval, revalidate_mainnet_pre_submission
from .mainnet_factory import MainnetFactoryReview, revalidate_mainnet_factory_pre_submission
from .mainnet_review import MainnetTokenReview
from .network_manifest import load_network_manifest

class MainnetExecutionDisabled(RuntimeError):
    """The reviewed candidate has not been authorized for real submission."""

def _require_authorization(capability: str, controls: Mapping[str, Any]) -> dict[str, Any]:
    manifest = load_network_manifest(BASE_MAINNET_NETWORK_KEY)
    authorization = manifest["authorization"]
    if (
        controls.get("enabled") is not True
        or controls.get("paused") is not False
        or authorization.get(capability) is not True
    ):
        raise MainnetExecutionDisabled(
            "TokenFactory Base mainnet submission remains disabled by owner controls "
            "or the pinned candidate manifest."
        )
    if capability == "memberDeployment" and not manifest.get("factoryAddress"):
        raise MainnetExecutionDisabled(
            "TokenFactory Base mainnet token submission requires a verified deployed factory."
        )
    return manifest

def prepare_mainnet_token_submission(
    review: MainnetTokenReview,
    approval: MainnetCanaryApproval,
    operation: dict[str, Any],
    *,
    controls: Mapping[str, Any],
    live: Mapping[str, Any],
    limits: dict[str, Any],
    now: int,
) -> dict[str, Any]:
    """Revalidate the exact token candidate, then enforce the independent release gate."""
    validated = revalidate_mainnet_pre_submission(
        review, approval, operation,
        owner_discord_id=int(live["owner_discord_id"]),
        wallet_profile_id=str(live["wallet_profile_id"]),
        signer_address=str(live["signer_address"]),
        live_chain_id=int(live["chain_id"]),
        live_factory_code_hash=str(live["factory_code_hash"]),
        authorization_active=live.get("authorization_active") is True,
        signer_balance_wei=int(live["signer_balance_wei"]),
        operation_state=str(live["operation_state"]),
        limits=limits, now=now,
    )
    manifest = _require_authorization("memberDeployment", controls)
    if str(manifest["factoryAddress"]).lower() != review.target_factory:
        raise MainnetExecutionDisabled(
            "The authorized mainnet factory does not match the reviewed token target."
        )
    return {**validated, "manifest_status": manifest["status"], "submission_ready": True}

def prepare_mainnet_factory_submission(
    review: MainnetFactoryReview,
    approval: MainnetCanaryApproval,
    operation: Mapping[str, Any],
    *,
    controls: Mapping[str, Any],
    live: Mapping[str, Any],
    limits: Mapping[str, Any],
    now: int,
) -> dict[str, Any]:
    """Revalidate the deterministic factory candidate, then enforce its release gate."""
    validated = revalidate_mainnet_factory_pre_submission(
        review, approval, operation,
        owner_discord_id=int(live["owner_discord_id"]),
        wallet_profile_id=str(live["wallet_profile_id"]),
        signer_address=str(live["signer_address"]),
        live_chain_id=int(live["chain_id"]),
        live_singleton_code_sha256=str(live["singleton_code_sha256"]),
        destination_empty=live.get("destination_empty") is True,
        authorization_active=live.get("authorization_active") is True,
        operation_state=str(live["operation_state"]),
        limits=limits, now=now,
    )
    manifest = _require_authorization("factoryDeployment", controls)
    return {**validated, "manifest_status": manifest["status"], "submission_ready": True}
