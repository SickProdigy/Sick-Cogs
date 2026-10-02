"""Strict public transport contracts for Polymarket Bridge funding."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Awaitable, Callable

import aiohttp

from .account_connection import AccountConnectionError, normalize_evm_address
from .production_manifest import POLYMARKET_PRODUCTION_MANIFEST

SUPPORTED_ASSETS_PATH = "/supported-assets"
DEPOSIT_PATH = "/deposit"
QUOTE_PATH = "/quote"
STATUS_PREFIX = "/status/"
NATIVE_EVM_TOKEN = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
HEX_32 = re.compile(r"^0x[0-9a-f]{64}$")
HEX_HASH = re.compile(r"^0x[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
Transport = Callable[..., Awaitable[object]]


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise AccountConnectionError(f"Bridge {label} is invalid.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AccountConnectionError(f"Bridge {label} is invalid.") from exc
    if not result.is_finite() or result < 0:
        raise AccountConnectionError(f"Bridge {label} is invalid.")
    return result


def _uint_string(value: Any, label: str) -> int:
    if not isinstance(value, str) or not value.isdigit():
        raise AccountConnectionError(f"Bridge {label} is invalid.")
    result = int(value)
    if result < 0 or result >= 2**256:
        raise AccountConnectionError(f"Bridge {label} is invalid.")
    return result


@dataclass(frozen=True, slots=True)
class BridgeAsset:
    chain_id: int
    chain_name: str
    token_name: str
    symbol: str
    token_address: str
    decimals: int
    minimum_usd: Decimal

    @classmethod
    def from_payload(cls, payload: dict) -> "BridgeAsset":
        if not isinstance(payload, dict) or set(payload) != {
            "chainId", "chainName", "token", "minCheckoutUsd",
        }:
            raise AccountConnectionError("Bridge supported asset shape is invalid.")
        token = payload["token"]
        if not isinstance(token, dict) or set(token) != {
            "name", "symbol", "address", "decimals",
        }:
            raise AccountConnectionError("Bridge token shape is invalid.")
        chain = _uint_string(payload["chainId"], "chain ID")
        name = payload["chainName"]
        token_name = token["name"]
        symbol = token["symbol"]
        address = token["address"]
        decimals = token["decimals"]
        if (not all(isinstance(v, str) and 1 <= len(v) <= 64
                    for v in (name, token_name, symbol))
                or not isinstance(address, str)
                or re.fullmatch(r"0x[0-9a-fA-F]{40}", address) is None
                or type(decimals) is not int or not 0 <= decimals <= 36):
            raise AccountConnectionError("Bridge supported asset is invalid.")
        return cls(
            chain, name, token_name, symbol, address.lower(), decimals,
            _decimal(payload["minCheckoutUsd"], "minimum"),
        )


@dataclass(frozen=True, slots=True)
class BridgeQuote:
    quote_id: str
    input_usd: Decimal
    output_usd: Decimal
    output_atomic: int
    minimum_received_usd: Decimal
    total_impact_percent: Decimal
    total_impact_usd: Decimal
    gas_usd: Decimal
    maximum_slippage_percent: Decimal
    checkout_time_ms: int

    @classmethod
    def from_payload(cls, payload: dict) -> "BridgeQuote":
        required = {
            "estCheckoutTimeMs", "estFeeBreakdown", "estInputUsd",
            "estOutputUsd", "estToTokenBaseUnit", "quoteId",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise AccountConnectionError("Bridge quote shape is invalid.")
        fees = payload["estFeeBreakdown"]
        fee_keys = {
            "appFeeLabel", "appFeePercent", "appFeeUsd", "fillCostPercent",
            "fillCostUsd", "gasUsd", "maxSlippage", "minReceived",
            "swapImpact", "swapImpactUsd", "totalImpact", "totalImpactUsd",
        }
        if not isinstance(fees, dict) or set(fees) != fee_keys:
            raise AccountConnectionError("Bridge quote fee shape is invalid.")
        quote_id = payload["quoteId"]
        checkout = payload["estCheckoutTimeMs"]
        if (not isinstance(quote_id, str) or HEX_32.fullmatch(quote_id.lower()) is None
                or type(checkout) is not int or checkout <= 0 or checkout > 86_400_000
                or not isinstance(fees["appFeeLabel"], str)
                or len(fees["appFeeLabel"]) > 128):
            raise AccountConnectionError("Bridge quote identity is invalid.")
        result = cls(
            quote_id.lower(), _decimal(payload["estInputUsd"], "input USD"),
            _decimal(payload["estOutputUsd"], "output USD"),
            _uint_string(payload["estToTokenBaseUnit"], "output amount"),
            _decimal(fees["minReceived"], "minimum received"),
            _decimal(fees["totalImpact"], "total impact"),
            _decimal(fees["totalImpactUsd"], "total impact USD"),
            _decimal(fees["gasUsd"], "gas USD"),
            _decimal(fees["maxSlippage"], "maximum slippage"), checkout,
        )
        if (result.input_usd <= 0 or result.output_usd <= 0
                or result.output_atomic <= 0
                or result.output_usd > result.input_usd
                or result.minimum_received_usd > result.output_usd
                or result.total_impact_percent > 100
                or result.maximum_slippage_percent > 100):
            raise AccountConnectionError("Bridge quote values are invalid.")
        return result


@dataclass(frozen=True, slots=True)
class BridgeDepositAddresses:
    evm: str
    svm: str
    btc: str
    tvm: str | None = None

    @classmethod
    def from_payload(cls, payload: dict) -> "BridgeDepositAddresses":
        if not isinstance(payload, dict) or set(payload) not in (
            {"evm", "svm", "btc"}, {"evm", "svm", "btc", "tvm"},
        ):
            raise AccountConnectionError("Bridge deposit address shape is invalid.")
        evm = normalize_evm_address(payload["evm"], "Bridge EVM deposit address")
        values = {name: payload.get(name) for name in ("svm", "btc", "tvm")}
        if any(value is not None and (not isinstance(value, str)
                                     or not 1 <= len(value) <= 256)
               for value in values.values()):
            raise AccountConnectionError("Bridge deposit address is invalid.")
        return cls(evm, values["svm"], values["btc"], values["tvm"])


@dataclass(frozen=True, slots=True)
class BridgeTransaction:
    source_chain_id: int
    source_token_address: str
    source_amount_atomic: int
    destination_chain_id: int
    destination_token_address: str
    status: str
    transaction_hash: str | None
    created_time_ms: int | None

    @classmethod
    def from_payload(cls, payload: dict) -> "BridgeTransaction":
        required = {
            "fromChainId", "fromTokenAddress", "fromAmountBaseUnit",
            "toChainId", "toTokenAddress", "status",
        }
        allowed = required | {"txHash", "createdTimeMs"}
        if (not isinstance(payload, dict) or not required.issubset(payload)
                or set(payload) - allowed):
            raise AccountConnectionError("Bridge status transaction shape is invalid.")
        status = payload["status"]
        if status not in {
            "DEPOSIT_DETECTED", "PROCESSING", "ORIGIN_TX_CONFIRMED",
            "SUBMITTED", "COMPLETED", "FAILED",
        }:
            raise AccountConnectionError("Bridge transaction status is invalid.")
        tx_hash = payload.get("txHash")
        created = payload.get("createdTimeMs")
        if (tx_hash is not None and (not isinstance(tx_hash, str)
                                     or HEX_HASH.fullmatch(tx_hash.lower()) is None)):
            raise AccountConnectionError("Bridge transaction hash is invalid.")
        if created is not None and (type(created) is not int or created <= 0):
            raise AccountConnectionError("Bridge transaction timestamp is invalid.")
        source_token = payload["fromTokenAddress"]
        if (not isinstance(source_token, str)
                or re.fullmatch(r"0x[0-9a-fA-F]{40}", source_token) is None):
            raise AccountConnectionError("Bridge source token is invalid.")
        return cls(
            _uint_string(payload["fromChainId"], "source chain ID"),
            source_token.lower(),
            _uint_string(payload["fromAmountBaseUnit"], "source amount"),
            _uint_string(payload["toChainId"], "destination chain ID"),
            normalize_evm_address(payload["toTokenAddress"], "Bridge destination token"),
            status, tx_hash.lower() if tx_hash else None, created,
        )


class PolymarketBridgeClient:
    def __init__(self, transport: Transport | None = None):
        self._transport = transport or self._http_transport

    @staticmethod
    async def _http_transport(method: str, url: str, *, headers=None, body=None, params=None):
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method, url, headers=headers, data=body, params=params
            ) as response:
                payload = await response.json(content_type=None)
                if response.status < 200 or response.status >= 300:
                    raise AccountConnectionError(
                        f"Polymarket Bridge returned HTTP {response.status}."
                    )
                return payload

    async def supported_assets(self) -> tuple[BridgeAsset, ...]:
        payload = await self._transport(
            "GET", POLYMARKET_PRODUCTION_MANIFEST.bridge_api + SUPPORTED_ASSETS_PATH,
            headers={"Accept": "application/json"}, body=None, params=None,
        )
        if not isinstance(payload, dict) or set(payload) not in (
            {"supportedAssets"}, {"supportedAssets", "note"},
        ) or not isinstance(payload["supportedAssets"], list):
            raise AccountConnectionError("Bridge supported-assets response is invalid.")
        assets = tuple(BridgeAsset.from_payload(item) for item in payload["supportedAssets"])
        if not assets:
            raise AccountConnectionError("Bridge supported-assets response is empty.")
        identities = {(item.chain_id, item.token_address) for item in assets}
        if len(identities) != len(assets):
            raise AccountConnectionError("Bridge supported assets contain duplicate identities.")
        return assets

    async def quote(
        self, *, amount_atomic: int, source_chain_id: int, source_token: str,
        recipient: str,
    ) -> BridgeQuote:
        if amount_atomic <= 0 or source_chain_id <= 0:
            raise AccountConnectionError("Bridge quote request is invalid.")
        body = json.dumps({
            "fromAmountBaseUnit": str(amount_atomic),
            "fromChainId": str(source_chain_id),
            "fromTokenAddress": source_token,
            "recipientAddress": normalize_evm_address(recipient, "pUSD recipient"),
            "toChainId": str(POLYMARKET_PRODUCTION_MANIFEST.chain_id),
            "toTokenAddress": POLYMARKET_PRODUCTION_MANIFEST.collateral_token,
        }, separators=(",", ":"))
        payload = await self._transport(
            "POST", POLYMARKET_PRODUCTION_MANIFEST.bridge_api + QUOTE_PATH,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            body=body, params=None,
        )
        return BridgeQuote.from_payload(payload)

    async def deposit_addresses(self, account_wallet: str) -> BridgeDepositAddresses:
        wallet = normalize_evm_address(account_wallet, "Polymarket account wallet")
        body = json.dumps({"address": wallet}, separators=(",", ":"))
        payload = await self._transport(
            "POST", POLYMARKET_PRODUCTION_MANIFEST.bridge_api + DEPOSIT_PATH,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            body=body, params=None,
        )
        if not isinstance(payload, dict) or set(payload) not in (
            {"address"}, {"address", "note"},
        ):
            raise AccountConnectionError("Bridge deposit response is invalid.")
        return BridgeDepositAddresses.from_payload(payload["address"])

    async def status(self, deposit_address: str) -> tuple[BridgeTransaction, ...]:
        address = normalize_evm_address(deposit_address, "Bridge deposit address")
        payload = await self._transport(
            "GET", POLYMARKET_PRODUCTION_MANIFEST.bridge_api + STATUS_PREFIX + address,
            headers={"Accept": "application/json"}, body=None, params=None,
        )
        if (not isinstance(payload, dict) or set(payload) != {"transactions"}
                or not isinstance(payload["transactions"], list)):
            raise AccountConnectionError("Bridge status response is invalid.")
        return tuple(BridgeTransaction.from_payload(item)
                     for item in payload["transactions"])
