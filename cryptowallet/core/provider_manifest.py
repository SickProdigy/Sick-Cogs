import hashlib
import json
from dataclasses import asdict, dataclass

from .networks import BASE_MAINNET, ChainFamily, Network


@dataclass(frozen=True, slots=True)
class EvmProviderManifest:
    """Immutable reviewed provider assumptions for one disabled production network."""

    schema_version: int
    network_key: str
    provider_network: str
    chain_id: int
    family: str
    account_type: str
    owner_account_type: str
    owner_relationship_field: str
    required_owner_count: int
    exportable_account_type: str
    smart_account_key_exportable: bool
    paymaster_behavior: str
    fee_quote_required: bool
    bounded_user_paid_fee_supported: bool
    spend_permission_fields: tuple[str, ...]
    operation_statuses: tuple[str, ...]
    operation_identifiers: tuple[str, ...]
    executable_capabilities: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


BASE_MAINNET_PROVIDER_MANIFEST = EvmProviderManifest(
    schema_version=2,
    network_key="base-mainnet",
    provider_network="base",
    chain_id=8453,
    family="evm",
    account_type="erc-4337-smart-account",
    owner_account_type="evm-eoa",
    owner_relationship_field="ownerAddresses",
    required_owner_count=1,
    exportable_account_type="evm-eoa-owner",
    smart_account_key_exportable=False,
    paymaster_behavior="optional-sponsorship-unbounded-user-paid",
    fee_quote_required=True,
    bounded_user_paid_fee_supported=False,
    spend_permission_fields=(
        "network", "spender", "token", "allowance", "period", "start", "end",
        "salt", "extraData", "paymasterUrl",
    ),
    operation_statuses=(
        "pending", "signed", "broadcast", "complete", "dropped", "failed",
    ),
    operation_identifiers=("userOpHash", "transactionHash"),
    executable_capabilities=(),
)


def validate_evm_provider_manifest(
    manifest: EvmProviderManifest, network: Network
) -> tuple[str, ...]:
    """Return every drift reason; an empty tuple is the only passing result."""

    errors = []
    if manifest.schema_version != 2:
        errors.append("unsupported manifest schema")
    if manifest.network_key != network.key:
        errors.append("network key mismatch")
    if manifest.provider_network != "base":
        errors.append("provider network mismatch")
    if manifest.chain_id != network.chain_id:
        errors.append("chain ID mismatch")
    if manifest.family != ChainFamily.EVM.value or network.family is not ChainFamily.EVM:
        errors.append("chain family mismatch")
    if network.enabled or network.capabilities.enabled():
        errors.append("production network is not fail-closed")
    if manifest.executable_capabilities:
        errors.append("manifest exposes executable capabilities")
    if manifest.account_type != "erc-4337-smart-account":
        errors.append("smart-account model mismatch")
    if manifest.owner_account_type != "evm-eoa":
        errors.append("owner-account model mismatch")
    if manifest.owner_relationship_field != "ownerAddresses":
        errors.append("owner relationship field mismatch")
    if manifest.required_owner_count != 1:
        errors.append("owner count mismatch")
    if manifest.exportable_account_type != "evm-eoa-owner":
        errors.append("export model mismatch")
    if manifest.smart_account_key_exportable:
        errors.append("smart-account key export must be disabled")
    if not manifest.fee_quote_required:
        errors.append("fee quote is not required")
    if manifest.paymaster_behavior != "optional-sponsorship-unbounded-user-paid":
        errors.append("paymaster policy mismatch")
    if manifest.bounded_user_paid_fee_supported:
        errors.append("unsupported bounded user-paid fee capability enabled")
    expected_permission_fields = {
        "network", "spender", "token", "allowance", "period", "start", "end",
        "salt", "extraData", "paymasterUrl",
    }
    if set(manifest.spend_permission_fields) != expected_permission_fields:
        errors.append("spend-permission fields mismatch")
    if set(manifest.operation_statuses) != {
        "pending", "signed", "broadcast", "complete", "dropped", "failed",
    }:
        errors.append("operation status vocabulary mismatch")
    if set(manifest.operation_identifiers) != {"userOpHash", "transactionHash"}:
        errors.append("operation identifiers mismatch")
    return tuple(errors)


def validate_base_mainnet_provider_manifest() -> tuple[str, ...]:
    return validate_evm_provider_manifest(BASE_MAINNET_PROVIDER_MANIFEST, BASE_MAINNET)
