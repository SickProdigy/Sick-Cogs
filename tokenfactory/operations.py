import re
from hashlib import sha256
from importlib import import_module
from typing import Any

from .constants import NETWORK_KEY
from .models import TokenDraft
from .validation import normalize_owner_address


TOKEN_FACTORY_SINGLETON = "0xce0042b868300000d44a59004da54a005ffdcf9f"
TOKEN_FACTORY_ADDRESS = "0xcba30318008035bb5a855a8684cea954d573c2c3"
TOKEN_FACTORY_DEPLOYED_TOPIC = (
    "0x8fdcf262da18a046c6f85d4fd10e822a07e071a567fa391c6b3d1fe6d91a1c5f"
)
TOKEN_FACTORY_CREATION_SHA256 = "8f8f4cd23e799be527a98bc723aa77695aa1addff5a805f7c0971bfb8251dd45"
TOKEN_FACTORY_RUNTIME_SHA256 = "d9cdd1effe5aac3b2bab44d78897fb27d4527f6ac964cdbc517e812da2bf20fb"
TOKEN_FACTORY_SINGLETON_SHA256 = "687bc888d213f8eff1e6a982da794f24b835191feb99dd2cacfcd33a9e58fdea"
TOKEN_FACTORY_DEPLOY_GAS_LIMIT = 2_000_000
TOKEN_DEPLOY_GAS_LIMIT = 1_500_000
TOKEN_CREATE_SELECTOR = "8b08cf96"
HASH_PATTERN = re.compile(r"^0x[0-9a-fA-F]{64}$")


class TokenFactoryOperationError(RuntimeError):
    """A TokenFactory operation could not be constructed or verified safely."""


class _BaseRpcFailure(RuntimeError):
    """A lazily imported CryptoWallet RPC request failed."""


def _base_rpc():
    try:
        return import_module("cryptowallet.providers.base_rpc")
    except ImportError as exc:
        raise TokenFactoryOperationError(
            "CryptoWallet RPC support is unavailable. Load CryptoWallet and try again."
        ) from exc


async def get_contract_code(address: str, network: str) -> str:
    rpc = _base_rpc()
    try:
        return await rpc.get_contract_code(address, network)
    except rpc.BaseRpcError as exc:
        raise _BaseRpcFailure from exc


async def get_erc20_asset(
    contract: str, owner: str, network: str, *, include_metadata: bool = False
) -> dict:
    rpc = _base_rpc()
    try:
        return await rpc.get_erc20_asset(
            contract, owner, network, include_metadata=include_metadata
        )
    except rpc.BaseRpcError as exc:
        raise _BaseRpcFailure from exc


async def get_factory_token_deployment(
    factory: str, request_id: str, network: str
) -> dict:
    rpc = _base_rpc()
    try:
        return await rpc.get_factory_token_deployment(factory, request_id, network)
    except rpc.BaseRpcError as exc:
        raise _BaseRpcFailure from exc


async def get_transaction(transaction_hash: str, network: str) -> dict | None:
    rpc = _base_rpc()
    try:
        return await rpc.get_transaction(transaction_hash, network)
    except rpc.BaseRpcError as exc:
        raise _BaseRpcFailure from exc


