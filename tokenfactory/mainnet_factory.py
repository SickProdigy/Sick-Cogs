import json
import secrets
import time
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any, Mapping

from .constants import BASE_MAINNET_CHAIN_ID, BASE_MAINNET_NETWORK_KEY
from .mainnet_approval import (
    MainnetCanaryApproval,
    create_mainnet_canary_approval,
)
from .mainnet_lifecycle import MainnetCanaryLifecycle
from .network_manifest import load_network_manifest
from .operations import factory_operation
from .policy import validate_mainnet_limits
from .validation import normalize_owner_address


@dataclass(frozen=True, slots=True)
class MainnetFactoryReview:
    network: str
    chain_id: int
    owner_discord_id: int
    wallet_profile_id: str
    signer_address: str
    request_id: str
    singleton_address: str
    predicted_factory_address: str
    creation_code_sha256: str
    manifest_creation_code_hash: str
    calldata_sha256: str
    gas_limit: int
    max_gas_fee_wei: int
    gas_payer: str
    native_value_wei: int
    irreversible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MainnetFactoryReview":
        return cls(
            network=str(data["network"]),
            chain_id=int(data["chain_id"]),
            owner_discord_id=int(data["owner_discord_id"]),
            wallet_profile_id=str(data["wallet_profile_id"]),
            signer_address=normalize_owner_address(data["signer_address"]),
            request_id=str(data["request_id"]).lower(),
            singleton_address=normalize_owner_address(
                data["singleton_address"]
            ).lower(),
            predicted_factory_address=normalize_owner_address(
                data["predicted_factory_address"]
            ).lower(),
            creation_code_sha256=str(data["creation_code_sha256"]).lower(),
            manifest_creation_code_hash=str(
                data["manifest_creation_code_hash"]
            ).lower(),
            calldata_sha256=str(data["calldata_sha256"]).lower(),
            gas_limit=int(data["gas_limit"]),
            max_gas_fee_wei=int(data["max_gas_fee_wei"]),
            gas_payer=str(data["gas_payer"]),
            native_value_wei=int(data["native_value_wei"]),
            irreversible=data.get("irreversible") is True,
        )

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return "0x" + sha256(payload).hexdigest()


def build_mainnet_factory_review(
    creation_code: str,
    *,
    owner_discord_id: int,
    wallet_profile_id: str,
    signer_address: str,
    max_gas_fee_wei: int,
    gas_payer: str,
    limits: Mapping[str, Any],
) -> MainnetFactoryReview:
    reviewed_limits = validate_mainnet_limits(limits)
    manifest = load_network_manifest(BASE_MAINNET_NETWORK_KEY)
    profile_id = str(wallet_profile_id or "").strip()
    if not profile_id or len(profile_id) > 160:
        raise ValueError("The reviewed wallet profile is invalid.")
    signer = normalize_owner_address(signer_address)
    if (
        int(max_gas_fee_wei) <= 0
        or int(max_gas_fee_wei) > reviewed_limits["max_gas_fee_wei"]
    ):
        raise ValueError("The maximum gas fee exceeds the reviewed factory ceiling.")
    payer = str(gas_payer or "").strip()
    if not payer or len(payer) > 80:
        raise ValueError("The reviewed gas payer is invalid.")
    operation = factory_operation(
        creation_code, network=BASE_MAINNET_NETWORK_KEY
    )
    if operation["gas_limit"] != reviewed_limits["factory_gas_limit"]:
        raise ValueError("The factory gas limit does not match mainnet policy.")
    if operation["value_wei"] != reviewed_limits["native_value_wei"]:
        raise ValueError("The factory native value does not match mainnet policy.")
    deployment_salt = str(manifest["deploymentSalt"]).lower()
    return MainnetFactoryReview(
        network=BASE_MAINNET_NETWORK_KEY,
        chain_id=BASE_MAINNET_CHAIN_ID,
        owner_discord_id=int(owner_discord_id),
        wallet_profile_id=profile_id,
        signer_address=signer,
        request_id=deployment_salt,
        singleton_address=normalize_owner_address(
            manifest["singletonFactory"]
        ).lower(),
        predicted_factory_address=normalize_owner_address(
            manifest["predictedFactoryAddress"]
        ).lower(),
        creation_code_sha256="0x" + sha256(
            bytes.fromhex(creation_code[2:])
        ).hexdigest(),
        manifest_creation_code_hash=str(
            manifest["factoryCreationCodeHash"]
        ).lower(),
        calldata_sha256="0x" + sha256(
            bytes.fromhex(str(operation["data"])[2:])
        ).hexdigest(),
        gas_limit=int(operation["gas_limit"]),
        max_gas_fee_wei=int(max_gas_fee_wei),
        gas_payer=payer,
        native_value_wei=int(operation["value_wei"]),
    )


