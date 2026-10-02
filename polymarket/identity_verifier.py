"""Independent read-only verification of official Polymarket account identities."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from eth_hash.auto import keccak

from .account_connection import AccountConnectionError, WalletType, normalize_evm_address
from .production_manifest import (
    POLYMARKET_PRODUCTION_MANIFEST,
    PolymarketProductionManifest,
    validate_polymarket_production_manifest,
)


RpcCall = Callable[[str, list[Any]], Awaitable[Any]]
HEX_32 = re.compile(r"^0x[0-9a-fA-F]{64}$")
HEX_DATA = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")

PROXY_BYTECODE_TEMPLATE = (
    "3d3d606380380380913d393d73%s5af4602a57600080fd5b602d8060366000396000f3"
    "363d3d373d3d3d363d73%s5af43d82803e903d91602b57fd5bf352e831dd0000000000"
    "0000000000000000000000000000000000000000000000000000200000000000000000"
    "000000000000000000000000000000000000000000000000"
)
ERC1967_CONST1 = "cc3735a920a3ca505d382bbc545af43d6000803e6038573d6000fd5b3d6000f3"
ERC1967_CONST2 = "5155f3363d3d373d3d363d7f360894a13ba1a3210667c828492db98dca3e2076"
ERC1967_PREFIX = 0x61003D3D8160233D3973
ERC1967_BEACON_CONST1 = "b3582b35133d50545afa5036515af43d6000803e604d573d6000fd5b3d6000f3"
ERC1967_BEACON_CONST2 = "1b60e01b36527fa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6c"
ERC1967_BEACON_CONST3 = "60195155f3363d3d373d3d363d602036600436635c60da"
ERC1967_BEACON_PREFIX = 0x6100523D8160233D3973


@dataclass(frozen=True, slots=True)
class AccountRelationshipEvidence:
    """Public verification evidence; it contains no signer or API credential."""

    signer_address: str
    account_wallet_address: str
    wallet_type: WalletType
    block_number: int
    code_hash: str
    evidence_digest: str
    source: str = "polygon_contract_read"
    chain_id: int = 137

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "signer_address",
            normalize_evm_address(self.signer_address, "signer_address"),
        )
        object.__setattr__(
            self, "account_wallet_address",
            normalize_evm_address(self.account_wallet_address, "account_wallet_address"),
        )
        if not isinstance(self.wallet_type, WalletType):
            raise AccountConnectionError("A recognized wallet type is required.")
        if self.block_number < 0 or not HEX_32.fullmatch(self.code_hash):
            raise AccountConnectionError("Polygon account evidence is invalid.")
        if not re.fullmatch(r"[0-9a-f]{64}", self.evidence_digest):
            raise AccountConnectionError("Account evidence digest is invalid.")
        if self.source != "polygon_contract_read" or self.chain_id != 137:
            raise AccountConnectionError("Account evidence source is invalid.")


class PolygonAccountIdentityVerifier:
    """Derive the official account wallet and confirm smart-wallet deployment."""

    def __init__(
        self,
        rpc: RpcCall,
        manifest: PolymarketProductionManifest = POLYMARKET_PRODUCTION_MANIFEST,
    ) -> None:
        if validate_polymarket_production_manifest(manifest):
            raise AccountConnectionError("Polymarket production manifest has drifted.")
        self.rpc = rpc
        self.manifest = manifest

    async def _keccak(self, value: bytes) -> bytes:
        return keccak(value)

    @staticmethod
    def _address_bytes(address: str) -> bytes:
        return bytes.fromhex(normalize_evm_address(address, "address")[2:])

    @classmethod
    def _address_word(cls, address: str) -> bytes:
        return bytes(12) + cls._address_bytes(address)

    async def _create2(self, factory: str, salt: bytes, init_code_hash: bytes) -> str:
        if len(salt) != 32 or len(init_code_hash) != 32:
            raise AccountConnectionError("CREATE2 derivation input is invalid.")
        digest = await self._keccak(
            b"\xff" + self._address_bytes(factory) + salt + init_code_hash
        )
        return "0x" + digest[-20:].hex()

    async def derive_wallets(self, signer_address: str) -> dict[WalletType, tuple[str, ...]]:
        signer = normalize_evm_address(signer_address, "signer_address")
        manifest = self.manifest
        signer_word = self._address_word(signer)

        proxy_bytecode = bytes.fromhex(
            PROXY_BYTECODE_TEMPLATE
            .replace("%s", manifest.proxy_wallet_factory[2:].lower(), 1)
            .replace("%s", manifest.proxy_wallet_implementation[2:].lower(), 1)
        )
        proxy_hash = await self._keccak(proxy_bytecode)
        proxy_salt = await self._keccak(self._address_bytes(signer))
        proxy_wallet = await self._create2(
            manifest.proxy_wallet_factory, proxy_salt, proxy_hash
        )

        safe_salt = await self._keccak(signer_word)
        safe_wallet = await self._create2(
            manifest.safe_wallet_factory,
            safe_salt,
            bytes.fromhex(manifest.safe_init_code_hash[2:]),
        )

        args = self._address_word(manifest.deposit_wallet_factory) + signer_word
        deposit_salt = await self._keccak(args)
        args_length = len(args)

        uups_prefix = (ERC1967_PREFIX + (args_length << 56)).to_bytes(10, "big")
        uups_init = (
            uups_prefix
            + self._address_bytes(manifest.deposit_wallet_implementation)
            + bytes.fromhex("6009" + ERC1967_CONST2 + ERC1967_CONST1)
            + args
        )
        uups_wallet = await self._create2(
            manifest.deposit_wallet_factory,
            deposit_salt,
            await self._keccak(uups_init),
        )

        beacon_prefix = (
            ERC1967_BEACON_PREFIX + (args_length << 56)
        ).to_bytes(10, "big")
        beacon_init = (
            beacon_prefix
            + self._address_bytes(manifest.deposit_wallet_beacon)
            + bytes.fromhex(
                ERC1967_BEACON_CONST3
                + ERC1967_BEACON_CONST2
                + ERC1967_BEACON_CONST1
            )
            + args
        )
        beacon_wallet = await self._create2(
            manifest.deposit_wallet_factory,
            deposit_salt,
            await self._keccak(beacon_init),
        )
        return {
            WalletType.EOA: (signer,),
            WalletType.POLY_PROXY: (proxy_wallet,),
            WalletType.GNOSIS_SAFE: (safe_wallet,),
            WalletType.DEPOSIT_WALLET: (uups_wallet, beacon_wallet),
        }

    async def verify(
        self,
        *,
        signer_address: str,
        account_wallet_address: str,
        wallet_type: WalletType,
    ) -> AccountRelationshipEvidence:
        if not isinstance(wallet_type, WalletType):
            raise AccountConnectionError("A recognized wallet type is required.")
        signer = normalize_evm_address(signer_address, "signer_address")
        wallet = normalize_evm_address(account_wallet_address, "account_wallet_address")
        derived = await self.derive_wallets(signer)
        if wallet not in derived[wallet_type]:
            raise AccountConnectionError(
                "Account wallet does not match the official signer derivation."
            )
        chain_id_raw = await self.rpc("eth_chainId", [])
        block_raw = await self.rpc("eth_blockNumber", [])
        code = await self.rpc("eth_getCode", [wallet, "latest"])
        try:
            chain_id = int(str(chain_id_raw), 16)
            block_number = int(str(block_raw), 16)
        except (TypeError, ValueError) as exc:
            raise AccountConnectionError("Polygon RPC returned invalid chain metadata.") from exc
        if chain_id != self.manifest.chain_id:
            raise AccountConnectionError("Polygon RPC chain ID does not match.")
        if not isinstance(code, str) or not HEX_DATA.fullmatch(code):
            raise AccountConnectionError("Polygon RPC returned invalid wallet bytecode.")
        deployed = code not in {"0x", "0x00"}
        if wallet_type is WalletType.EOA and deployed:
            raise AccountConnectionError("EOA account unexpectedly contains contract bytecode.")
        if wallet_type is not WalletType.EOA and not deployed:
            raise AccountConnectionError("Polymarket smart wallet is not deployed.")
        code_hash = "0x" + (await self._keccak(bytes.fromhex(code[2:]))).hex()
        evidence = {
            "account_wallet_address": wallet,
            "block_number": block_number,
            "chain_id": chain_id,
            "code_hash": code_hash,
            "signer_address": signer,
            "source": "polygon_contract_read",
            "wallet_type": wallet_type.value,
        }
        digest = hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return AccountRelationshipEvidence(
            signer_address=signer,
            account_wallet_address=wallet,
            wallet_type=wallet_type,
            block_number=block_number,
            code_hash=code_hash,
            evidence_digest=digest,
        )