def _abi_dynamic_text(value: str) -> str:
    raw = value.encode("utf-8")
    return format(len(raw), "064x") + raw.hex().ljust(((len(raw) + 31) // 32) * 64, "0")


def fixed_supply_token_data(draft: TokenDraft, request_id: str, recipient: str) -> str:
    """Build the authoritative fixed-supply factory call owned by TokenFactory."""

    name_tail = _abi_dynamic_text(draft.name)
    symbol_tail = _abi_dynamic_text(draft.symbol)
    address = normalize_owner_address(recipient)
    if not draft.name or len(draft.name.encode("utf-8")) > 64:
        raise ValueError("Invalid token name")
    if not draft.symbol or len(draft.symbol.encode("utf-8")) > 10:
        raise ValueError("Invalid token symbol")
    if (
        draft.decimals < 0
        or draft.decimals > 18
        or draft.supply_atomic <= 0
        or draft.supply_atomic >= 2**256
    ):
        raise ValueError("Invalid fixed-supply token parameters")
    if not HASH_PATTERN.fullmatch(request_id or "") or int(request_id, 16) == 0:
        raise ValueError("Invalid token deployment request ID")
    name_offset = 6 * 32
    symbol_offset = name_offset + len(name_tail) // 2
    return (
        "0x"
        + TOKEN_CREATE_SELECTOR
        + format(name_offset, "064x")
        + format(symbol_offset, "064x")
        + format(draft.decimals, "064x")
        + format(draft.supply_atomic, "064x")
        + address[2:].lower().rjust(64, "0")
        + request_id[2:].lower()
        + name_tail
        + symbol_tail
    )


def singleton_deploy_data(creation_code: str) -> str:
    """Build the authoritative pinned EIP-2470 factory deployment call."""

    if (
        not isinstance(creation_code, str)
        or not creation_code.startswith("0x")
        or len(creation_code) % 2
    ):
        raise ValueError("Invalid EVM bytecode")
    if sha256(bytes.fromhex(creation_code[2:])).hexdigest() != TOKEN_FACTORY_CREATION_SHA256:
        raise ValueError("Unrecognized TokenFactory creation bytecode")
    raw = creation_code[2:].lower()
    length = len(raw) // 2
    padded = raw.ljust(((length + 31) // 32) * 64, "0")
    return "0x4af63f02" + format(64, "064x") + "0" * 64 + format(length, "064x") + padded


def token_operation(draft: TokenDraft, request_id: str, recipient: str) -> dict[str, Any]:
    return {
        "kind": "fixed_supply_token",
        "network": NETWORK_KEY,
        "to": TOKEN_FACTORY_ADDRESS,
        "value_wei": 0,
        "data": fixed_supply_token_data(draft, request_id, recipient),
        "gas_limit": TOKEN_DEPLOY_GAS_LIMIT,
        "recipient": normalize_owner_address(recipient),
        "request_id": request_id.lower(),
    }


def factory_operation(creation_code: str) -> dict[str, Any]:
    return {
        "kind": "factory",
        "network": NETWORK_KEY,
        "to": TOKEN_FACTORY_SINGLETON,
        "value_wei": 0,
        "data": singleton_deploy_data(creation_code),
        "gas_limit": TOKEN_FACTORY_DEPLOY_GAS_LIMIT,
    }


async def factory_deployment_status() -> dict[str, Any]:
    """Verify the pinned singleton and deterministic factory destination."""

    try:
        singleton_code = await get_contract_code(
            TOKEN_FACTORY_SINGLETON, NETWORK_KEY
        )
        factory_code = await get_contract_code(
            TOKEN_FACTORY_ADDRESS, NETWORK_KEY
        )
        singleton_hash = sha256(bytes.fromhex(singleton_code[2:])).hexdigest()
        if singleton_hash != TOKEN_FACTORY_SINGLETON_SHA256:
            raise TokenFactoryOperationError(
                "The Base Sepolia singleton deployer code does not match its pin."
            )
        if factory_code == "0x":
            return {"deployed": False, "address": TOKEN_FACTORY_ADDRESS}
        factory_hash = sha256(bytes.fromhex(factory_code[2:])).hexdigest()
        if factory_hash != TOKEN_FACTORY_RUNTIME_SHA256:
            raise TokenFactoryOperationError(
                "Unexpected code exists at the deterministic TokenFactory address."
            )
        return {"deployed": True, "address": TOKEN_FACTORY_ADDRESS}
    except (_BaseRpcFailure, ValueError) as exc:
        raise TokenFactoryOperationError(
            "Base Sepolia could not verify the TokenFactory deployment state."
        ) from exc


async def verify_fixed_supply_token(
    draft: TokenDraft, request_id: str, recipient: str
) -> dict[str, Any]:
    """Verify a factory record and the token's immutable public properties."""

    try:
        recipient = normalize_owner_address(recipient)
        deployment = await get_factory_token_deployment(
            TOKEN_FACTORY_ADDRESS, request_id, NETWORK_KEY
        )
        token = deployment["token_address"]
        if not token:
            return {"deployed": False}
        asset = await get_erc20_asset(
            token, recipient, NETWORK_KEY, include_metadata=True
        )
        if (
            asset["name"] != draft.name
            or asset["symbol"] != draft.symbol
            or asset["decimals"] != draft.decimals
            or asset["amount_atomic"] != draft.supply_atomic
            or asset["total_supply_atomic"] != draft.supply_atomic
        ):
            raise TokenFactoryOperationError(
                "The deployed token does not match its immutable draft."
            )
        return {
            "deployed": True,
            "token_address": token,
            "parameters_hash": deployment["parameters_hash"],
            **asset,
        }
    except _BaseRpcFailure as exc:
        raise TokenFactoryOperationError(
            "Base Sepolia could not verify the token deployment."
        ) from exc


async def verify_external_transaction(
    draft: TokenDraft, request_id: str, recipient: str, transaction_hash: str
) -> dict[str, Any]:
    """Verify an external transaction against TokenFactory's authoritative call."""

    if not HASH_PATTERN.fullmatch(transaction_hash):
        raise TokenFactoryOperationError("The external transaction hash is invalid.")
    try:
        recipient = normalize_owner_address(recipient)
        expected_data = fixed_supply_token_data(draft, request_id, recipient)
        transaction = await get_transaction(transaction_hash, NETWORK_KEY)
        if transaction is None or transaction.get("success") is None:
            return {"deployed": False, "provider_status": "pending"}
        if transaction.get("success") is not True:
            raise TokenFactoryOperationError(
                "The external deployment transaction failed on-chain."
            )
        transaction_to = normalize_owner_address(
            str(transaction.get("to_address") or "")
        )
        transaction_input = str(transaction.get("input_data") or "").lower()
        direct_call = (
            transaction_to == TOKEN_FACTORY_ADDRESS
            and transaction_input == expected_data.lower()
        )
        factory_log = any(
            item.get("address") == TOKEN_FACTORY_ADDRESS
            and item.get("topics")
            and item["topics"][0] == TOKEN_FACTORY_DEPLOYED_TOPIC
            for item in transaction.get("receipt_logs") or []
        )
        wrapped_call = (
            TOKEN_FACTORY_ADDRESS.removeprefix("0x") in transaction_input
            and expected_data.removeprefix("0x").lower() in transaction_input
            and factory_log
        )
        if int(transaction.get("value_wei", -1)) != 0 or not (
            direct_call or wrapped_call
        ):
            raise TokenFactoryOperationError(
                "The external transaction does not match the reviewed token draft."
            )
    except _BaseRpcFailure as exc:
        raise TokenFactoryOperationError(
            "Base Sepolia could not verify the external deployment transaction."
        ) from exc
    verified = await verify_fixed_supply_token(draft, request_id, recipient)
    return {
        **verified,
        "provider_status": "complete" if verified.get("deployed") else "pending",
        "transaction_hash": transaction_hash.lower(),
        "signer_address": normalize_owner_address(transaction["from_address"]),
    }
