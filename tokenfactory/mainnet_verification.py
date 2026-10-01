from typing import Any, Mapping

from .constants import BASE_MAINNET_CHAIN_ID, BASE_MAINNET_NETWORK_KEY
from .mainnet_lifecycle import MainnetCanaryLifecycle
from .mainnet_review import MainnetTokenReview
from .network_manifest import load_network_manifest
from .validation import normalize_owner_address


def _hash(value: Any, label: str) -> str:
    normalized = str(value or "").lower()
    if (
        len(normalized) != 66
        or not normalized.startswith("0x")
        or any(character not in "0123456789abcdef" for character in normalized[2:])
    ):
        raise ValueError(f"The {label} hash is invalid.")
    return normalized


def _snapshot_values(
    snapshot: Mapping[str, Any],
    review: MainnetTokenReview,
    lifecycle: MainnetCanaryLifecycle,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if int(snapshot.get("chain_id", -1)) != BASE_MAINNET_CHAIN_ID:
        raise ValueError("Verification evidence is not from Base mainnet.")
    if snapshot.get("receipt_success") is not True:
        raise ValueError("The deployment receipt was not successful.")
    transaction_hash = _hash(snapshot.get("transaction_hash"), "transaction")
    if transaction_hash != lifecycle.transaction_hash:
        raise ValueError("The receipt transaction does not match the lifecycle.")
    if normalize_owner_address(snapshot.get("transaction_to")) != review.target_factory:
        raise ValueError("The receipt target factory does not match the review.")
    if _hash(snapshot.get("calldata_sha256"), "calldata") != review.calldata_sha256:
        raise ValueError("The receipt calldata does not match the review.")
    if int(snapshot.get("native_value_wei", -1)) != 0:
        raise ValueError("The deployment transaction unexpectedly transferred native value.")
    if normalize_owner_address(snapshot.get("signer_address")) != review.signer_address:
        raise ValueError("The receipt signer does not match the review.")
    factory_hash = _hash(snapshot.get("factory_runtime_code_hash"), "factory runtime")
    if factory_hash != str(manifest["factoryRuntimeCodeHash"]).lower():
        raise ValueError("The deployed factory runtime does not match the manifest.")
    token_hash = _hash(snapshot.get("token_runtime_code_hash"), "token runtime")
    if token_hash != str(manifest["tokenTemplateUnlinkedRuntimeCodeHash"]).lower():
        raise ValueError("The deployed token runtime does not match the manifest.")
    token_address = normalize_owner_address(snapshot.get("token_address"))
    if token_address == "0x0000000000000000000000000000000000000000":
        raise ValueError("The verified token address is empty.")
    if str(snapshot.get("name")) != review.name:
        raise ValueError("The verified token name does not match the review.")
    if str(snapshot.get("symbol")) != review.symbol:
        raise ValueError("The verified token symbol does not match the review.")
    if int(snapshot.get("decimals", -1)) != review.decimals:
        raise ValueError("The verified token decimals do not match the review.")
    if int(snapshot.get("total_supply_atomic", -1)) != review.supply_atomic:
        raise ValueError("The verified fixed supply does not match the review.")
    if int(snapshot.get("recipient_balance_atomic", -1)) != review.supply_atomic:
        raise ValueError("The recipient did not receive the complete fixed supply.")
    if normalize_owner_address(snapshot.get("recipient")) != review.recipient:
        raise ValueError("The verified recipient does not match the review.")
    if _hash(snapshot.get("event_topic"), "event topic") != str(
        manifest["fixedSupplyTokenCreatedTopic"]
    ).lower():
        raise ValueError("The expected factory creation event is missing.")
    if _hash(snapshot.get("event_request_id"), "event request ID") != review.request_id:
        raise ValueError("The factory event request ID does not match the review.")
    if normalize_owner_address(snapshot.get("event_token")) != token_address:
        raise ValueError("The factory event token does not match the deployed token.")
    if normalize_owner_address(snapshot.get("event_recipient")) != review.recipient:
        raise ValueError("The factory event recipient does not match the review.")
    parameters_hash = _hash(snapshot.get("event_parameters_hash"), "event parameters")
    if _hash(snapshot.get("registry_request_id"), "registry request ID") != review.request_id:
        raise ValueError("The factory registry request ID does not match the review.")
    if normalize_owner_address(snapshot.get("registry_token")) != token_address:
        raise ValueError("The factory registry token does not match the event.")
    if _hash(snapshot.get("registry_parameters_hash"), "registry parameters") != parameters_hash:
        raise ValueError("The factory registry parameters do not match the event.")
    block_hash = _hash(snapshot.get("block_hash"), "block")
    block_number = int(snapshot.get("block_number", -1))
    if block_number < 0 or block_number != lifecycle.block_number:
        raise ValueError("The verified block does not match the lifecycle.")
    return {
        "network": BASE_MAINNET_NETWORK_KEY,
        "chain_id": BASE_MAINNET_CHAIN_ID,
        "transaction_hash": transaction_hash,
        "block_number": block_number,
        "block_hash": block_hash,
        "token_address": token_address,
        "factory_runtime_code_hash": factory_hash,
        "token_runtime_code_hash": token_hash,
        "parameters_hash": parameters_hash,
        "name": review.name,
        "symbol": review.symbol,
        "decimals": review.decimals,
        "supply_atomic": review.supply_atomic,
        "recipient": review.recipient,
        "request_id": review.request_id,
    }


def verify_mainnet_canary_evidence(
    review: MainnetTokenReview,
    lifecycle: MainnetCanaryLifecycle,
    primary: Mapping[str, Any],
    secondary: Mapping[str, Any],
) -> dict[str, Any]:
    """Require two independently collected, identical mainnet verification snapshots."""

    if lifecycle.status != "confirmed":
        raise ValueError("Mainnet deployment evidence requires a confirmed lifecycle.")
    if lifecycle.review_fingerprint != review.fingerprint:
        raise ValueError("The confirmed lifecycle does not match the reviewed deployment.")
    manifest = load_network_manifest(BASE_MAINNET_NETWORK_KEY)
    first = _snapshot_values(primary, review, lifecycle, manifest)
    second = _snapshot_values(secondary, review, lifecycle, manifest)
    if first != second:
        raise ValueError("The two independent RPC verification results disagree.")
    return {
        **first,
        "review_fingerprint": review.fingerprint,
        "attempt_id": lifecycle.attempt_id,
        "independent_rpc_verifications": 2,
        "verified": True,
    }