def revalidate_mainnet_factory_pre_submission(
    review: MainnetFactoryReview,
    approval: MainnetCanaryApproval,
    operation: Mapping[str, Any],
    *,
    owner_discord_id: int,
    wallet_profile_id: str,
    signer_address: str,
    live_chain_id: int,
    live_singleton_code_sha256: str,
    destination_empty: bool,
    authorization_active: bool,
    operation_state: str,
    limits: Mapping[str, Any],
    now: int | None = None,
) -> dict[str, Any]:
    timestamp = int(time.time() if now is None else now)
    reviewed_limits = validate_mainnet_limits(limits)
    manifest = load_network_manifest(BASE_MAINNET_NETWORK_KEY)
    if approval.totp_verified is not True or approval.consumed_at is not None:
        raise ValueError("The protected factory approval is missing or consumed.")
    if timestamp < approval.approved_at or timestamp >= approval.expires_at:
        raise ValueError("The protected factory approval has expired.")
    if (
        int(owner_discord_id) != review.owner_discord_id
        or approval.owner_discord_id != review.owner_discord_id
        or not secrets.compare_digest(
            approval.review_fingerprint, review.fingerprint
        )
    ):
        raise ValueError("The protected factory approval binding changed.")
    if str(wallet_profile_id) != review.wallet_profile_id:
        raise ValueError("The factory wallet profile changed after review.")
    if normalize_owner_address(signer_address) != review.signer_address:
        raise ValueError("The factory signer changed after review.")
    if live_chain_id != BASE_MAINNET_CHAIN_ID:
        raise ValueError("The live chain is not Base mainnet.")
    if (
        operation.get("network") != BASE_MAINNET_NETWORK_KEY
        or int(operation.get("chain_id", -1)) != BASE_MAINNET_CHAIN_ID
    ):
        raise ValueError("The factory operation network changed after review.")
    if normalize_owner_address(operation.get("to")).lower() != review.singleton_address:
        raise ValueError("The singleton target changed after review.")
    data = str(operation.get("data") or "")
    try:
        calldata_hash = "0x" + sha256(bytes.fromhex(data[2:])).hexdigest()
    except (TypeError, ValueError) as exc:
        raise ValueError("The factory calldata is invalid.") from exc
    if not data.startswith("0x") or calldata_hash != review.calldata_sha256:
        raise ValueError("The factory calldata changed after review.")
    if (
        int(operation.get("gas_limit", -1)) != review.gas_limit
        or review.gas_limit != reviewed_limits["factory_gas_limit"]
    ):
        raise ValueError("The factory gas limit changed after review.")
    if (
        review.max_gas_fee_wei <= 0
        or review.max_gas_fee_wei > reviewed_limits["max_gas_fee_wei"]
    ):
        raise ValueError("The factory gas maximum violates current policy.")
    if int(operation.get("value_wei", -1)) != 0 or review.native_value_wei != 0:
        raise ValueError("The factory native value must remain zero.")
    expected_singleton = str(
        manifest["observations"]["singletonRuntimeSha256"]
    ).lower()
    if str(live_singleton_code_sha256 or "").lower() != expected_singleton:
        raise ValueError("The live singleton code does not match preflight evidence.")
    if destination_empty is not True:
        raise ValueError("The predicted factory destination is no longer empty.")
    if authorization_active is not True:
        raise ValueError("The mainnet signer authorization is not active.")
    if operation_state != "not-created":
        raise ValueError("A factory provider operation or transaction already exists.")
    return {
        "review_fingerprint": review.fingerprint,
        "approval_expires_at": approval.expires_at,
        "singleton_code_sha256": expected_singleton,
        "destination_empty": True,
        "operation_state": "not-created",
    }


