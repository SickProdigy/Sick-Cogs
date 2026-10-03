"""Exact current-SDK settlement calls and immutable approval model."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping

from eth_hash.auto import keccak

from .account_connection import AccountConnectionError, normalize_evm_address
from .order_protocol import is_protocol_v3_position_id
from .production_manifest import POLYMARKET_PRODUCTION_MANIFEST
from .security_policy import EligibilityAttestation
from .signer_proof import recover_signer_address
from .trade_confirmation import TradeConfirmation, TradeConfirmationError

_UINT256_MAX = (1 << 256) - 1
_HEX32 = re.compile(r"^0x[0-9a-fA-F]{64}$")
_HEX31 = re.compile(r"^0x[0-9a-fA-F]{62}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_BATCH_LIFETIME_SECONDS = 10 * 60


class SettlementState(str, Enum):
    APPROVED = "approved"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"


_BATCH_TYPES = {
    "Call": [
        {"name": "target", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "data", "type": "bytes"},
    ],
    "Batch": [
        {"name": "wallet", "type": "address"},
        {"name": "nonce", "type": "uint256"},
        {"name": "deadline", "type": "uint256"},
        {"name": "calls", "type": "Call[]"},
    ],
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
}


def _uint(value: int, label: str) -> bytes:
    if not isinstance(value, int) or not 0 <= value <= _UINT256_MAX:
        raise AccountConnectionError(f"{label} is outside uint256.")
    return value.to_bytes(32, "big")


def _condition(value: str) -> bytes:
    if not isinstance(value, str) or _HEX32.fullmatch(value) is None:
        raise AccountConnectionError("Settlement condition ID is invalid.")
    return bytes.fromhex(value[2:])


def _string_list(value: Any, label: str) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AccountConnectionError(f"{label} is invalid.") from exc
    if (
        not isinstance(value, list) or len(value) != 2
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise AccountConnectionError(f"{label} is invalid.")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class ResolvedMarket:
    market_id: str
    condition_id: str
    question: str
    outcomes: tuple[str, str]
    token_ids: tuple[str, str]
    negative_risk: bool

    @classmethod
    def from_market(cls, market: Mapping[str, Any]) -> "ResolvedMarket":
        if not isinstance(market, Mapping) or market.get("closed") is not True:
            raise AccountConnectionError("Settlement market is not closed.")
        market_id = str(market.get("id") or "").strip()
        condition_id = str(market.get("conditionId") or "").lower()
        question = str(market.get("question") or "").strip()
        if (
            not market_id or len(market_id) > 128
            or _HEX32.fullmatch(condition_id) is None
            or not question or len(question) > 512
            or not isinstance(market.get("negRisk"), bool)
        ):
            raise AccountConnectionError("Settlement market identity is invalid.")
        outcomes = _string_list(market.get("outcomes"), "Settlement outcomes")
        token_ids = _string_list(market.get("clobTokenIds"), "Settlement tokens")
        if len(set(item.casefold() for item in outcomes)) != 2:
            raise AccountConnectionError("Settlement outcomes are ambiguous.")
        if len(set(token_ids)) != 2 or not all(
            token.isdigit() and 0 < int(token) <= _UINT256_MAX for token in token_ids
        ):
            raise AccountConnectionError("Settlement token identity is invalid.")
        return cls(
            market_id, condition_id, question, outcomes, token_ids,
            market["negRisk"],
        )

    def select(self, outcome: str) -> tuple[str, str]:
        if not isinstance(outcome, str):
            raise AccountConnectionError("Settlement outcome is invalid.")
        matches = [
            index for index, label in enumerate(self.outcomes)
            if label.casefold() == outcome.casefold()
        ]
        if not matches and outcome.isdigit() and 1 <= int(outcome) <= 2:
            matches = [int(outcome) - 1]
        if len(matches) != 1:
            raise AccountConnectionError("Settlement outcome is not in this market.")
        index = matches[0]
        return self.outcomes[index], self.token_ids[index]


@dataclass(frozen=True, slots=True)
class PositionBalanceEvidence:
    wallet_address: str
    token_id: str
    amount_atomic: int
    block_number: int
    chain_id: int = 137
    source: str = "polygon_position_manager"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "wallet_address",
            normalize_evm_address(self.wallet_address, "Deposit Wallet"),
        )
        if (
            not is_protocol_v3_position_id(self.token_id)
            or not 0 <= self.amount_atomic <= _UINT256_MAX
            or self.block_number < 0 or self.chain_id != 137
            or self.source != "polygon_position_manager"
        ):
            raise AccountConnectionError("Position Manager balance evidence is invalid.")


async def read_v3_position_balance(
    rpc: Callable[[str, list[Any]], Awaitable[Any]], *,
    wallet_address: str, token_id: str,
) -> PositionBalanceEvidence:
    wallet = normalize_evm_address(wallet_address, "Deposit Wallet")
    if not is_protocol_v3_position_id(token_id):
        raise AccountConnectionError("Position Manager token is not protocol v3.")
    selector = keccak(b"balanceOf(address,uint256)")[:4]
    data = "0x" + (
        selector + bytes(12) + bytes.fromhex(wallet[2:])
        + _uint(int(token_id), "Position Manager token")
    ).hex()
    chain_raw = await rpc("eth_chainId", [])
    block_raw = await rpc("eth_blockNumber", [])
    try:
        chain_id = int(str(chain_raw), 16)
        block_number = int(str(block_raw), 16)
    except (TypeError, ValueError) as exc:
        raise AccountConnectionError("Position Manager chain response is invalid.") from exc
    if chain_id != 137:
        raise AccountConnectionError("Polygon RPC chain ID does not match.")
    result = await rpc("eth_call", [{
        "to": POLYMARKET_PRODUCTION_MANIFEST.position_manager.lower(),
        "data": data,
    }, hex(block_number)])
    try:
        if not isinstance(result, str) or re.fullmatch(r"0x[0-9a-fA-F]{64}", result) is None:
            raise ValueError
        amount = int(result, 16)
    except (TypeError, ValueError) as exc:
        raise AccountConnectionError("Position Manager balance response is invalid.") from exc
    return PositionBalanceEvidence(wallet, token_id, amount, block_number, chain_id)


def ctf_redeem_calldata(condition_id: str) -> str:
    """Match SDK redeemPositions(pUSD, zero parent, condition, [1, 2])."""
    selector = keccak(b"redeemPositions(address,bytes32,bytes32,uint256[])")[:4]
    collateral = bytes(12) + bytes.fromhex(
        POLYMARKET_PRODUCTION_MANIFEST.collateral_token[2:]
    )
    encoded = (
        selector + collateral + bytes(32) + _condition(condition_id)
        + _uint(128, "Settlement array offset")
        + _uint(2, "Settlement index count")
        + _uint(1, "Settlement YES index")
        + _uint(2, "Settlement NO index")
    )
    return "0x" + encoded.hex()


def decode_v3_position_id(position_id: str) -> tuple[str, int]:
    if not is_protocol_v3_position_id(position_id):
        raise AccountConnectionError("Settlement position is not protocol v3.")
    raw = int(position_id).to_bytes(32, "big")
    if raw[0] not in {1, 2, 3} or raw[-1] not in {0, 1}:
        raise AccountConnectionError("Protocol-v3 settlement position is invalid.")
    return "0x" + raw[:-1].hex(), raw[-1]


def router_redeem_calldata(position_id: str, amount_atomic: int) -> str:
    """Match SDK protocol-v2 Router redeem(bytes31,uint256,uint256)."""
    condition_id, outcome_index = decode_v3_position_id(position_id)
    condition_word = bytes.fromhex(condition_id[2:]) + bytes(1)
    selector = keccak(b"redeem(bytes31,uint256,uint256)")[:4]
    return "0x" + (
        selector + condition_word + _uint(outcome_index, "Settlement outcome")
        + _uint(amount_atomic, "Settlement amount")
    ).hex()


@dataclass(frozen=True, slots=True)
class SettlementCall:
    target: str
    data: str
    value: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", normalize_evm_address(self.target, "target"))
        if (
            not isinstance(self.data, str) or not self.data.startswith("0x")
            or len(self.data) < 10 or len(self.data) % 2
            or any(c not in "0123456789abcdefABCDEF" for c in self.data[2:])
            or self.value != 0
        ):
            raise AccountConnectionError("Settlement call is invalid.")
        object.__setattr__(self, "data", self.data.lower())

    def to_record(self) -> dict[str, Any]:
        return {"target": self.target, "value": str(self.value), "data": self.data}


@dataclass(frozen=True, slots=True)
class SettlementPlan:
    settlement_id: str
    discord_user_id: int
    profile_id: str
    owner_address: str
    wallet_address: str
    market_id: str
    condition_id: str
    token_id: str
    outcome: str
    amount_atomic: int
    protocol: str
    negative_risk: bool
    nonce: int
    created_at: int
    deadline: int
    idempotency_key: str
    calls: tuple[SettlementCall, ...]
    chain_id: int = 137

    def __post_init__(self) -> None:
        for field in ("owner_address", "wallet_address"):
            object.__setattr__(self, field, normalize_evm_address(getattr(self, field), field))
        condition_valid = (
            _HEX32.fullmatch(self.condition_id) is not None
            if self.protocol == "2"
            else _HEX31.fullmatch(self.condition_id) is not None
        )
        if (
            not _ID.fullmatch(self.settlement_id)
            or not _ID.fullmatch(self.idempotency_key)
            or self.discord_user_id <= 0 or not self.profile_id
            or not self.market_id or not self.token_id or not self.outcome
            or not 0 < self.amount_atomic <= _UINT256_MAX
            or self.protocol not in {"2", "3"} or not condition_valid
            or not isinstance(self.negative_risk, bool)
            or not 0 <= self.nonce <= _UINT256_MAX
            or self.created_at <= 0
            or self.deadline != self.created_at + _BATCH_LIFETIME_SECONDS
            or self.chain_id != 137 or self.owner_address == self.wallet_address
            or not 1 <= len(self.calls) <= 2
            or self.calls != self.expected_calls()
        ):
            raise AccountConnectionError("Settlement approval binding is invalid.")

    def expected_calls(self) -> tuple[SettlementCall, ...]:
        manifest = POLYMARKET_PRODUCTION_MANIFEST
        if self.protocol == "2":
            if is_protocol_v3_position_id(self.token_id):
                raise AccountConnectionError("Legacy settlement token routing changed.")
            target = (
                manifest.neg_risk_collateral_adapter
                if self.negative_risk else manifest.collateral_adapter
            )
            return (SettlementCall(target, ctf_redeem_calldata(self.condition_id)),)
        decoded_condition, _ = decode_v3_position_id(self.token_id)
        if decoded_condition != self.condition_id.lower():
            raise AccountConnectionError("Protocol-v3 condition identity changed.")
        return (SettlementCall(
            manifest.protocol_v2_router,
            router_redeem_calldata(self.token_id, self.amount_atomic),
        ),)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_record(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def typed_data(self) -> dict[str, Any]:
        return {
            "domain": {
                "name": "DepositWallet", "version": "1", "chainId": 137,
                "verifyingContract": self.wallet_address,
            },
            "types": {name: [dict(field) for field in fields] for name, fields in _BATCH_TYPES.items()},
            "primaryType": "Batch",
            "message": {
                "wallet": self.wallet_address, "nonce": str(self.nonce),
                "deadline": str(self.deadline),
                "calls": [call.to_record() for call in self.calls],
            },
        }

    def relayer_request(self, signature: str) -> dict[str, Any]:
        if not isinstance(signature, str) or re.fullmatch(r"0x[0-9a-fA-F]{130}", signature) is None:
            raise AccountConnectionError("Settlement owner signature is invalid.")
        return {
            "depositWalletParams": {
                "calls": [call.to_record() for call in self.calls],
                "deadline": str(self.deadline),
                "depositWallet": self.wallet_address,
            },
            "from": self.owner_address,
            "metadata": f"Redeem Polymarket position for market {self.market_id}",
            "nonce": str(self.nonce), "signature": signature.lower(),
            "to": POLYMARKET_PRODUCTION_MANIFEST.deposit_wallet_factory.lower(),
            "type": "WALLET",
        }

    def to_record(self) -> dict[str, Any]:
        result = asdict(self)
        result["calls"] = [call.to_record() for call in self.calls]
        return result

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "SettlementPlan":
        try:
            values = dict(record)
            values["calls"] = tuple(
                SettlementCall(item["target"], item["data"], int(item["value"]))
                for item in values["calls"]
            )
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountConnectionError("Stored settlement is invalid.") from exc


def _address_word(address: str) -> bytes:
    return bytes(12) + bytes.fromhex(
        normalize_evm_address(address, "EIP-712 address")[2:]
    )


def deposit_wallet_batch_digest(
    *, wallet_address: str, chain_id: int, nonce: int, deadline: int,
    calls: tuple[SettlementCall, ...],
) -> bytes:
    """Hash one exact Deposit Wallet Batch for purpose-specific validators."""

    if not isinstance(calls, tuple) or not calls or not all(
        isinstance(call, SettlementCall) for call in calls
    ):
        raise AccountConnectionError("Deposit Wallet Batch calls are invalid.")
    domain_type = keccak(
        b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    )
    domain_separator = keccak(
        domain_type + keccak(b"DepositWallet") + keccak(b"1")
        + _uint(chain_id, "Batch chain") + _address_word(wallet_address)
    )
    call_type = keccak(b"Call(address target,uint256 value,bytes data)")
    call_hashes = b"".join(
        keccak(
            call_type + _address_word(call.target)
            + _uint(call.value, "Batch call value")
            + keccak(bytes.fromhex(call.data[2:]))
        )
        for call in calls
    )
    batch_type = keccak(
        b"Batch(address wallet,uint256 nonce,uint256 deadline,Call[] calls)"
        b"Call(address target,uint256 value,bytes data)"
    )
    batch_hash = keccak(
        batch_type + _address_word(wallet_address)
        + _uint(nonce, "Batch nonce") + _uint(deadline, "Batch deadline")
        + keccak(call_hashes)
    )
    return keccak(b"\x19\x01" + domain_separator + batch_hash)


def settlement_batch_digest(plan: SettlementPlan) -> bytes:
    if not isinstance(plan, SettlementPlan):
        raise AccountConnectionError("Settlement plan is invalid.")
    return deposit_wallet_batch_digest(
        wallet_address=plan.wallet_address, chain_id=plan.chain_id,
        nonce=plan.nonce, deadline=plan.deadline, calls=plan.calls,
    )


def verify_settlement_batch_signature(plan: SettlementPlan, signature: str) -> str:
    recovered = recover_signer_address(settlement_batch_digest(plan), signature)
    if recovered != plan.owner_address:
        raise AccountConnectionError(
            "Settlement owner signature does not match CryptoWallet."
        )
    return recovered


@dataclass(frozen=True, slots=True)
class SettlementApprovalRequest:
    request_id: str
    plan: SettlementPlan
    question: str
    market_path: str
    eligibility: EligibilityAttestation
    confirmation: TradeConfirmation
    balance_source: str
    balance_block_number: int | None = None

    def __post_init__(self) -> None:
        if (
            not _ID.fullmatch(self.request_id)
            or not isinstance(self.plan, SettlementPlan)
            or not isinstance(self.question, str) or not self.question
            or len(self.question) > 512
            or not isinstance(self.market_path, str)
            or re.fullmatch(
                r"/markets/(?:[0-9]+|slug/[A-Za-z0-9_-]+)", self.market_path
            ) is None
            or not isinstance(self.eligibility, EligibilityAttestation)
            or not isinstance(self.confirmation, TradeConfirmation)
            or self.balance_source not in {"data_api", "polygon_position_manager"}
            or self.balance_source == "data_api" and self.balance_block_number is not None
            or self.balance_source == "polygon_position_manager" and (
                self.balance_block_number is None or self.balance_block_number < 0
            )
            or (self.plan.protocol == "3") != (
                self.balance_source == "polygon_position_manager"
            )
        ):
            raise TradeConfirmationError("Settlement request is invalid.")
        if (
            self.eligibility.discord_user_id != self.plan.discord_user_id
            or self.eligibility.blocked
            or self.eligibility.checked_at > self.plan.created_at
            or self.eligibility.expires_at < self.confirmation.expires_at
            or self.confirmation.requester_id != self.plan.discord_user_id
            or self.confirmation.order_fingerprint != self.plan.fingerprint
            or self.confirmation.created_at != self.plan.created_at
            or self.confirmation.expires_at > self.plan.deadline
        ):
            raise TradeConfirmationError("Settlement approval bindings disagree.")

    @classmethod
    def create(
        cls, *, request_id: str, plan: SettlementPlan, question: str,
        market_path: str, eligibility: EligibilityAttestation,
        final_confirmation_required: bool,
        balance_source: str, balance_block_number: int | None = None,
    ) -> "SettlementApprovalRequest":
        expires_at = min(
            plan.created_at + 120, plan.deadline, eligibility.expires_at
        )
        confirmation = TradeConfirmation(
            requester_id=plan.discord_user_id,
            order_fingerprint=plan.fingerprint,
            final_confirmation_required=final_confirmation_required,
            created_at=plan.created_at, expires_at=expires_at,
        )
        return cls(
            request_id, plan, question, market_path, eligibility, confirmation,
            balance_source, balance_block_number,
        )

    def approve_primary(
        self, *, requester_id: int, now: int,
    ) -> "SettlementApprovalRequest":
        return replace(self, confirmation=self.confirmation.approve_primary(
            requester_id=requester_id,
            order_fingerprint=self.plan.fingerprint, now=now,
        ))

    def decide_final(
        self, approved: bool, *, requester_id: int, now: int,
    ) -> "SettlementApprovalRequest":
        return replace(self, confirmation=self.confirmation.decide_final(
            requester_id=requester_id,
            order_fingerprint=self.plan.fingerprint,
            approved=approved, now=now,
        ))

    def require_approved(self, *, requester_id: int, now: int) -> None:
        self.eligibility.require_current(discord_user_id=requester_id, now=now)
        self.confirmation.require_approved(
            requester_id=requester_id,
            order_fingerprint=self.plan.fingerprint, now=now,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id, "plan": self.plan.to_record(),
            "question": self.question, "market_path": self.market_path,
            "eligibility": asdict(self.eligibility),
            "confirmation": self.confirmation.to_record(),
            "balance_source": self.balance_source,
            "balance_block_number": self.balance_block_number,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "SettlementApprovalRequest":
        expected = {
            "request_id", "plan", "question", "market_path", "eligibility",
            "confirmation", "balance_source", "balance_block_number",
        }
        if not isinstance(record, Mapping) or set(record) != expected:
            raise TradeConfirmationError("Stored settlement request has an invalid shape.")
        try:
            return cls(
                request_id=str(record["request_id"]),
                plan=SettlementPlan.from_record(record["plan"]),
                question=str(record["question"]),
                market_path=str(record["market_path"]),
                eligibility=EligibilityAttestation(**dict(record["eligibility"])),
                confirmation=TradeConfirmation.from_record(record["confirmation"]),
                balance_source=str(record["balance_source"]),
                balance_block_number=record["balance_block_number"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TradeConfirmationError("Stored settlement request is invalid.") from exc


@dataclass(frozen=True, slots=True)
class SettlementOperation:
    """Restart-safe public lifecycle; raw owner signatures never persist."""

    plan: SettlementPlan
    state: SettlementState = SettlementState.APPROVED
    owner_signature_digest: str | None = None
    transaction_id: str | None = None
    transaction_hash: str | None = None
    failure_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.plan, SettlementPlan) or not isinstance(
            self.state, SettlementState
        ):
            raise AccountConnectionError("Settlement operation is invalid.")
        for value in (self.owner_signature_digest, self.failure_digest):
            if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise AccountConnectionError("Settlement operation digest is invalid.")
        if self.transaction_id is not None and (
            not isinstance(self.transaction_id, str)
            or not self.transaction_id or len(self.transaction_id) > 128
        ):
            raise AccountConnectionError("Settlement transaction ID is invalid.")
        if self.transaction_hash is not None and re.fullmatch(
            r"0x[0-9a-f]{64}", self.transaction_hash
        ) is None:
            raise AccountConnectionError("Settlement transaction hash is invalid.")
        if self.state is SettlementState.APPROVED and any((
            self.owner_signature_digest, self.transaction_id,
            self.transaction_hash, self.failure_digest,
        )):
            raise AccountConnectionError("Approved settlement contains results.")
        if self.state is SettlementState.SUBMITTING and not self.owner_signature_digest:
            raise AccountConnectionError("Submitting settlement lacks owner approval.")
        if self.state in {SettlementState.SUBMITTED, SettlementState.CONFIRMED} and (
            not self.owner_signature_digest or not self.transaction_id
        ):
            raise AccountConnectionError("Submitted settlement lacks identity.")
        if self.state is SettlementState.CONFIRMED and not self.transaction_hash:
            raise AccountConnectionError("Confirmed settlement lacks a transaction hash.")
        if self.state is SettlementState.FAILED and not self.failure_digest:
            raise AccountConnectionError("Failed settlement lacks a digest.")

    def begin_submission(self, signature: str, *, now: int) -> "SettlementOperation":
        if self.state is not SettlementState.APPROVED:
            raise AccountConnectionError("Settlement is not approved.")
        if now < self.plan.created_at or now >= self.plan.deadline:
            raise AccountConnectionError("Settlement approval expired.")
        self.plan.relayer_request(signature)
        return replace(
            self, state=SettlementState.SUBMITTING,
            owner_signature_digest=hashlib.sha256(
                bytes.fromhex(signature[2:])
            ).hexdigest(),
        )

    def recover_after_restart(self) -> "SettlementOperation":
        if self.state is SettlementState.SUBMITTING:
            return replace(self, state=SettlementState.UNKNOWN)
        return self

    def record_submission(self, response: Mapping[str, Any]) -> "SettlementOperation":
        if self.state is not SettlementState.SUBMITTING:
            raise AccountConnectionError("Settlement is not submitting.")
        transaction_id = response.get("transaction_id")
        transaction_hash = response.get("transaction_hash")
        if not isinstance(transaction_id, str) or not transaction_id or len(transaction_id) > 128:
            return replace(self, state=SettlementState.UNKNOWN)
        if transaction_hash is not None and (
            not isinstance(transaction_hash, str)
            or re.fullmatch(r"0x[0-9a-fA-F]{64}", transaction_hash) is None
        ):
            return replace(
                self, state=SettlementState.UNKNOWN,
                transaction_id=transaction_id,
            )
        return replace(
            self, state=SettlementState.SUBMITTED,
            transaction_id=transaction_id,
            transaction_hash=transaction_hash.lower() if transaction_hash else None,
        )

    def reconcile(self, response: Mapping[str, Any]) -> "SettlementOperation":
        if self.state not in {SettlementState.SUBMITTED, SettlementState.UNKNOWN}:
            raise AccountConnectionError("Settlement cannot be reconciled.")
        transaction_id = response.get("transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id:
            raise AccountConnectionError("Settlement transaction identity is missing.")
        if self.transaction_id and transaction_id != self.transaction_id:
            raise AccountConnectionError("Settlement transaction identity changed.")
        if any((
            response.get("from") != self.plan.owner_address,
            response.get("to") != POLYMARKET_PRODUCTION_MANIFEST.deposit_wallet_factory.lower(),
            response.get("proxy_address") != self.plan.wallet_address,
            response.get("type") != "WALLET",
        )):
            raise AccountConnectionError("Settlement relayer identity changed.")
        state = response.get("state")
        if state in {"STATE_FAILED", "STATE_INVALID"}:
            reason = str(response.get("error_msg") or state)
            return replace(
                self, state=SettlementState.FAILED,
                transaction_id=transaction_id,
                failure_digest=hashlib.sha256(reason.encode("utf-8")).hexdigest(),
            )
        if state == "STATE_CONFIRMED":
            transaction_hash = response.get("transaction_hash") or self.transaction_hash
            if not isinstance(transaction_hash, str) or re.fullmatch(
                r"0x[0-9a-fA-F]{64}", transaction_hash
            ) is None:
                raise AccountConnectionError(
                    "Confirmed settlement lacks a transaction hash."
                )
            return replace(
                self, state=SettlementState.CONFIRMED,
                transaction_id=transaction_id,
                transaction_hash=transaction_hash.lower(),
            )
        if state not in {"STATE_NEW", "STATE_EXECUTED", "STATE_MINED"}:
            raise AccountConnectionError("Settlement relayer state is invalid.")
        return replace(
            self, state=SettlementState.SUBMITTED,
            transaction_id=transaction_id,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_record(), "state": self.state.value,
            "owner_signature_digest": self.owner_signature_digest,
            "transaction_id": self.transaction_id,
            "transaction_hash": self.transaction_hash,
            "failure_digest": self.failure_digest,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "SettlementOperation":
        try:
            values = dict(record)
            values["plan"] = SettlementPlan.from_record(values["plan"])
            values["state"] = SettlementState(values["state"])
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountConnectionError("Stored settlement operation is invalid.") from exc
