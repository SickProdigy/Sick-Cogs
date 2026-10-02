"""Current official Deposit Wallet order signing with a local CLOB session key."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from eth_hash.auto import keccak

from .account_connection import normalize_evm_address
from .production_manifest import POLYMARKET_PRODUCTION_MANIFEST
from .signer_proof import CURVE_N, HALF_CURVE_N, recover_signer_address
from .session_authorization import session_address_from_private_key

ZERO_BYTES32 = "0x" + "0" * 64
SIGNATURE_TYPE = 3
SESSION_SIGNER_MAGIC = bytes.fromhex("6492" * 16)
ORDER_TYPE = (
    "Order(uint256 salt,address maker,address signer,uint256 tokenId,"
    "uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,"
    "uint256 timestamp,bytes32 metadata,bytes32 builder)"
)
DOMAIN_TYPE = (
    "EIP712Domain(string name,string version,uint256 chainId,"
    "address verifyingContract)"
)
TYPED_SIGN_TYPE = (
    "TypedDataSign(Order contents,string name,string version,uint256 chainId,"
    "address verifyingContract,bytes32 salt)" + ORDER_TYPE
)
HEX32 = re.compile(r"^0x[0-9a-fA-F]{64}$")


class OrderSigningError(ValueError):
    """Raised when a current order or its session signature is invalid."""


def _uint(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise OrderSigningError(f"{field} is invalid")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise OrderSigningError(f"{field} is invalid") from exc
    if str(parsed) != str(value) or not 0 <= parsed < 2**256:
        raise OrderSigningError(f"{field} is invalid")
    return parsed


def _word(value: int) -> bytes:
    return value.to_bytes(32, "big")


def _address_word(value: str) -> bytes:
    return bytes(12) + bytes.fromhex(normalize_evm_address(value, "order address")[2:])


def _bytes32(value: str, field: str) -> bytes:
    if not isinstance(value, str) or HEX32.fullmatch(value) is None:
        raise OrderSigningError(f"{field} is invalid")
    return bytes.fromhex(value[2:])


@dataclass(frozen=True, slots=True)
class UnsignedDepositWalletOrder:
    exchange_address: str
    maker: str
    token_id: str
    maker_amount: int
    taker_amount: int
    salt: int
    timestamp: int
    side: str = "BUY"
    expiration: int = 0
    signature_type: int = SIGNATURE_TYPE
    metadata: str = ZERO_BYTES32
    builder: str = ZERO_BYTES32
    protocol_version: str = "2"
    chain_id: int = 137

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "exchange_address",
            normalize_evm_address(self.exchange_address, "exchange address"),
        )
        object.__setattr__(self, "maker", normalize_evm_address(self.maker, "maker"))
        if self.exchange_address not in {
            POLYMARKET_PRODUCTION_MANIFEST.ctf_exchange.lower(),
            POLYMARKET_PRODUCTION_MANIFEST.neg_risk_exchange.lower(),
            POLYMARKET_PRODUCTION_MANIFEST.exchange_v3.lower(),
        }:
            raise OrderSigningError("order exchange is not pinned")
        if not isinstance(self.token_id, str) or not self.token_id.isdigit():
            raise OrderSigningError("token ID is invalid")
        for field in ("maker_amount", "taker_amount", "salt", "timestamp", "expiration"):
            value = _uint(getattr(self, field), field)
            object.__setattr__(self, field, value)
        if self.maker_amount <= 0 or self.taker_amount <= 0:
            raise OrderSigningError("order amounts must be positive")
        if self.side not in {"BUY", "SELL"}:
            raise OrderSigningError("order side is invalid")
        if self.expiration != 0 or self.signature_type != SIGNATURE_TYPE:
            raise OrderSigningError("only current Deposit Wallet GTC orders are supported")
        if self.metadata != ZERO_BYTES32 or self.builder != ZERO_BYTES32:
            raise OrderSigningError("unreviewed metadata or builder attribution is blocked")
        if self.protocol_version not in {"2", "3"} or self.chain_id != 137:
            raise OrderSigningError("order protocol binding is invalid")

    @property
    def signer(self) -> str:
        return self.maker

    def provider_order(self, signature: str) -> dict[str, Any]:
        return {
            "builder": self.builder,
            "expiration": str(self.expiration),
            "maker": self.maker,
            "makerAmount": str(self.maker_amount),
            "metadata": self.metadata,
            "salt": self.salt,
            "side": self.side,
            "signature": signature,
            "signatureType": self.signature_type,
            "signer": self.signer,
            "takerAmount": str(self.taker_amount),
            "timestamp": str(self.timestamp),
            "tokenId": self.token_id,
        }


def _order_contents_hash(order: UnsignedDepositWalletOrder) -> bytes:
    return keccak(
        keccak(ORDER_TYPE.encode("ascii"))
        + _word(order.salt)
        + _address_word(order.maker)
        + _address_word(order.signer)
        + _word(int(order.token_id))
        + _word(order.maker_amount)
        + _word(order.taker_amount)
        + _word(0 if order.side == "BUY" else 1)
        + _word(order.signature_type)
        + _word(order.timestamp)
        + _bytes32(order.metadata, "metadata")
        + _bytes32(order.builder, "builder")
    )


def _domain_separator(order: UnsignedDepositWalletOrder) -> bytes:
    return keccak(
        keccak(DOMAIN_TYPE.encode("ascii"))
        + keccak(b"Polymarket CTF Exchange")
        + keccak(order.protocol_version.encode("ascii"))
        + _word(order.chain_id)
        + _address_word(order.exchange_address)
    )


def deposit_wallet_order_digest(order: UnsignedDepositWalletOrder) -> bytes:
    """Return the official ERC-7739 TypedDataSign digest for a Deposit Wallet."""

    contents = _order_contents_hash(order)
    envelope = keccak(
        keccak(TYPED_SIGN_TYPE.encode("ascii"))
        + contents
        + keccak(b"DepositWallet")
        + keccak(b"1")
        + _word(order.chain_id)
        + _address_word(order.maker)
        + bytes(32)
    )
    return keccak(b"\x19\x01" + _domain_separator(order) + envelope)


def _sign_digest(private_key: bytes, digest: bytes) -> tuple[str, str]:
    address = session_address_from_private_key(private_key)
    scalar = int.from_bytes(private_key, "big")
    der = ec.derive_private_key(scalar, ec.SECP256K1()).sign(
        digest, ec.ECDSA(utils.Prehashed(hashes.SHA256()))
    )
    r, s = utils.decode_dss_signature(der)
    if s > HALF_CURVE_N:
        s = CURVE_N - s
    for recovery_id in (0, 1):
        signature = "0x" + (
            r.to_bytes(32, "big") + s.to_bytes(32, "big")
            + bytes([27 + recovery_id])
        ).hex()
        try:
            if recover_signer_address(digest, signature) == address:
                return signature, address
        except ValueError:
            continue
    raise OrderSigningError("order signature could not be recovered")


def _erc7739_signature(order: UnsignedDepositWalletOrder, signature: str) -> bytes:
    return (
        bytes.fromhex(signature[2:]) + _domain_separator(order)
        + _order_contents_hash(order) + ORDER_TYPE.encode("ascii")
        + len(ORDER_TYPE).to_bytes(2, "big")
    )


def _wrap_session_signature(session_address: str, signature: bytes) -> str:
    signer_id = _address_word(session_address)
    padding = bytes((-len(signature)) % 32)
    payload = (
        signer_id + bytes(32) + _word(96) + _word(len(signature))
        + signature + padding
    )
    return "0x" + (payload + SESSION_SIGNER_MAGIC).hex()


def sign_deposit_wallet_order(
    private_key: bytes, order: UnsignedDepositWalletOrder,
) -> dict[str, Any]:
    """Sign, independently recover, and wrap one current Deposit Wallet order."""

    digest = deposit_wallet_order_digest(order)
    signature, session_address = _sign_digest(private_key, digest)
    wrapped = _wrap_session_signature(
        session_address, _erc7739_signature(order, signature)
    )
    return order.provider_order(wrapped)


def verify_deposit_wallet_order_signature(
    order: UnsignedDepositWalletOrder, signature: str, *, expected_session_address: str,
) -> None:
    expected = normalize_evm_address(expected_session_address, "session signer")
    if not isinstance(signature, str) or not signature.startswith("0x"):
        raise OrderSigningError("wrapped order signature is invalid")
    try:
        raw = bytes.fromhex(signature[2:])
    except ValueError as exc:
        raise OrderSigningError("wrapped order signature is invalid") from exc
    if len(raw) < 32 or raw[-32:] != SESSION_SIGNER_MAGIC:
        raise OrderSigningError("wrapped order signature magic is invalid")
    abi = raw[:-32]
    if len(abi) < 128 or abi[:32] != _address_word(expected) or abi[32:64] != bytes(32):
        raise OrderSigningError("wrapped order signer is invalid")
    if int.from_bytes(abi[64:96], "big") != 96:
        raise OrderSigningError("wrapped order signature offset is invalid")
    length = int.from_bytes(abi[96:128], "big")
    if 128 + ((length + 31) // 32) * 32 != len(abi):
        raise OrderSigningError("wrapped order signature length is invalid")
    owner_signature = abi[128:128 + length]
    trailer = (
        _domain_separator(order) + _order_contents_hash(order)
        + ORDER_TYPE.encode("ascii") + len(ORDER_TYPE).to_bytes(2, "big")
    )
    if len(owner_signature) != 65 + len(trailer) or owner_signature[65:] != trailer:
        raise OrderSigningError("ERC-7739 order signature trailer is invalid")
    base = "0x" + owner_signature[:65].hex()
    if recover_signer_address(deposit_wallet_order_digest(order), base) != expected:
        raise OrderSigningError("order signature does not match the active session")