def _factory_snapshot(
    snapshot: Mapping[str, Any],
    review: MainnetFactoryReview,
    lifecycle: MainnetCanaryLifecycle,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if int(snapshot.get("chain_id", -1)) != BASE_MAINNET_CHAIN_ID:
        raise ValueError("Factory evidence is not from Base mainnet.")
    if snapshot.get("receipt_success") is not True:
        raise ValueError("The factory deployment receipt was not successful.")
    transaction_hash = str(snapshot.get("transaction_hash") or "").lower()
    if transaction_hash != lifecycle.transaction_hash:
        raise ValueError("The factory transaction does not match the lifecycle.")
    if normalize_owner_address(snapshot.get("transaction_to")).lower() != review.singleton_address:
        raise ValueError("The factory transaction did not target the singleton.")
    if str(snapshot.get("calldata_sha256") or "").lower() != review.calldata_sha256:
        raise ValueError("The factory transaction calldata does not match the review.")
    if int(snapshot.get("native_value_wei", -1)) != 0:
        raise ValueError("The factory deployment transferred native value.")
    if normalize_owner_address(snapshot.get("signer_address")) != review.signer_address:
        raise ValueError("The factory deployment signer does not match the review.")
    singleton_hash = str(snapshot.get("singleton_code_sha256") or "").lower()
    expected_singleton = str(
        manifest["observations"]["singletonRuntimeSha256"]
    ).lower()
    if singleton_hash != expected_singleton:
        raise ValueError("The singleton runtime does not match its independent pin.")
    factory_address = normalize_owner_address(
        snapshot.get("factory_address")
    ).lower()
    if factory_address != review.predicted_factory_address:
        raise ValueError("The deployed factory address does not match its prediction.")
    runtime_hash = str(snapshot.get("factory_runtime_code_hash") or "").lower()
    if runtime_hash != str(manifest["factoryRuntimeCodeHash"]).lower():
        raise ValueError("The deployed factory runtime does not match the manifest.")
    if snapshot.get("factory_has_owner") is not False:
        raise ValueError("The deployed factory unexpectedly exposes ownership.")
    if snapshot.get("factory_is_upgradeable") is not False:
        raise ValueError("The deployed factory unexpectedly exposes an upgrade path.")
    block_number = int(snapshot.get("block_number", -1))
    if block_number != lifecycle.block_number:
        raise ValueError("The factory deployment block does not match the lifecycle.")
    block_hash = str(snapshot.get("block_hash") or "").lower()
    if len(block_hash) != 66 or not block_hash.startswith("0x"):
        raise ValueError("The factory deployment block hash is invalid.")
    return {
        "network": BASE_MAINNET_NETWORK_KEY,
        "chain_id": BASE_MAINNET_CHAIN_ID,
        "transaction_hash": transaction_hash,
        "block_number": block_number,
        "block_hash": block_hash,
        "factory_address": factory_address,
        "factory_runtime_code_hash": runtime_hash,
        "singleton_code_sha256": singleton_hash,
        "factory_has_owner": False,
        "factory_is_upgradeable": False,
    }


def verify_mainnet_factory_evidence(
    review: MainnetFactoryReview,
    lifecycle: MainnetCanaryLifecycle,
    primary: Mapping[str, Any],
    secondary: Mapping[str, Any],
) -> dict[str, Any]:
    if lifecycle.status != "confirmed":
        raise ValueError("Factory evidence requires a confirmed lifecycle.")
    if lifecycle.review_fingerprint != review.fingerprint:
        raise ValueError("The factory lifecycle does not match the reviewed deployment.")
    manifest = load_network_manifest(BASE_MAINNET_NETWORK_KEY)
    first = _factory_snapshot(primary, review, lifecycle, manifest)
    second = _factory_snapshot(secondary, review, lifecycle, manifest)
    if first != second:
        raise ValueError("The two independent factory RPC results disagree.")
    return {
        **first,
        "review_fingerprint": review.fingerprint,
        "attempt_id": lifecycle.attempt_id,
        "independent_rpc_verifications": 2,
        "verified": True,
    }
