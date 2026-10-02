"""Restart-safe CryptoWallet-to-Polymarket Bridge deposit lifecycle."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import re
from typing import Mapping, Sequence

from .account_connection import AccountConnectionError, normalize_evm_address
from .bridge import BridgeAsset, BridgeQuote, BridgeTransaction, NATIVE_EVM_TOKEN
from .production_manifest import POLYMARKET_PRODUCTION_MANIFEST

IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
HEX_HASH = re.compile(r"^0x[0-9a-f]{64}$")


class DepositState(str, Enum):
    AWAITING_WALLET_APPROVAL = "awaiting_wallet_approval"
    WALLET_PROCESSING = "wallet_processing"
    WALLET_UNCERTAIN = "wallet_uncertain"
    BRIDGE_PENDING = "bridge_pending"
    BRIDGING = "bridging"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class BridgeDeposit:
    deposit_id: str
    discord_user_id: int
    profile_id: str
    account_wallet_address: str
    bridge_address: str
    source_network: str
    source_chain_id: int
    source_token_address: str
    source_symbol: str
    source_decimals: int
    source_amount_atomic: int
    minimum_usd: str
    quote_id: str
    quoted_input_usd: str
    quoted_output_usd: str
    quoted_pusd_atomic: int
    minimum_received_usd: str
    wallet_intent_id: str
    wallet_intent_fingerprint: str
    created_at: int
    expires_at: int
    state: DepositState = DepositState.AWAITING_WALLET_APPROVAL
    wallet_transaction_hash: str | None = None
    bridge_status: str | None = None
    bridge_transaction_hash: str | None = None
    failure_digest: str | None = None

    def __post_init__(self) -> None:
        if not IDENTIFIER.fullmatch(self.deposit_id):
            raise AccountConnectionError("Bridge deposit identity is invalid.")
        if self.discord_user_id <= 0 or not IDENTIFIER.fullmatch(self.profile_id):
            raise AccountConnectionError("Bridge deposit user binding is invalid.")
        object.__setattr__(self, "account_wallet_address", normalize_evm_address(
            self.account_wallet_address, "Polymarket account wallet"
        ))
        object.__setattr__(self, "bridge_address", normalize_evm_address(
            self.bridge_address, "Bridge deposit address"
        ))
        if (self.source_network != "base-mainnet" or self.source_chain_id != 8453
                or self.source_token_address != NATIVE_EVM_TOKEN
                or self.source_symbol != "ETH" or self.source_decimals != 18
                or self.source_amount_atomic <= 0):
            raise AccountConnectionError("Bridge source asset binding is invalid.")
        for value, label in (
            (self.minimum_usd, "minimum"),
            (self.quoted_input_usd, "quoted input"),
            (self.quoted_output_usd, "quoted output"),
            (self.minimum_received_usd, "minimum received"),
        ):
            try:
                parsed = Decimal(value)
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise AccountConnectionError(f"Bridge deposit {label} is invalid.") from exc
            if not parsed.is_finite() or parsed < 0:
                raise AccountConnectionError(f"Bridge deposit {label} is invalid.")
        if (Decimal(self.quoted_input_usd) < Decimal(self.minimum_usd)
                or Decimal(self.quoted_output_usd) <= 0
                or Decimal(self.quoted_output_usd) > Decimal(self.quoted_input_usd)
                or Decimal(self.minimum_received_usd) > Decimal(self.quoted_output_usd)
                or self.quoted_pusd_atomic <= 0):
            raise AccountConnectionError("Bridge deposit quote is below its protected bounds.")
        if not HEX_HASH.fullmatch(self.quote_id):
            raise AccountConnectionError("Bridge deposit quote identity is invalid.")
        if (not IDENTIFIER.fullmatch(self.wallet_intent_id)
                or not FINGERPRINT.fullmatch(self.wallet_intent_fingerprint)):
            raise AccountConnectionError("CryptoWallet intent binding is invalid.")
        if (type(self.created_at) is not int or type(self.expires_at) is not int
                or self.created_at <= 0 or self.expires_at <= self.created_at):
            raise AccountConnectionError("Bridge deposit lifetime is invalid.")
        if not isinstance(self.state, DepositState):
            raise AccountConnectionError("Bridge deposit state is invalid.")
        for value in (self.wallet_transaction_hash, self.bridge_transaction_hash):
            if value is not None and HEX_HASH.fullmatch(value) is None:
                raise AccountConnectionError("Bridge deposit transaction hash is invalid.")
        if self.state is DepositState.FAILED and not FINGERPRINT.fullmatch(
            self.failure_digest or ""
        ):
            raise AccountConnectionError("Bridge deposit failure evidence is missing.")
        if self.state is DepositState.COMPLETED and self.bridge_status != "COMPLETED":
            raise AccountConnectionError("Completed Bridge deposit lacks provider evidence.")

    @classmethod
    def create(
        cls, *, deposit_id: str, discord_user_id: int, profile_id: str,
        account_wallet_address: str, bridge_address: str, asset: BridgeAsset,
        amount_atomic: int, quote: BridgeQuote, wallet_intent_id: str,
        wallet_intent_fingerprint: str, created_at: int, expires_at: int,
    ) -> "BridgeDeposit":
        return cls(
            deposit_id=deposit_id, discord_user_id=discord_user_id,
            profile_id=profile_id, account_wallet_address=account_wallet_address,
            bridge_address=bridge_address, source_network="base-mainnet",
            source_chain_id=asset.chain_id, source_token_address=asset.token_address,
            source_symbol=asset.symbol, source_decimals=asset.decimals,
            source_amount_atomic=amount_atomic, minimum_usd=str(asset.minimum_usd),
            quote_id=quote.quote_id, quoted_input_usd=str(quote.input_usd),
            quoted_output_usd=str(quote.output_usd),
            quoted_pusd_atomic=quote.output_atomic,
            minimum_received_usd=str(quote.minimum_received_usd),
            wallet_intent_id=wallet_intent_id,
            wallet_intent_fingerprint=wallet_intent_fingerprint,
            created_at=created_at, expires_at=expires_at,
        )

    @property
    def fingerprint(self) -> str:
        values = self.to_record()
        for key in (
            "state", "wallet_transaction_hash", "bridge_status",
            "bridge_transaction_hash", "failure_digest",
        ):
            values.pop(key)
        return hashlib.sha256(json.dumps(
            values, sort_keys=True, separators=(",", ":")
        ).encode("ascii")).hexdigest()

    def reconcile_wallet_intent(self, intent: Mapping, *, now: int) -> "BridgeDeposit":
        try:
            fingerprint = str(intent["approval_fingerprint"])
            status = str(intent["status"])
        except (KeyError, TypeError) as exc:
            raise AccountConnectionError("CryptoWallet intent evidence is incomplete.") from exc
        if any((
            str(intent.get("intent_id")) != self.wallet_intent_id,
            str(intent.get("profile_id")) != self.profile_id,
            str(intent.get("network")) != self.source_network,
            normalize_evm_address(intent.get("to_address"), "Bridge recipient")
                != self.bridge_address,
            int(intent.get("value_atomic", -1)) != self.source_amount_atomic,
            str(intent.get("asset_kind")) != "native",
            str(intent.get("asset_symbol")) != self.source_symbol,
            int(intent.get("asset_decimals", -1)) != self.source_decimals,
            fingerprint != self.wallet_intent_fingerprint,
        )):
            raise AccountConnectionError("CryptoWallet deposit intent identity changed.")
        tx_hash = intent.get("transaction_hash")
        if tx_hash is not None:
            tx_hash = str(tx_hash).lower()
            if HEX_HASH.fullmatch(tx_hash) is None:
                raise AccountConnectionError("CryptoWallet transaction hash is invalid.")
        if status == "rejected":
            return replace(self, state=DepositState.REJECTED)
        if status == "expired":
            return replace(self, state=DepositState.EXPIRED)
        if status == "failed":
            digest = hashlib.sha256(b"cryptowallet-transfer-failed").hexdigest()
            return replace(self, state=DepositState.FAILED, failure_digest=digest)
        if status == "uncertain":
            return replace(self, state=DepositState.WALLET_UNCERTAIN,
                           wallet_transaction_hash=tx_hash)
        if status in {"processing", "approved", "submitted"}:
            return replace(self, state=DepositState.WALLET_PROCESSING,
                           wallet_transaction_hash=tx_hash)
        if status == "confirmed":
            if tx_hash is None:
                raise AccountConnectionError("Confirmed CryptoWallet transfer lacks a hash.")
            return replace(self, state=DepositState.BRIDGE_PENDING,
                           wallet_transaction_hash=tx_hash)
        if status != "pending":
            raise AccountConnectionError("CryptoWallet deposit intent state is invalid.")
        if now >= self.expires_at:
            return replace(self, state=DepositState.EXPIRED)
        return self

    def reconcile_bridge(
        self, transactions: Sequence[BridgeTransaction], *, now_ms: int,
    ) -> "BridgeDeposit":
        if self.state not in {
            DepositState.BRIDGE_PENDING, DepositState.BRIDGING,
            DepositState.WALLET_UNCERTAIN,
        }:
            raise AccountConnectionError("Bridge evidence is not expected yet.")
        earliest = self.created_at * 1000 - 60_000
        matches = [item for item in transactions if (
            item.source_chain_id == self.source_chain_id
            and item.source_token_address == self.source_token_address
            and item.source_amount_atomic == self.source_amount_atomic
            and item.destination_chain_id == POLYMARKET_PRODUCTION_MANIFEST.chain_id
            and item.destination_token_address
                == POLYMARKET_PRODUCTION_MANIFEST.collateral_token.lower()
            and (item.created_time_ms is None or item.created_time_ms >= earliest)
        )]
        if not matches:
            return replace(self, state=DepositState.BRIDGE_PENDING)
        if len(matches) != 1:
            raise AccountConnectionError("Bridge deposit evidence is ambiguous.")
        item = matches[0]
        if item.created_time_ms is not None and item.created_time_ms > now_ms + 60_000:
            raise AccountConnectionError("Bridge deposit timestamp is in the future.")
        if item.status == "FAILED":
            digest = hashlib.sha256(b"bridge-deposit-failed").hexdigest()
            return replace(
                self, state=DepositState.FAILED, bridge_status=item.status,
                bridge_transaction_hash=item.transaction_hash, failure_digest=digest,
            )
        if item.status == "COMPLETED":
            if item.transaction_hash is None:
                raise AccountConnectionError("Completed Bridge deposit lacks a hash.")
            return replace(
                self, state=DepositState.COMPLETED, bridge_status=item.status,
                bridge_transaction_hash=item.transaction_hash,
            )
        return replace(
            self, state=DepositState.BRIDGING, bridge_status=item.status,
            bridge_transaction_hash=item.transaction_hash,
        )

    def to_record(self) -> dict:
        values = asdict(self)
        values["state"] = self.state.value
        return values

    @classmethod
    def from_record(cls, record: Mapping) -> "BridgeDeposit":
        try:
            values = dict(record)
            values["state"] = DepositState(values["state"])
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountConnectionError("Stored Bridge deposit is invalid.") from exc
