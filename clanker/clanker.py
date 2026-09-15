import asyncio
import copy
import datetime
import io
import ipaddress
import json
import logging
import re
import secrets
import socket
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import aiohttp

import discord
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, humanize_list

from .constants import (
    BASE_CHAIN_ID,
    CONFIG_IDENTIFIER,
    DEFAULT_CLANKER_SUPPLY,
    MAX_AUDIT_RECORDS,
    MERKLE_ROOT_RE,
    MIN_AIRDROP_LOCKUP_SECONDS,
    MIN_VAULT_LOCKUP_SECONDS,
    SYMBOL_RE,
)
from .models import (
    BASE_SEPOLIA_WETH, ClankerAirdrop, ClankerLaunchIntent, ClankerPool,
    ClankerPoolPosition, ClankerReward, ClankerVault, standard_base_sepolia_pool,
)
from .operation import clanker_deployment_operation
from .rewards import (
    WETH, collect_rewards_call, reconcile_collection_receipt,
    reconcile_withdrawal_receipt, reward_preflight,
    verify_external_collection,
)
from .helpers import (
    build_airdrop_merkle_tree,
    format_tokens,
    is_eth_address,
    parse_airdrop_lines,
    utc_now,
    validate_airdrop_total,
)
from .admin import ClankerAdminMixin
from .views import (
    ClankerClaimAllView, ClankerDeleteDraftsView, ClankerDraftHistoryView,
    ClankerDraftView, ClankerLaunchHistoryView,
    ClankerReceiptRewardsView, ClankerTreasuryWithdrawalView,
)

log = logging.getLogger("red.Sick-Cogs.Clanker")

BASE_SEPOLIA_RPCS = ("https://sepolia.base.org", "https://sepolia-preconf.base.org")
TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
MAX_RPC_BYTES = 1024 * 1024
TOKEN_CREATED_TOPIC = "0x9299d1d1a88d8e1abdc591ae7a167a6bc63a8f17d695804e9091ee33aa89fb67"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


async def clanker_guild_or_dm_history(ctx: commands.Context) -> bool:
    """Keep Clanker guild-scoped except for requester-owned launch history."""
    if ctx.guild is not None:
        return True
    dm_commands = {
        "clanker",
        "clanker drafts",
        "clanker launches",
    }
    if ctx.command and ctx.command.qualified_name in dm_commands:
        return True
    raise commands.NoPrivateMessage


def _creator_buy_in_tokens(
    receipt: Dict[str, Any], token_address: str, recipient: str, native_value_wei: int
) -> Optional[int]:
    if int(native_value_wei or 0) <= 0:
        return None
    recipient_topic = str(recipient).lower().replace("0x", "").rjust(64, "0")
    total = 0
    for item in receipt.get("logs") or []:
        topics = item.get("topics") if isinstance(item, dict) else None
        if (
            str(item.get("address") or "").lower() == str(token_address).lower()
            and isinstance(topics, list) and len(topics) >= 3
            and str(topics[0]).lower() == TRANSFER_TOPIC
            and str(topics[2]).lower().replace("0x", "") == recipient_topic
        ):
            try:
                total += int(str(item.get("data") or "0x0"), 16)
            except ValueError:
                return None
    return total or None


def _format_eth_wei(value: int) -> str:
    whole, fraction = divmod(int(value), 10**18)
    suffix = str(fraction).rjust(18, "0").rstrip("0")
    return "{}{} ETH".format(whole, "." + suffix if suffix else "")


def _format_token_atomic(value: int) -> str:
    whole, fraction = divmod(int(value), 10**18)
    suffix = str(fraction).rjust(18, "0").rstrip("0")
    return "{:,}{}".format(whole, "." + suffix if suffix else "")


def _format_vault_duration(seconds: int) -> str:
    amount = int(seconds or 0)
    if amount == 0:
        return "None"
    for singular, plural, size in (
        ("year", "years", 31536000), ("month", "months", 2592000),
        ("week", "weeks", 604800), ("day", "days", 86400), ("hour", "hours", 3600),
    ):
        if amount % size == 0:
            count = amount // size
            return "{} {}".format(count, singular if count == 1 else plural)
    return "{:,} seconds".format(amount)


async def _read_bounded_rpc_content(content: Any) -> bytes:
    """Collect a fragmented RPC response without exceeding the response cap."""
    raw = bytearray()
    async for chunk in content.iter_chunked(64 * 1024):
        raw.extend(chunk)
        if len(raw) > MAX_RPC_BYTES:
            raise RuntimeError("oversized RPC response")
    return bytes(raw)


async def clanker_rpc(method: str, params: list[Any]) -> Any:
    """Call bounded public Base Sepolia RPC endpoints."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    timeout = aiohttp.ClientTimeout(total=15)
    last_error = None
    for url in BASE_SEPOLIA_RPCS:
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as response:
                    raw = await _read_bounded_rpc_content(response.content)
                    if response.status != 200:
                        raise RuntimeError("invalid RPC response")
                    body = json.loads(raw.decode("utf-8"))
                    if body.get("error") or "result" not in body:
                        raise RuntimeError("RPC request rejected")
                    return body["result"]
        except (aiohttp.ClientError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
    raise RuntimeError("Base Sepolia transaction lookup is unavailable.") from last_error


async def verify_external_operation(transaction_hash: str, operation: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    """Verify one direct wallet call and its deployed token against immutable data."""
    if not TX_HASH_RE.fullmatch(str(transaction_hash)):
        raise ValueError("The external transaction hash is invalid.")
    expected = {"launch_id", "payload_hash", "chain_id", "to", "value", "data"}
    if set(operation) != expected or int(operation["chain_id"]) != BASE_CHAIN_ID:
        raise ValueError("The stored Clanker operation is invalid.")
    transaction = await clanker_rpc("eth_getTransactionByHash", [transaction_hash])
    receipt = await clanker_rpc("eth_getTransactionReceipt", [transaction_hash])
    chain_id = await clanker_rpc("eth_chainId", [])
    if int(str(chain_id), 16) != BASE_CHAIN_ID:
        raise RuntimeError("The RPC endpoint is not Base Sepolia.")
    if transaction is None or receipt is None:
        return {"verified": False, "status": "pending", "transaction_hash": transaction_hash.lower()}
    try:
        returned_hash = str(transaction["hash"]).lower()
        sender = str(transaction["from"]).lower()
        target = str(transaction["to"]).lower()
        value = int(str(transaction["value"]), 16)
        calldata = str(transaction.get("input") or transaction.get("data") or "").lower()
        success = int(str(receipt["status"]), 16) == 1
        receipt_hash = str(receipt["transactionHash"]).lower()
        block_number = int(str(receipt["blockNumber"]), 16)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Base Sepolia returned malformed transaction data.") from exc
    if not ADDRESS_RE.fullmatch(sender) or returned_hash != transaction_hash.lower() or receipt_hash != returned_hash:
        raise RuntimeError("Base Sepolia returned mismatched transaction identity.")
    if not success:
        raise ValueError("The external Clanker transaction failed on-chain.")
    if target != str(operation["to"]).lower() or value != int(operation["value"]) or calldata != str(operation["data"]).lower():
        raise ValueError("The external transaction does not match the immutable Clanker operation.")
    expected_admin = str((intent.get("token") or {}).get("admin") or "").lower()
    if not ADDRESS_RE.fullmatch(expected_admin):
        raise ValueError("The immutable creator and token administrator is invalid.")
    logs = receipt.get("logs")
    if not isinstance(logs, list):
        raise RuntimeError("Base Sepolia returned malformed receipt logs.")
    created = [item for item in logs if isinstance(item, dict)
               and str(item.get("address", "")).lower() == str(operation["to"]).lower()
               and isinstance(item.get("topics"), list) and len(item["topics"]) >= 3
               and str(item["topics"][0]).lower() == TOKEN_CREATED_TOPIC]
    if len(created) != 1:
        raise ValueError("The receipt does not contain exactly one pinned Clanker TokenCreated event.")
    token_address = "0x" + str(created[0]["topics"][1])[-40:].lower()
    token_admin = "0x" + str(created[0]["topics"][2])[-40:].lower()
    if not ADDRESS_RE.fullmatch(token_address) or token_admin != expected_admin:
        raise ValueError("The Clanker token event does not match the immutable launch admin.")
    code = str(await clanker_rpc("eth_getCode", [token_address, "latest"]) or "").lower()
    if code in {"", "0x", "0x0"}:
        raise ValueError("The reported Clanker token has no deployed bytecode.")
    buy_in_tokens = _creator_buy_in_tokens(
        receipt, token_address, expected_admin, int(intent.get("expected_native_value_wei") or 0)
    )
    return {"verified": True, "status": "confirmed", "transaction_hash": returned_hash,
            "signer_address": sender, "block_number": block_number, "token_address": token_address,
            "creator_buy_in_tokens_atomic": buy_in_tokens}



async def verify_internal_receipt(
    transaction_hash: str, operation: Dict[str, Any], intent: Dict[str, Any]
) -> Dict[str, Any]:
    """Verify a confirmed smart-account launch from its factory receipt event."""
    if not TX_HASH_RE.fullmatch(str(transaction_hash)):
        raise ValueError("The internal Clanker transaction hash is invalid.")
    receipt = await clanker_rpc("eth_getTransactionReceipt", [transaction_hash])
    if not isinstance(receipt, dict):
        raise RuntimeError("The confirmed Clanker receipt is unavailable.")
    if int(str(receipt.get("status", "0x0")), 16) != 1:
        raise ValueError("The internal Clanker transaction failed on-chain.")
    logs = receipt.get("logs")
    if not isinstance(logs, list):
        raise RuntimeError("Base Sepolia returned malformed receipt logs.")
    created = [
        item for item in logs
        if isinstance(item, dict)
        and str(item.get("address", "")).lower() == str(operation["to"]).lower()
        and isinstance(item.get("topics"), list)
        and len(item["topics"]) >= 3
        and str(item["topics"][0]).lower() == TOKEN_CREATED_TOPIC
    ]
    if len(created) != 1:
        raise ValueError("The receipt has no unique pinned Clanker TokenCreated event.")
    token_address = "0x" + str(created[0]["topics"][1])[-40:].lower()
    token_admin = "0x" + str(created[0]["topics"][2])[-40:].lower()
    expected_admin = str((intent.get("token") or {}).get("admin") or "").lower()
    if not ADDRESS_RE.fullmatch(token_address) or token_admin != expected_admin:
        raise ValueError("The confirmed Clanker token event does not match the review card.")
    code = str(await clanker_rpc("eth_getCode", [token_address, "latest"]) or "").lower()
    if code in {"", "0x", "0x0"}:
        raise ValueError("The confirmed Clanker token has no deployed bytecode.")
    buy_in_tokens = _creator_buy_in_tokens(
        receipt, token_address, expected_admin, int(intent.get("expected_native_value_wei") or 0)
    )
    return {
        "token_address": token_address,
        "transaction_hash": str(transaction_hash).lower(),
        "block_number": int(str(receipt["blockNumber"]), 16),
        "creator_buy_in_tokens_atomic": buy_in_tokens,
    }


class Clanker(ClankerAdminMixin, commands.Cog):
    """Prepare and orchestrate Clanker token launch requests on Base Sepolia."""

    __author__ = ["SickProdigy"]
    __version__ = "0.1.0"

    default_global = {
        "treasury_address": None,
        "platform_bps": 2000,
        "platform_config_migrated": False,
    }

    default_guild = {
        "enabled": False,
        "launch_channel_id": None,
        "approval_channel_id": None,
        "allowed_role_id": None,
        "blocked_role_id": None,
        "launch_cooldown_seconds": 60,
        "daily_max_per_user": 0,
        "vault_enabled": False,
        "vault_percentage": 0,
        "vault_lockup_seconds": MIN_VAULT_LOCKUP_SECONDS,
        "vault_vesting_seconds": 0,
        "vault_recipient": None,
        "airdrop_enabled": False,
        "airdrop_merkle_root": None,
        "airdrop_amount": 0,
        "airdrop_proof_export": None,
        "airdrop_lockup_seconds": MIN_AIRDROP_LOCKUP_SECONDS,
        "airdrop_vesting_seconds": 0,
        "airdrop_admin": None,
        "audit_log": [],
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_global(**self.default_global)
        self.config.register_guild(**self.default_guild)
        self.user_cooldowns: Dict[Tuple[int, int], datetime.datetime] = {}
        self.confirmation_tasks: set[asyncio.Task] = set()

    async def settings_for_guild(self, guild: discord.Guild) -> Dict[str, Any]:
        """Combine guild launch policy with bot-owner platform configuration."""
        settings = await self.config.guild(guild).all()
        settings.update({
            "treasury_address": await self.config.treasury_address(),
            "platform_bps": int(await self.config.platform_bps()),
        })
        return settings

    async def cog_load(self):
        guild_data = await self.config.all_guilds()
        if not await self.config.platform_config_migrated():
            treasuries = {
                str(data.get("treasury_address") or "").lower()
                for data in guild_data.values() if data.get("treasury_address")
            }
            platform_splits = {
                int(data.get("platform_bps", 2000)) for data in guild_data.values()
            }
            if len(treasuries) == 1 and not await self.config.treasury_address():
                await self.config.treasury_address.set(treasuries.pop())
            elif len(treasuries) > 1:
                log.error(
                    "Clanker guild treasury values conflict; set the global platform "
                    "treasury with clankerset treasury before launching."
                )
            if len(platform_splits) == 1:
                await self.config.platform_bps.set(platform_splits.pop())
            await self.config.platform_config_migrated.set(True)
        for guild_id, data in guild_data.items():
            for record in data.get("audit_log") or []:
                if record.get("status") in {"internal_submitted", "internal_uncertain"}:
                    self._start_confirmation_task(
                        int(guild_id), int(record["requester_id"]),
                        str(record["launch_id"]),
                    )
                if (
                    record.get("status") == "external_pending"
                    and record.get("transaction_hash")
                    and record.get("operation")
                    and record.get("intent")
                ):
                    self._start_external_confirmation_task(
                        int(guild_id), int(record["requester_id"]),
                        str(record["launch_id"]),
                    )
                if (record.get("reward_collection") or {}).get("status") == "submitted":
                    self._start_reward_confirmation_task(
                        int(guild_id), int(record["requester_id"]),
                        str(record["launch_id"]),
                    )
                withdrawal = record.get("reward_withdrawal") or {}
                if withdrawal.get("status") == "submitted" and withdrawal.get("submitted_by"):
                    self._start_treasury_confirmation_task(
                        int(guild_id), int(withdrawal["submitted_by"]), str(record["launch_id"]))

    def cog_unload(self):
        for task in self.confirmation_tasks:
            task.cancel()

    def _start_confirmation_task(
        self, guild_id: int, user_id: int, launch_id: str
    ) -> None:
        task = self.bot.loop.create_task(
            self._track_internal_confirmation(guild_id, user_id, launch_id)
        )
        self.confirmation_tasks.add(task)
        task.add_done_callback(self.confirmation_tasks.discard)

    def _start_external_confirmation_task(
        self, guild_id: int, user_id: int, launch_id: str
    ) -> None:
        task = self.bot.loop.create_task(
            self._track_external_confirmation(guild_id, user_id, launch_id)
        )
        self.confirmation_tasks.add(task)
        task.add_done_callback(self.confirmation_tasks.discard)

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        """Remove the Discord identity attached to retained guild audit records."""
        for guild_id in (await self.config.all_guilds()):
            async with self.config.guild_from_id(guild_id).audit_log() as audit_log:
                for record in audit_log:
                    if int(record.get("requester_id", 0) or 0) == int(user_id):
                        record["requester_id"] = 0
                        record["requester_name"] = "Deleted User"
                        record.pop("launch_ref", None)

    @staticmethod
    def validate_https_url(url: str) -> bool:
        parsed = urlparse((url or "").strip())
        return parsed.scheme == "https" and bool(parsed.netloc)

    async def validate_remote_image(self, url: str) -> None:
        """Validate a direct public HTTPS raster image without downloading it."""
        parsed = urlparse((url or "").strip())
        if not self.validate_https_url(url) or not parsed.hostname:
            raise ValueError("Image URL must be a direct public HTTPS image URL.")
        try:
            addresses = await asyncio.get_running_loop().getaddrinfo(
                parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
            )
        except socket.gaierror as exc:
            raise ValueError("Image host could not be resolved.") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global:
                raise ValueError("Image URL must resolve only to public internet addresses.")
        timeout = aiohttp.ClientTimeout(total=8)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url, headers={"Range": "bytes=0-511"}, allow_redirects=False
                ) as response:
                    if response.status not in {200, 206}:
                        raise ValueError(
                            "Image URL must respond directly without redirects or errors."
                        )
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                    if content_type not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
                        raise ValueError("Image URL must return a JPEG, PNG, GIF, or WebP image.")
                    length = response.headers.get("Content-Length")
                    if length and int(length) > 8 * 1024 * 1024:
                        raise ValueError("Image must be 8 MB or smaller.")
                    prefix = await response.content.read(512)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ValueError("Image URL could not be reached safely.") from exc
        signatures = (
            prefix.startswith(b"\xff\xd8\xff"),
            prefix.startswith(b"\x89PNG\r\n\x1a\n"),
            prefix.startswith((b"GIF87a", b"GIF89a")),
            prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP",
        )
        if not any(signatures):
            raise ValueError("Image response does not contain the advertised image format.")

    async def companion_session_url(self) -> str:
        """Return the single shared CryptoWallet companion session page."""
        wallet = self.bot.get_cog("CryptoWallet")
        if wallet is None or not hasattr(wallet, "config"):
            raise RuntimeError("CryptoWallet must be loaded for the shared companion.")
        base_url = str(await wallet.config.approval_base_url() or "").rstrip("/")
        if not self.validate_https_url(base_url):
            raise RuntimeError("The shared CryptoWallet companion URL is not configured.")
        return base_url + "/session"
    async def create_external_wallet_handoff(self, user: Any, handoff: Dict[str, Any]) -> str:
        """Register one Clanker-owned operation with the shared companion relay."""
        wallet = self.bot.get_cog("CryptoWallet")
        create = getattr(wallet, "clanker_create_external_handoff", None) if wallet else None
        register = getattr(wallet, "register_recovery_handoff", None) if wallet else None
        if not callable(create) or not callable(register):
            raise RuntimeError("CryptoWallet shared companion handoff support is unavailable.")
        token, expires_at = await create(int(user.id), handoff)
        handle = await register(token, int(expires_at))
        if not isinstance(handle, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", handle):
            raise RuntimeError("CryptoWallet returned an invalid companion handoff.")
        return await self.companion_session_url() + "#handoff=" + quote(handle, safe="")

    @staticmethod
    def build_payload(
        symbol: str,
        name: str,
        primary_beneficiary: str,
        platform_address: str,
        platform_bps: int,
        airdrop_enabled: bool,
        airdrop_merkle_root: Optional[str],
        airdrop_amount: int,
        airdrop_lockup_seconds: int,
        airdrop_vesting_seconds: int,
        airdrop_admin: Optional[str],
        requester_id: int,
        image_url: Optional[str] = None,
        description: Optional[str] = None,
        vault_enabled: bool = False,
        vault_percentage: int = 0,
        vault_lockup_seconds: int = MIN_VAULT_LOCKUP_SECONDS,
        vault_vesting_seconds: int = 0,
        vault_recipient: Optional[str] = None,
        creator_reward_recipient: Optional[str] = None,
        expected_native_value_wei: int = 0,
    ) -> Dict[str, Any]:
        clean_name = " ".join(str(name or "").strip().split())
        clean_symbol = str(symbol or "").strip().upper().lstrip("$")
        if not clean_name or len(clean_name.encode("utf-8")) > 64:
            raise ValueError("Token name must contain 1 through 64 UTF-8 bytes.")
        if not SYMBOL_RE.fullmatch(clean_symbol):
            raise ValueError("Token symbol must contain 2 through 12 uppercase letters or numbers.")
        if not is_eth_address(primary_beneficiary):
            raise ValueError("Token admin must be a valid EVM address.")
        creator_treasury = creator_reward_recipient or primary_beneficiary
        if not is_eth_address(creator_treasury):
            raise ValueError("Creator reward treasury must be a valid EVM address.")
        if not is_eth_address(platform_address):
            raise ValueError("Platform treasury must be a valid EVM address.")
        if image_url and not Clanker.validate_https_url(image_url):
            raise ValueError("Image URL must be HTTPS.")
        if not 0 <= platform_bps <= 10_000:
            raise ValueError("Platform reward bps must be from 0 through 10000.")
        if not 0 <= int(expected_native_value_wei) <= 10**18:
            raise ValueError("Creator buy-in must be from 0 through 1 ETH.")
        recipients = []
        creator_bps = 10_000 - platform_bps
        if creator_bps:
            creator_reward = ClankerReward(
                primary_beneficiary, creator_treasury, creator_bps,
            ).to_dict()
            recipients.append(creator_reward)
        if platform_bps:
            platform_reward = ClankerReward(
                platform_address, platform_address, platform_bps,
            ).to_dict()
            recipients.append(platform_reward)
        pool = standard_base_sepolia_pool()
        payload = {
            "name": clean_name,
            "symbol": clean_symbol,
            "image": image_url or "",
            "chainId": BASE_CHAIN_ID,
            "tokenAdmin": primary_beneficiary.lower(),
            "metadata": {"description": description or ""},
            "context": {
                "interface": "SickGamingBot", "platform": "discord",
                "id": str(requester_id),
            },
            "pool": {
                "pairedToken": "WETH",
                "tickIfToken0IsClanker": pool.tick_if_token0_is_clanker,
                "tickSpacing": pool.tick_spacing,
                "positions": [{
                    "tickLower": item.tick_lower, "tickUpper": item.tick_upper,
                    "positionBps": item.position_bps,
                } for item in pool.positions],
            },
            "fees": {
                "type": pool.fee_type, "clankerFee": pool.clanker_fee_bps,
                "pairedFee": pool.paired_fee_bps,
            },
            "rewards": {"recipients": recipients},
        }
        if int(expected_native_value_wei):
            payload["devBuy"] = {
                "ethAmountWei": str(int(expected_native_value_wei)),
                "recipient": primary_beneficiary.lower(),
                "amountOutMin": "0",
            }
        vault = None
        if vault_enabled:
            vault = ClankerVault(
                vault_recipient or primary_beneficiary, vault_percentage,
                vault_lockup_seconds, vault_vesting_seconds,
            )
            payload["vault"] = {
                "percentage": vault_percentage,
                "lockupDuration": vault_lockup_seconds,
                "vestingDuration": vault_vesting_seconds,
                "recipient": vault.recipient,
            }
        airdrop = None
        if airdrop_enabled and airdrop_merkle_root and airdrop_amount > 0:
            validate_airdrop_total(airdrop_amount, DEFAULT_CLANKER_SUPPLY)
            airdrop = ClankerAirdrop(
                airdrop_admin or primary_beneficiary, airdrop_merkle_root,
                airdrop_amount, airdrop_lockup_seconds, airdrop_vesting_seconds,
            )
            payload["airdrop"] = {
                "admin": airdrop.admin,
                "merkleRoot": airdrop.merkle_root,
                "amount": airdrop_amount,
                "lockupDuration": airdrop_lockup_seconds,
                "vestingDuration": airdrop_vesting_seconds,
            }
        extension_bps = vault.percentage * 100 if vault else 0
        if airdrop:
            extension_bps += (airdrop.amount_tokens * 10_000 + DEFAULT_CLANKER_SUPPLY - 1) // DEFAULT_CLANKER_SUPPLY
        if extension_bps > 9_000:
            raise ValueError("Clanker vault and airdrop allocations cannot exceed 90% of supply.")
        return payload

    @staticmethod
    def build_draft_payload(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Build a route-neutral payload, preserving omitted wallet roles as JSON null."""
        primary = args[2] if len(args) > 2 else kwargs.get("primary_beneficiary")
        creator_recipient = kwargs.get("creator_reward_recipient")
        platform = args[3] if len(args) > 3 else kwargs.get("platform_address")
        fallback = primary or platform
        draft_args = list(args)
        if len(draft_args) > 2:
            draft_args[2] = fallback
        else:
            kwargs["primary_beneficiary"] = fallback
        kwargs["creator_reward_recipient"] = creator_recipient or fallback
        payload = Clanker.build_payload(*draft_args, **kwargs)
        platform_bps = int(args[4] if len(args) > 4 else kwargs["platform_bps"])
        creator_bps = 10_000 - platform_bps
        if not primary:
            payload["tokenAdmin"] = None
            if creator_bps:
                payload["rewards"]["recipients"][0]["admin"] = None
            vault_recipient = args[18] if len(args) > 18 else kwargs.get("vault_recipient")
            if payload.get("vault") and not vault_recipient:
                payload["vault"]["recipient"] = None
            airdrop_admin = args[10] if len(args) > 10 else kwargs.get("airdrop_admin")
            if payload.get("airdrop") and not airdrop_admin:
                payload["airdrop"]["admin"] = None
            if payload.get("devBuy"):
                payload["devBuy"]["recipient"] = None
        if not creator_recipient and creator_bps:
            payload["rewards"]["recipients"][0]["recipient"] = None
        return payload

    @staticmethod
    def build_launch_intent(
        guild_id: int, requester_id: int, launch_id: str, payload: Dict[str, Any],
        *, created_at: Optional[int] = None, expires_at: Optional[int] = None,
    ) -> ClankerLaunchIntent:
        pool_data = payload["pool"]
        fee_data = payload["fees"]
        paired_token = pool_data["pairedToken"]
        if paired_token == "WETH":
            paired_token = BASE_SEPOLIA_WETH
        pool = ClankerPool(
            paired_token=paired_token,
            tick_if_token0_is_clanker=int(pool_data["tickIfToken0IsClanker"]),
            tick_spacing=int(pool_data["tickSpacing"]),
            positions=tuple(
                ClankerPoolPosition(
                    int(item["tickLower"]), int(item["tickUpper"]),
                    int(item["positionBps"]),
                )
                for item in pool_data["positions"]
            ),
            fee_type=str(fee_data["type"]),
            clanker_fee_bps=int(fee_data["clankerFee"]),
            paired_fee_bps=int(fee_data["pairedFee"]),
        )
        rewards = tuple(
            ClankerReward(
                str(item["admin"]), str(item["recipient"]), int(item["bps"]),
                str(item["token"]),
            )
            for item in payload["rewards"]["recipients"]
        )
        vault_data = payload.get("vault")
        vault = ClankerVault(
            str(vault_data["recipient"]), int(vault_data["percentage"]),
            int(vault_data["lockupDuration"]), int(vault_data["vestingDuration"]),
        ) if vault_data else None
        airdrop_data = payload.get("airdrop")
        airdrop = ClankerAirdrop(
            str(airdrop_data["admin"]), str(airdrop_data["merkleRoot"]),
            int(airdrop_data["amount"]), int(airdrop_data["lockupDuration"]),
            int(airdrop_data["vestingDuration"]),
        ) if airdrop_data else None
        created_at = int(created_at or datetime.datetime.now(datetime.timezone.utc).timestamp())
        expires_at = int(expires_at or created_at + 900)
        return ClankerLaunchIntent.create(
            launch_id=launch_id, guild_id=guild_id, requester_id=requester_id,
            token_admin=str(payload["tokenAdmin"]), name=str(payload["name"]),
            symbol=str(payload["symbol"]), image=str(payload.get("image") or ""),
            metadata=payload.get("metadata") or {}, context=payload.get("context") or {},
            pool=pool, rewards=rewards, vault=vault, airdrop=airdrop,
            expected_native_value_wei=int((payload.get("devBuy") or {}).get("ethAmountWei") or 0),
            created_at=created_at, expires_at=expires_at,
        )

    @staticmethod
    def new_launch_id(symbol: str) -> str:
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"{symbol.lower()}-{timestamp}-{secrets.token_hex(3)}"

    @staticmethod
    def build_audit_record(
        requester: Any,
        payload: Dict[str, Any],
        guild_id: int,
        status: str = "dry_run",
    ) -> Dict[str, Any]:
        airdrop = payload.get("airdrop") or {}
        vault = payload.get("vault") or {}
        rewards = payload["rewards"]["recipients"]
        token_admin = str(payload.get("tokenAdmin") or "").lower()
        creator_reward = next(
            (item for item in rewards if str(item.get("admin") or "").lower() == token_admin), {}
        )
        platform_reward = next((item for item in reversed(rewards) if item is not creator_reward), {})
        launch_id = Clanker.new_launch_id(payload["symbol"])
        intent = Clanker.build_launch_intent(guild_id, requester.id, launch_id, payload)
        operation = clanker_deployment_operation(intent)
        return {
            "launch_id": launch_id,
            "payload_hash": intent.payload_hash,
            "intent": intent.to_dict(),
            "operation": operation.to_dict(),
            "created_at": utc_now(),
            "requester_id": requester.id,
            "requester_name": str(requester),
            "symbol": payload["symbol"],
            "name": payload["name"],
            "chain": "base-sepolia",
            "supply": str(DEFAULT_CLANKER_SUPPLY),
            "token_admin": payload.get("tokenAdmin"),
            "platform_treasury": platform_reward.get("recipient"),
            "creator_bps": creator_reward.get("bps", 0),
            "creator_reward_recipient": creator_reward.get("recipient"),
            "platform_bps": platform_reward.get("bps", 0),
            "vault_percentage": vault.get("percentage", 0),
            "vault_recipient": vault.get("recipient"),
            "airdrop_amount": airdrop.get("amount", 0),
            "airdrop_merkle_root": airdrop.get("merkleRoot"),
            "airdrop_proofs": airdrop.get("merkleExport"),
            "payload": payload,
            "status": status,
        }

    @staticmethod
    def build_draft_record(requester: Any, payload: Dict[str, Any], guild_id: int) -> Dict[str, Any]:
        """Persist a route-neutral template without creating an expiring transaction."""
        rewards = payload["rewards"]["recipients"]
        token_admin = str(payload.get("tokenAdmin") or "").lower()
        creator = next((item for item in rewards if item.get("admin") is None or (token_admin and str(item.get("admin") or "").lower() == token_admin)), {})
        platform = next((item for item in reversed(rewards) if item is not creator), {})
        platform_bps = int(platform.get("bps", 0))
        return {
            "launch_id": Clanker.new_launch_id(payload["symbol"]),
            "payload_hash": None, "intent": None, "operation": None,
            "created_at": utc_now(), "requester_id": requester.id,
            "requester_name": str(requester), "symbol": payload["symbol"],
            "name": payload["name"], "chain": "base-sepolia",
            "supply": str(DEFAULT_CLANKER_SUPPLY), "token_admin": payload.get("tokenAdmin"),
            "platform_treasury": platform.get("recipient"),
            "creator_bps": creator.get("bps", 0),
            "creator_reward_recipient": creator.get("recipient"),
            "platform_bps": platform.get("bps", 0),
            "vault_percentage": (payload.get("vault") or {}).get("percentage", 0),
            "vault_recipient": (payload.get("vault") or {}).get("recipient"),
            "airdrop_amount": (payload.get("airdrop") or {}).get("amount", 0),
            "airdrop_merkle_root": (payload.get("airdrop") or {}).get("merkleRoot"),
            "airdrop_proofs": (payload.get("airdrop") or {}).get("merkleExport"),
            "payload": copy.deepcopy(payload), "status": "dry_run",
        }

    @staticmethod
    def launch_reference(record: Dict[str, Any], audit_log: List[Dict[str, Any]]) -> str:
        """Return a compact reference unique to one requester and token symbol."""
        base = str(record.get("symbol") or "token").strip().lower()
        requester_id = int(record.get("requester_id", 0) or 0)
        siblings = [
            item for item in audit_log
            if int(item.get("requester_id", 0) or 0) == requester_id
            and str(item.get("symbol") or "").strip().lower() == base
        ]
        if siblings and siblings[0] is record:
            return base
        suffix = str(record.get("launch_id") or "").rsplit("-", 1)[-1][-4:]
        return f"{base}-{suffix}" if suffix else base

    @staticmethod
    def launch_record_line(
        record: Dict[str, Any], audit_log: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        launch_id = record.get("launch_ref") or (
            Clanker.launch_reference(record, audit_log)
            if audit_log is not None else record.get("launch_id", "legacy")
        )
        created = record.get("created_at", "unknown")
        status = record.get("status", "unknown")
        symbol = record.get("symbol", "?")
        requester = record.get("requester_name", record.get("requester_id", "?"))
        return f"{launch_id} · {created} · {status} · ${symbol} by {requester}"

    @staticmethod
    def launch_status_label(status: str) -> str:
        labels = {
            "awaiting_cryptowallet_approval": "⏳ Awaiting CryptoWallet approval",
            "internal_submitted": "⏳ Submitted — confirming",
            "internal_confirmed": "✅ Confirmed",
            "internal_failed": "❌ Failed",
            "internal_uncertain": "⚠️ Needs status recovery",
            "awaiting_external_wallet": "⏳ Awaiting external wallet",
            "external_pending": "⏳ External transaction pending",
            "external_confirmed": "✅ Confirmed",
        }
        return labels.get(status, status.replace("_", " ").title())

    @staticmethod
    def launch_list_embed(
        records: List[Dict[str, Any]], audit_log: List[Dict[str, Any]]
    ) -> discord.Embed:
        embed = discord.Embed(
            title="Your Clanker launch activity",
            description="Most recent first. Use the short reference with Clanker commands.",
            color=discord.Color.blue(),
        )
        for record in reversed(records):
            reference = record.get("launch_ref") or Clanker.launch_reference(record, audit_log)
            symbol = str(record.get("symbol") or "?").upper()
            status = Clanker.launch_status_label(str(record.get("status") or "unknown"))
            lines = [f"**Status:** {status}"]
            created = str(record.get("created_at") or "")
            try:
                moment = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
                exact = discord.utils.format_dt(moment, style="f")
                relative = discord.utils.format_dt(moment, style="R")
                lines.append(f"**Created:** {exact} ({relative})")
            except ValueError:
                if created:
                    lines.append(f"**Created:** {created}")
            server_name = str(record.get("history_guild_name") or "")
            if server_name:
                lines.append(f"**Server:** {server_name}")
            route = str(record.get("execution_route") or "").replace("_", " ").title()
            if route:
                lines.append(f"**Route:** {route}")
            token = record.get("token_address")
            if token:
                lines.append(
                    f"[Clanker](https://www.clanker.world/clanker/{token}) · "
                    f"[Contract](https://sepolia.basescan.org/address/{token})"
                )
            transaction = record.get("transaction_hash")
            if transaction:
                lines.append(f"[Transaction](https://sepolia.basescan.org/tx/{transaction})")
            embed.add_field(
                name="$" + symbol + "  •  " + reference,
                value="\n".join(lines),
                inline=False,
            )
        return embed

    async def check_launch_controls(self, ctx: commands.Context, settings: Dict[str, Any]) -> bool:
        launch_channel_id = settings.get("launch_channel_id")
        if launch_channel_id and ctx.channel.id != launch_channel_id:
            channel = ctx.guild.get_channel(launch_channel_id)
            destination = channel.mention if channel else f"channel ID {launch_channel_id}"
            await ctx.send(f"Clanker launches must be started in {destination}.")
            return False

        author_roles = {role.id for role in getattr(ctx.author, "roles", [])}
        blocked_role_id = settings.get("blocked_role_id")
        if blocked_role_id and blocked_role_id in author_roles:
            await ctx.send("Your role is blocked from creating Clanker launch requests in this server.")
            return False

        allowed_role_id = settings.get("allowed_role_id")
        if allowed_role_id and allowed_role_id not in author_roles:
            role = ctx.guild.get_role(allowed_role_id)
            role_name = role.mention if role else f"role ID {allowed_role_id}"
            await ctx.send(f"You need {role_name} to create Clanker launch requests in this server.")
            return False

        daily_max = int(settings.get("daily_max_per_user") or 0)
        if daily_max > 0:
            audit_log: List[Dict[str, Any]] = await self.config.guild(ctx.guild).audit_log()
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
            recent_count = 0
            for record in audit_log:
                if record.get("requester_id") != ctx.author.id:
                    continue
                try:
                    created = datetime.datetime.fromisoformat(str(record.get("created_at")))
                except ValueError:
                    continue
                if created >= cutoff:
                    recent_count += 1
            if recent_count >= daily_max:
                await ctx.send(f"You have reached the Clanker launch limit of {daily_max} per 24 hours.")
                return False

        cooldown_seconds = int(settings.get("launch_cooldown_seconds") or 0)
        if cooldown_seconds > 0:
            key = (ctx.guild.id, ctx.author.id)
            now = datetime.datetime.now(datetime.timezone.utc)
            previous = self.user_cooldowns.get(key)
            if previous:
                remaining = cooldown_seconds - int((now - previous).total_seconds())
                if remaining > 0:
                    await ctx.send(f"Please wait {remaining} seconds before creating another Clanker launch request.")
                    return False
            self.user_cooldowns[key] = now

        return True

    async def add_audit_record(self, guild: discord.Guild, record: Dict[str, Any]):
        async with self.config.guild(guild).audit_log() as audit_log:
            audit_log.append(record)
            record["launch_ref"] = self.launch_reference(record, audit_log)
            del audit_log[:-MAX_AUDIT_RECORDS]

    async def notify_approval_channel(self, guild: discord.Guild, settings: Dict[str, Any], record: Dict[str, Any]) -> None:
        channel_id = settings.get("approval_channel_id")
        if not channel_id:
            return
        channel = guild.get_channel(channel_id) or self.bot.get_channel(channel_id)
        if not channel:
            log.warning("Configured Clanker approval/log channel %s was not found in guild %s", channel_id, guild.id)
            return
        try:
            await channel.send("Clanker launch record created.", embed=self.launch_record_embed(record))
        except discord.HTTPException:
            log.exception("Failed to send Clanker launch record to channel %s", channel_id)

    @staticmethod
    def launch_record_embed(record: Dict[str, Any]) -> discord.Embed:
        """Render a readable launch receipt while retaining its audit facts."""
        reference = record.get("launch_ref") or record.get("launch_id", "legacy")
        status = str(record.get("status") or "unknown")
        symbol = str(record.get("symbol") or "?").upper()
        confirmed = status in {"internal_confirmed", "external_confirmed"}
        embed = discord.Embed(
            title="Clanker launch " + chr(36) + symbol,
            color=discord.Color.green() if confirmed else discord.Color.blue(),
        )

        def address_link(value: Any) -> str:
            address = str(value or "")
            if not ADDRESS_RE.fullmatch(address):
                return "Not set"
            compact = address[:8] + "\u2026" + address[-6:]
            return "[{}](https://sepolia.basescan.org/address/{})".format(compact, address)

        def bps(value: Any) -> str:
            try:
                amount = int(value)
            except (TypeError, ValueError):
                return "Unknown"
            return "{:g}%".format(amount / 100)

        embed.add_field(
            name="Token",
            value="{} ({}{})".format(
                record.get("name") or "Unnamed token", chr(36), symbol
            ),
            inline=False,
        )
        description = str(
            ((record.get("payload") or {}).get("metadata") or {}).get("description") or ""
        ).strip()
        if description:
            embed.add_field(name="Description", value=description, inline=False)
        embed.add_field(name="Status", value=Clanker.launch_status_label(status), inline=True)
        embed.add_field(name="Network", value="Base Sepolia", inline=True)
        try:
            supply = "{:,}".format(int(record.get("supply")))
        except (TypeError, ValueError):
            supply = str(record.get("supply") or "Unknown")
        embed.add_field(name="Supply", value=supply, inline=True)

        created = str(record.get("created_at") or "")
        try:
            moment = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
            created = "{} ({})".format(
                discord.utils.format_dt(moment, style="f"),
                discord.utils.format_dt(moment, style="R"),
            )
        except ValueError:
            created = created or "Unknown"
        embed.add_field(name="Created", value=created, inline=False)

        token_address = record.get("token_address")
        transaction_hash = record.get("transaction_hash")
        if token_address:
            embed.add_field(
                name="Token contract",
                value="`{}`\n[View on BaseScan](https://sepolia.basescan.org/address/{})".format(
                    token_address, token_address
                ),
                inline=False,
            )
            links = ["[View on Clanker](https://www.clanker.world/clanker/{})".format(token_address)]
            if transaction_hash:
                links.append("[View transaction](https://sepolia.basescan.org/tx/{})".format(transaction_hash))
            embed.add_field(name="Launch links", value=" \u00b7 ".join(links), inline=False)
        elif transaction_hash:
            embed.add_field(
                name="Transaction",
                value="[View on BaseScan](https://sepolia.basescan.org/tx/{})".format(transaction_hash),
                inline=False,
            )

        embed.add_field(name="Token administrator", value=address_link(record.get("token_admin")), inline=False)
        creator_treasury = record.get("creator_reward_recipient") or record.get("token_admin")
        embed.add_field(
            name="Creator rewards",
            value="{} \u2192 {}".format(bps(record.get("creator_bps")), address_link(creator_treasury)),
            inline=False,
        )
        embed.add_field(
            name="Platform rewards",
            value="{} \u2192 {}".format(
                bps(record.get("platform_bps")), address_link(record.get("platform_treasury"))
            ),
            inline=False,
        )
        dev_buy = (record.get("payload") or {}).get("devBuy") or {}
        buy_in_wei = int(dev_buy.get("ethAmountWei") or 0)
        if buy_in_wei:
            buy_lines = [
                "Spent: {}".format(_format_eth_wei(buy_in_wei)),
                "Recipient: {}".format(address_link(dev_buy.get("recipient") or record.get("token_admin"))),
            ]
            bought_atomic = record.get("creator_buy_in_tokens_atomic")
            if bought_atomic is not None:
                buy_lines.append(
                    "Tokens received: {} {}".format(
                        _format_token_atomic(int(bought_atomic)), chr(36) + symbol
                    )
                )
            else:
                buy_lines.append("Tokens received: Not available from reconciled receipt")
            embed.add_field(
                name="Creator buy-in", value="\n".join(buy_lines), inline=False
            )
        if record.get("vault_percentage"):
            vault = (record.get("payload") or {}).get("vault") or {}
            percentage = int(record.get("vault_percentage") or vault.get("percentage") or 0)
            vaulted_supply = int(record.get("supply") or 0) * percentage // 100
            lockup_seconds = int(vault.get("lockupDuration") or 0)
            vesting_seconds = int(vault.get("vestingDuration") or 0)
            vault_lines = [
                "Supply Percentage: {}% ({:,})".format(percentage, vaulted_supply),
                "Vault time: {}".format(_format_vault_duration(lockup_seconds)),
            ]
            block_timestamp = record.get("block_timestamp")
            if block_timestamp is not None:
                release_start = int(block_timestamp) + lockup_seconds
                if vesting_seconds:
                    fully_released = release_start + vesting_seconds
                    vault_lines.extend([
                        "Vesting: {}".format(_format_vault_duration(vesting_seconds)),
                        "Vesting starts: <t:{}:F> (<t:{}:R>)".format(release_start, release_start),
                        "Fully released: <t:{}:F> (<t:{}:R>)".format(fully_released, fully_released),
                    ])
                else:
                    vault_lines.extend([
                        "Vesting: Full unlock after vault time",
                        "Release date: <t:{}:F> (<t:{}:R>)".format(release_start, release_start),
                    ])
            else:
                vault_lines.append("Release date: Waiting for confirmed block time")
            vault_lines.append("Recipient: {}".format(
                address_link(record.get("vault_recipient") or record.get("token_admin"))
            ))
            embed.add_field(name="Vault", value="\n".join(vault_lines), inline=False)
        if record.get("airdrop_amount"):
            proof_export = record.get("airdrop_proofs") or {}
            proof_note = ""
            if proof_export:
                proof_note = " \u00b7 {} generated proofs".format(proof_export.get("recipient_count", 0))
            embed.add_field(
                name="Airdrop",
                value="{:,} tokens{}".format(int(record.get("airdrop_amount")), proof_note),
                inline=False,
            )
        operation_hash = str(record.get("user_operation_hash") or "")
        embed.add_field(
            name="Launch reference", value="`{}`".format(reference), inline=False
        )
        if operation_hash:
            compact_operation = operation_hash[:10] + "\u2026" + operation_hash[-8:]
            embed.add_field(
                name="Technical reference",
                value="User operation " + chr(96) + compact_operation + chr(96),
                inline=False,
            )
        image_url = str((record.get("payload") or {}).get("image") or "")
        if image_url.startswith("https://"):
            embed.set_thumbnail(url=image_url)
        requester = record.get("requester_name", record.get("requester_id", "?"))
        embed.set_footer(text="Requested by {} \u00b7 Base Sepolia testnet".format(requester))
        return embed

    async def reward_preflight_embed(
        self, records: List[Dict[str, Any]], *, portfolio: bool, offset: int = 0,
        include_snapshot: bool = False,
    ) -> Any:
        """Show exact claimable assets and one-approval claim costs."""
        snapshot = await reward_preflight(records, clanker_rpc)
        launches = snapshot["launches"]
        if not launches:
            raise RuntimeError("No confirmed Clanker launches were found.")

        title = "Clanker reward portfolio" if portfolio else "Clanker rewards • $" + launches[0]["symbol"]
        embed = discord.Embed(
            title=title,
            description=(
                "Current claimable balances are shown below. Claim every available asset in "
                "one CryptoWallet approval, or claim only WETH or the launched token."
            ),
            color=discord.Color.gold(),
        )
        image_url = str((records[0].get("payload") or {}).get("image") or "")
        if not portfolio and image_url.startswith("https://"):
            embed.set_thumbnail(url=image_url)
        balances = {
            (row["owner"], row["asset"]): int(row["amount_wei"])
            for row in snapshot["treasuries"]
        }
        gas_by_asset: Dict[str, int] = {}
        for row in snapshot["treasuries"]:
            gas_by_asset[row["asset"]] = gas_by_asset.get(row["asset"], 0) + int(row.get("claim_gas") or 0)

        for launch in launches[offset:offset + 10]:
            owners = list(dict.fromkeys((launch["creator"], launch["platform"])))
            weth = sum(balances.get((owner, WETH.lower()), 0) for owner in owners)
            token = sum(balances.get((owner, launch["token"]), 0) for owner in owners)
            lines = [
                "Claimable WETH: {:,.8f}".format(weth / 10**18),
                "Claimable ${}: {:,.8f}".format(launch["symbol"], token / 10**18),
            ]
            if launch["creator"] == launch["platform"]:
                lines.append("Destination: shared creator/platform treasury")
            else:
                lines.append("Destinations: creator and platform treasuries")
            embed.add_field(
                name="$" + launch["symbol"] + " • " + launch["reference"],
                value=chr(10).join(lines), inline=False,
            )

        gas_price = int(snapshot.get("gas_price_wei") or 0)
        all_gas = int(snapshot.get("claim_estimated_gas") or 0)
        if snapshot["treasuries"]:
            token = launches[0]["token"]
            cost_lines = [
                "Claim all: {:,} gas · {:.8f} ETH".format(all_gas, all_gas * gas_price / 10**18),
                "WETH only: {:,} gas · {:.8f} ETH".format(
                    gas_by_asset.get(WETH.lower(), 0), gas_by_asset.get(WETH.lower(), 0) * gas_price / 10**18
                ),
                "${} only: {:,} gas · {:.8f} ETH".format(
                    launches[0]["symbol"], gas_by_asset.get(token, 0), gas_by_asset.get(token, 0) * gas_price / 10**18
                ),
                "CryptoWallet requests CDP sponsorship; if sponsored, your wallet pays 0 ETH.",
            ]
        else:
            cost_lines = ["No claimable rewards, so no transaction or gas charge is needed."]
        embed.add_field(name="Estimated network cost", value=chr(10).join(cost_lines), inline=False)
        if not portfolio:
            embed.add_field(
                name="Launch reference",
                value=chr(96) + launches[0]["reference"] + chr(96) + " · use with Clanker commands",
                inline=False,
            )
        if portfolio and len(launches) > 10:
            page = offset // 10 + 1
            pages = (len(launches) + 9) // 10
            embed.add_field(name="Portfolio page", value="{} of {} · {} total launches".format(page, pages, len(launches)), inline=False)
        embed.set_footer(text="Balances and gas estimates checked when this card opened · Base Sepolia")
        return (embed, snapshot) if include_snapshot else embed

    async def external_reward_handoff(
        self, user: Any, record: Dict[str, Any], guild_id: int
    ) -> str:
        """Create a signed one-time companion handoff for one collection call."""
        if int(record.get("requester_id", 0) or 0) != int(user.id):
            raise ValueError("Only the launch requester can open this reward handoff.")
        current_collection = record.get("reward_collection") or {}
        if (current_collection.get("status") == "submitted"
                or current_collection.get("status") == "awaiting_external_wallet"
                and int(current_collection.get("expires_at") or 0) > int(datetime.datetime.now(datetime.timezone.utc).timestamp())):
            raise ValueError("This token already has an active reward collection.")
        token = str(record.get("token_address") or "").lower()
        admin = str(record.get("token_admin") or "").lower()
        if not ADDRESS_RE.fullmatch(token) or not ADDRESS_RE.fullmatch(admin):
            raise ValueError("The confirmed reward binding is invalid.")
        expires_at = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + 900
        call = collect_rewards_call(token)
        reference = str(record.get("launch_ref") or record["launch_id"])
        handoff = {"version": 1, "kind": "clanker-v4-reward-collection",
                   "requester_id": str(user.id), "expires_at": expires_at,
                   "intent": {"launch_id": str(record["launch_id"]), "reference": reference,
                              "token": {"address": token, "admin": admin,
                                        "name": str(record.get("name") or "Token"),
                                        "symbol": str(record.get("symbol") or "?")}},
                   "operation": {"chain_id": BASE_CHAIN_ID, "to": call["to"],
                                 "value": "0", "data": call["data"]},
                   "verification_command": "!clanker rewardverify " + reference + " <transaction_hash>"}
        url = await self.create_external_wallet_handoff(user, handoff)
        guild = self.bot.get_guild(int(guild_id))
        if guild is not None:
            async with self.config.guild(guild).audit_log() as audit_log:
                match = next(item for item in audit_log if str(item.get("launch_id")) == str(record["launch_id"]))
                match["reward_collection"] = {"status": "awaiting_external_wallet",
                    "route": "external", "token_address": token, "expires_at": expires_at}
        return url

    async def treasury_withdrawal_review(
        self, records: Dict[str, Any] | List[Dict[str, Any]]
    ) -> tuple[discord.Embed, List[Dict[str, str]], bool]:
        """Build a separately labeled, treasury-wide withdrawal review."""
        records = [records] if isinstance(records, dict) else list(records)
        if not records:
            raise ValueError("No launches were selected.")
        first_creator = str(records[0].get("creator_reward_recipient") or records[0].get("token_admin") or "").lower()
        first_platform = str(records[0].get("platform_treasury") or "").lower()
        first_admin = str(records[0].get("token_admin") or "").lower()
        compatible = [item for item in records
                      if str(item.get("token_admin") or "").lower() == first_admin
                      and str(item.get("creator_reward_recipient") or item.get("token_admin") or "").lower() == first_creator
                      and str(item.get("platform_treasury") or "").lower() == first_platform]
        snapshot = await reward_preflight(compatible, clanker_rpc)
        launches = snapshot["launches"]
        allowed = {first_creator, first_platform}
        claims = []
        seen = set()
        for row in snapshot["treasuries"]:
            key = (row["owner"], row["asset"])
            if row["owner"] in allowed and key not in seen:
                claims.append({"owner": row["owner"], "asset": row["asset"]})
                seen.add(key)
        symbols = {item["token"]: item["symbol"] for item in launches}
        embed = discord.Embed(
            title="Treasury-wide reward withdrawal",
            description=("This is separate from per-token collection. WETH balances are aggregated "
                         "by treasury across every Clanker token."), color=discord.Color.orange())
        rows = []
        creator_weth = 0
        for row in snapshot["treasuries"]:
            if row["owner"] not in allowed:
                continue
            role = (
                "Combined creator/platform"
                if first_creator == first_platform and row["owner"] == first_creator
                else "Creator" if row["owner"] == first_creator else "Platform"
            )
            asset = "WETH" if row["asset"] == WETH.lower() else "$" + symbols.get(row["asset"], row["asset"][:8])
            rows.append(role + " · " + asset + ": " + format(row["amount_wei"] / 10**18, ",.8f"))
            if row["asset"] == WETH.lower() and row["owner"] == first_creator:
                creator_weth += int(row["amount_wei"])
        embed.add_field(name="Exact deposited balances", value=chr(10).join(rows) if rows else "Nothing available", inline=False)
        gas = int(snapshot.get("claim_estimated_gas") or 0)
        fee = int(snapshot.get("claim_estimated_fee_wei") or 0)
        profitable = creator_weth > fee and creator_weth > 0
        embed.add_field(name="Withdrawal gas", value=(format(gas, ",") + " gas · " + format(fee / 10**18, ".8f") + " ETH network estimate"), inline=False)
        embed.add_field(name="Creator profitability signal", value=("Profitable from deposited WETH alone" if profitable else "Not profitable from deposited WETH alone; token rewards are unpriced"), inline=False)
        if len(compatible) != len(records):
            embed.add_field(name="Different authority groups", value=str(len(records) - len(compatible)) + " launch(es) require a separate signer/treasury review.", inline=False)
        embed.add_field(name="Authority", value=("CryptoWallet must prove the immutable token administrator. Creator funds go only to the creator treasury; platform funds go only to the platform treasury."), inline=False)
        embed.set_footer(text="Treasury-wide action · not an individual-coin claim")
        return embed, claims, profitable

    async def platform_withdrawal_review(
        self, records: List[Dict[str, Any]]
    ) -> tuple[discord.Embed, List[Dict[str, str]], bool]:
        """Review only the platform treasury across all guild launches."""
        snapshot = await reward_preflight(records, clanker_rpc)
        platform = str(records[0].get("platform_treasury") or "").lower()
        rows = [row for row in snapshot["treasuries"] if row["owner"] == platform]
        claims = [{"owner": row["owner"], "asset": row["asset"]} for row in rows]
        gas = sum(int(row.get("claim_gas") or 0) for row in rows)
        fee = gas * int(snapshot.get("gas_price_wei") or 0)
        weth = sum(int(row["amount_wei"]) for row in rows if row["asset"] == WETH.lower())
        profitable = weth > fee and weth > 0
        symbols = {item["token"]: item["symbol"] for item in snapshot["launches"]}
        values = [("WETH" if row["asset"] == WETH.lower() else "$" + symbols.get(row["asset"], row["asset"][:8]))
                  + ": " + format(row["amount_wei"] / 10**18, ",.8f") for row in rows]
        embed = discord.Embed(title="Platform treasury withdrawal",
            description="Platform balances only. This operation cannot include any creator treasury.",
            color=discord.Color.orange())
        embed.add_field(name="Exact deposited balances", value=chr(10).join(values) if values else "Nothing available", inline=False)
        embed.add_field(name="Gas and profitability", value=(format(fee / 10**18, ".8f") + " ETH · "
            + ("profitable from WETH alone" if profitable else "not profitable from WETH alone")), inline=False)
        return embed, claims, profitable

    async def withdraw_platform_treasury_internal(
        self, user: Any, record: Dict[str, Any], claims: List[Dict[str, str]], guild_id: int
    ) -> Dict[str, Any]:
        if (record.get("reward_withdrawal") or {}).get("status") == "submitted":
            raise ValueError("A treasury withdrawal is already being reconciled.")
        platform = str(record.get("platform_treasury") or "").lower()
        if not claims or any(str(item.get("owner") or "").lower() != platform for item in claims):
            raise ValueError("Platform withdrawal cannot contain creator treasury claims.")
        wallet = self.bot.get_cog("CryptoWallet")
        withdraw = getattr(wallet, "clanker_withdraw_treasuries", None) if wallet else None
        if not callable(withdraw):
            raise RuntimeError("CryptoWallet treasury withdrawal is unavailable.")
        result = await withdraw(user, token_admin=str(record["token_admin"]),
            creator_treasury=str(record.get("creator_reward_recipient") or record["token_admin"]),
            platform_treasury=platform, claims=claims, attempt_id=secrets.token_urlsafe(18),
            platform_only=True)
        await self._record_treasury_submission(guild_id, user.id, record, claims, result, True)
        return result

    async def withdraw_launch_treasuries_internal(
        self, user: Any, record: Dict[str, Any], claims: List[Dict[str, str]], guild_id: int
    ) -> Dict[str, Any]:
        if (record.get("reward_withdrawal") or {}).get("status") == "submitted":
            raise ValueError("A treasury withdrawal is already being reconciled.")
        if int(record.get("requester_id", 0) or 0) != int(user.id):
            raise ValueError("Only the launch requester can approve this withdrawal.")
        if not claims:
            raise ValueError("No deposited treasury rewards are currently available.")
        wallet = self.bot.get_cog("CryptoWallet")
        withdraw = getattr(wallet, "clanker_withdraw_treasuries", None) if wallet else None
        if not callable(withdraw):
            raise RuntimeError("CryptoWallet treasury withdrawal is unavailable.")
        result = await withdraw(
            user, token_admin=str(record["token_admin"]),
            creator_treasury=str(record.get("creator_reward_recipient") or record["token_admin"]),
            platform_treasury=str(record["platform_treasury"]), claims=claims,
            attempt_id=secrets.token_urlsafe(18),
        )
        await self._record_treasury_submission(guild_id, user.id, record, claims, result, False)
        return result

    async def _record_treasury_submission(
        self, guild_id: int, user_id: int, record: Dict[str, Any], claims: List[Dict[str, str]],
        result: Dict[str, Any], platform_only: bool,
    ) -> None:
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            raise RuntimeError("The launch server is unavailable.")
        withdrawal = {"status": "submitted", "provider_status": result.get("provider_status"),
            "user_operation_hash": result.get("user_operation_hash"),
            "transaction_hash": result.get("transaction_hash"), "claims": copy.deepcopy(claims),
            "platform_only": bool(platform_only), "submitted_by": int(user_id), "submitted_at": utc_now()}
        async with self.config.guild(guild).audit_log() as audit_log:
            match = next(item for item in audit_log if str(item.get("launch_id")) == str(record["launch_id"]))
            match["reward_withdrawal"] = withdrawal
        self._start_treasury_confirmation_task(int(guild_id), int(user_id), str(record["launch_id"]))

    async def collect_launch_rewards_internal(
        self, user: Any, record: Dict[str, Any], guild_id: int
    ) -> Dict[str, Any]:
        """Collect exactly one launch after CryptoWallet proves the token administrator."""
        if int(record.get("requester_id", 0) or 0) != int(user.id):
            raise ValueError("Only the launch requester can start this reward review.")
        if record.get("status") not in {"internal_confirmed", "external_confirmed"}:
            raise ValueError("Only a confirmed launch can collect rewards.")
        current_collection = record.get("reward_collection") or {}
        if (current_collection.get("status") == "submitted"
                or current_collection.get("status") == "awaiting_external_wallet"
                and int(current_collection.get("expires_at") or 0) > int(datetime.datetime.now(datetime.timezone.utc).timestamp())):
            raise ValueError("This token already has an active reward collection.")
        token = str(record.get("token_address") or "").lower()
        admin = str(record.get("token_admin") or "").lower()
        if not ADDRESS_RE.fullmatch(token) or not ADDRESS_RE.fullmatch(admin):
            raise ValueError("The confirmed launch has invalid reward bindings.")
        wallet = self.bot.get_cog("CryptoWallet")
        collect = getattr(wallet, "clanker_collect_rewards", None) if wallet else None
        if not callable(collect):
            raise RuntimeError("CryptoWallet reward collection is unavailable.")
        result = await collect(
            user, token=token, token_admin=admin,
            attempt_id=secrets.token_urlsafe(18),
        )
        if str(result.get("provider_status") or "") not in {"pending", "signed", "broadcast", "complete"}:
            raise RuntimeError("CryptoWallet returned an invalid reward collection status.")
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            raise RuntimeError("The launch server is unavailable.")
        collection = {"status": "submitted", "route": "internal",
                      "provider_status": result.get("provider_status"),
                      "user_operation_hash": result.get("user_operation_hash"),
                      "transaction_hash": result.get("transaction_hash"),
                      "token_address": token, "submitted_at": utc_now()}
        async with self.config.guild(guild).audit_log() as audit_log:
            matches = [item for item in audit_log if str(item.get("launch_id")) == str(record.get("launch_id"))]
            if len(matches) != 1:
                raise RuntimeError("The launch changed before collection could be recorded.")
            matches[0]["reward_collection"] = collection
        self._start_reward_confirmation_task(int(guild_id), int(user.id), str(record["launch_id"]))
        return result

    async def launch_verified_internal(self, user: Any, record: Dict[str, Any]) -> Dict[str, Any]:
        """Submit the exact verified card through CryptoWallet delegation."""
        wallet = self.bot.get_cog("CryptoWallet")
        submit = getattr(wallet, "clanker_launch_verified", None) if wallet else None
        if not callable(submit):
            raise RuntimeError("CryptoWallet verified Clanker launch is unavailable.")
        launch = record.get("intent")
        operation = record.get("operation")
        execution_terms = record.get("execution_terms")
        if (
            not isinstance(launch, dict)
            or not isinstance(operation, dict)
            or not isinstance(execution_terms, dict)
        ):
            raise ValueError("This verified card has no immutable operation or spending policy.")
        if (
            int(record.get("requester_id", 0)) != int(user.id)
            or str(record.get("launch_id")) != str(launch.get("launch_id"))
            or str(record.get("payload_hash", "")).lower()
            != str(launch.get("payload_hash", "")).lower()
            or str(operation.get("payload_hash", "")).lower()
            != str(record.get("payload_hash", "")).lower()
        ):
            raise ValueError("The verified launch binding is invalid.")
        result = await submit(user, launch, operation, execution_terms)
        expected = {
            "status", "intent_id", "payload_hash", "authorization_expires_at",
            "provider_status", "user_operation_hash", "transaction_hash",
        }
        allowed = {"authorization_required", "submitted", "confirmed", "uncertain"}
        if (
            not isinstance(result, dict) or set(result) != expected
            or result.get("status") not in allowed
            or str(result.get("payload_hash", "")).lower() == ""
        ):
            raise RuntimeError("CryptoWallet returned an invalid verified-launch result.")
        return result

    async def return_verified_draft_to_editing(
        self, guild: discord.Guild, user: Any, launch_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Persist an unsubmitted verification as the same editable draft."""
        async with self.config.guild(guild).audit_log() as audit_log:
            matches = [
                (index, item) for index, item in enumerate(audit_log)
                if str(item.get("launch_id")) == launch_id
            ]
            if len(matches) != 1:
                raise RuntimeError("The verified Clanker record changed before editing.")
            index, record = matches[0]
            if (record.get("status") != "verified"
                    or int(record.get("requester_id", 0)) != int(user.id)):
                raise RuntimeError(
                    "Only an unsubmitted verified launch can return to editing."
                )
            editable = self.build_draft_record(user, payload, guild.id)
            editable["launch_id"] = record["launch_id"]
            editable["launch_ref"] = record.get("launch_ref")
            editable["created_at"] = record.get("created_at")
            audit_log[index] = editable
            return copy.deepcopy(editable)

    async def mark_verified_internal_result(
        self, guild: discord.Guild, launch_id: str, result: Dict[str, Any]
    ) -> None:
        """Persist the bounded result of a verified-card launch attempt."""
        async with self.config.guild(guild).audit_log() as audit_log:
            matches = [item for item in audit_log if str(item.get("launch_id")) == launch_id]
            if len(matches) != 1:
                raise RuntimeError("The verified Clanker record changed during launch.")
            record = matches[0]
            if record.get("status") != "verified":
                raise RuntimeError("This Clanker card is no longer awaiting launch.")
            if result["status"] == "authorization_required":
                record["authorization_requested_at"] = int(
                    datetime.datetime.now(datetime.timezone.utc).timestamp()
                )
                return
            record.update({
                "status": "internal_" + result["status"],
                "execution_route": "internal",
                "signing_intent_id": result["intent_id"],
                "signing_payload_hash": result["payload_hash"],
                "provider_status": result.get("provider_status"),
                "user_operation_hash": result.get("user_operation_hash"),
                "transaction_hash": result.get("transaction_hash"),
            })

    async def refresh_internal_wallet_status(self, user: Any, record: Dict[str, Any]) -> Dict[str, Any]:
        """Read one exactly bound persisted CryptoWallet lifecycle state."""
        wallet = self.bot.get_cog("CryptoWallet")
        get_status = getattr(wallet, "clanker_internal_status", None) if wallet else None
        if not callable(get_status):
            raise RuntimeError("CryptoWallet Clanker status is unavailable.")
        intent_id = str(record.get("signing_intent_id") or "")
        payload_hash = str(record.get("signing_payload_hash") or "")
        if record.get("execution_route") != "internal" or not intent_id or not payload_hash:
            raise RuntimeError("This launch has no bound internal-wallet lifecycle.")
        result = await get_status(user, intent_id, payload_hash)
        expected = {"route", "signing_intent_id", "signing_payload_hash", "status",
                    "provider_status", "attempt_id", "user_operation_hash",
                    "transaction_hash", "block_number"}
        allowed = {"pending", "processing", "uncertain", "rejected", "expired",
                   "submitted", "confirmed", "failed"}
        if (not isinstance(result, dict) or set(result) != expected
                or result.get("route") != "internal"
                or result.get("signing_intent_id") != intent_id
                or str(result.get("signing_payload_hash", "")).lower() != payload_hash.lower()
                or result.get("status") not in allowed):
            raise RuntimeError("CryptoWallet returned an invalid Clanker lifecycle state.")
        return result

    def _start_reward_confirmation_task(
        self, guild_id: int, user_id: int, launch_id: str
    ) -> None:
        task = self.bot.loop.create_task(
            self._track_reward_confirmation(guild_id, user_id, launch_id)
        )
        self.confirmation_tasks.add(task)
        task.add_done_callback(self.confirmation_tasks.discard)

    async def _track_reward_confirmation(
        self, guild_id: int, user_id: int, launch_id: str
    ) -> None:
        await asyncio.sleep(15)
        for delay in (20, 30, 45, 60, 90, 120, 180, 300, 300):
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            record = await self.get_launch_record(guild, launch_id)
            collection = (record or {}).get("reward_collection") or {}
            if collection.get("status") != "submitted":
                return
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            wallet = self.bot.get_cog("CryptoWallet")
            status_call = getattr(wallet, "clanker_reward_status", None) if wallet else None
            if not callable(status_call):
                await asyncio.sleep(delay)
                continue
            try:
                result = await status_call(
                    user, token=str(record["token_address"]),
                    token_admin=str(record["token_admin"]),
                    user_operation_hash=str(collection["user_operation_hash"]),
                )
                provider_status = str(result.get("provider_status") or "")
                if provider_status in {"dropped", "failed"}:
                    reconciled = {"status": "failed", **result}
                elif provider_status == "complete" and result.get("transaction_hash"):
                    recipients = (record.get("payload") or {}).get("rewards", {}).get("recipients") or []
                    reconciled = await reconcile_collection_receipt(
                        str(result["transaction_hash"]), str(record["token_address"]),
                        len(recipients), clanker_rpc,
                    )
                    if reconciled.get("status") == "pending":
                        await asyncio.sleep(delay)
                        continue
                    reconciled["provider_status"] = provider_status
                    reconciled["user_operation_hash"] = result.get("user_operation_hash")
                else:
                    await asyncio.sleep(delay)
                    continue
                async with self.config.guild(guild).audit_log() as audit_log:
                    match = next(item for item in audit_log if str(item.get("launch_id")) == launch_id)
                    match["reward_collection"] = reconciled
                symbol = str(record.get("symbol") or "token").upper()
                if reconciled["status"] == "confirmed":
                    embed = discord.Embed(title="$" + symbol + " rewards collected",
                        description="Exact amounts reconciled from the confirmed ClaimedRewards event.",
                        color=discord.Color.green())
                    asset0 = "$" + symbol if reconciled["asset0"] == str(record["token_address"]).lower() else "WETH"
                    asset1 = "$" + symbol if reconciled["asset1"] == str(record["token_address"]).lower() else "WETH"
                    for index, recipient in enumerate(recipients):
                        role = "Creator" if index == 0 else "Platform" if index == len(recipients) - 1 else "Recipient " + str(index + 1)
                        embed.add_field(name=role + " · " + str(recipient.get("recipient") or "")[:10] + "…",
                            value=(asset0 + ": " + format(reconciled["rewards0_wei"][index] / 10**18, ",.8f")
                                   + chr(10) + asset1 + ": " + format(reconciled["rewards1_wei"][index] / 10**18, ",.8f")), inline=False)
                    embed.add_field(name="Next step", value="These amounts are deposited in their treasuries. Any withdrawal is a separately reviewed treasury-wide action.", inline=False)
                    embed.add_field(name="Transaction", value="https://sepolia.basescan.org/tx/" + str(reconciled["transaction_hash"]), inline=False)
                    await user.send(embed=embed)
                else:
                    await user.send("$" + symbol + " reward collection failed. No withdrawal was recorded.")
                return
            except (KeyError, TypeError, ValueError, RuntimeError, discord.HTTPException):
                await asyncio.sleep(delay)

    def _start_treasury_confirmation_task(self, guild_id: int, user_id: int, launch_id: str) -> None:
        task = self.bot.loop.create_task(self._track_treasury_confirmation(guild_id, user_id, launch_id))
        self.confirmation_tasks.add(task)
        task.add_done_callback(self.confirmation_tasks.discard)

    async def _track_treasury_confirmation(self, guild_id: int, user_id: int, launch_id: str) -> None:
        await asyncio.sleep(15)
        for delay in (20, 30, 45, 60, 90, 120, 180, 300, 300):
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            record = await self.get_launch_record(guild, launch_id)
            withdrawal = (record or {}).get("reward_withdrawal") or {}
            if withdrawal.get("status") != "submitted":
                return
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            wallet = self.bot.get_cog("CryptoWallet")
            status_call = getattr(wallet, "clanker_treasury_status", None) if wallet else None
            if not callable(status_call):
                await asyncio.sleep(delay)
                continue
            try:
                result = await status_call(user, token_admin=str(record["token_admin"]),
                    creator_treasury=str(record.get("creator_reward_recipient") or record["token_admin"]),
                    platform_treasury=str(record["platform_treasury"]), claims=withdrawal["claims"],
                    user_operation_hash=str(withdrawal["user_operation_hash"]),
                    platform_only=bool(withdrawal.get("platform_only")))
                provider_status = str(result.get("provider_status") or "")
                if provider_status in {"dropped", "failed"}:
                    reconciled = {"status": "failed", **withdrawal, **result}
                elif provider_status == "complete" and result.get("transaction_hash"):
                    reconciled = await reconcile_withdrawal_receipt(
                        str(result["transaction_hash"]), withdrawal["claims"], clanker_rpc)
                    if reconciled.get("status") == "pending":
                        await asyncio.sleep(delay)
                        continue
                    reconciled.update({"provider_status": provider_status,
                        "user_operation_hash": result.get("user_operation_hash"),
                        "platform_only": bool(withdrawal.get("platform_only"))})
                else:
                    await asyncio.sleep(delay)
                    continue
                async with self.config.guild(guild).audit_log() as audit_log:
                    match = next(item for item in audit_log if str(item.get("launch_id")) == launch_id)
                    match["reward_withdrawal"] = reconciled
                if reconciled["status"] == "confirmed":
                    lines = ["Treasury withdrawal confirmed:"]
                    for item in reconciled["claims"]:
                        asset = "WETH" if item["asset"] == WETH.lower() else item["asset"][:10] + "…"
                        lines.append(asset + " → " + item["owner"][:10] + "…: " + format(item["amount_wei"] / 10**18, ",.8f"))
                    lines.append("https://sepolia.basescan.org/tx/" + reconciled["transaction_hash"])
                    await user.send(chr(10).join(lines))
                else:
                    await user.send("Clanker treasury withdrawal failed; no successful withdrawal was recorded.")
                return
            except (KeyError, TypeError, ValueError, RuntimeError, discord.HTTPException):
                await asyncio.sleep(delay)

    async def schedule_internal_confirmation(
        self, guild: discord.Guild, user: Any, record: Dict[str, Any],
        message: Optional[discord.Message] = None,
    ) -> None:
        """Track one submitted launch and persist only a public result-card destination."""
        async with self.config.guild(guild).audit_log() as audit_log:
            matches = [item for item in audit_log if str(item.get("launch_id")) == str(record["launch_id"])]
            if len(matches) != 1 or matches[0].get("status") != "internal_submitted":
                return
            is_ephemeral = bool(
                message and getattr(getattr(message, "flags", None), "ephemeral", False)
            )
            if message is not None and not is_ephemeral:
                matches[0]["confirmation_channel_id"] = int(message.channel.id)
                matches[0]["confirmation_message_id"] = int(message.id)
        self._start_confirmation_task(
            guild.id, int(user.id), str(record["launch_id"])
        )

    async def _track_internal_confirmation(
        self, guild_id: int, user_id: int, launch_id: str
    ) -> None:
        await asyncio.sleep(20)
        delays = (30, 45, 60, 90, 120, 180, 300, 300, 300, 900)
        attempt = 0
        while True:
            delay = delays[min(attempt, len(delays) - 1)]
            attempt += 1
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            record = await self.get_launch_record(guild, launch_id)
            if not record or record.get("status") not in {"internal_submitted", "internal_uncertain"}:
                return
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            try:
                result = await self.refresh_internal_wallet_status(user, record)
                await self._persist_internal_status(guild, launch_id, result)
            except (KeyError, TypeError, ValueError, RuntimeError):
                await asyncio.sleep(delay)
                continue
            if result["status"] in {"confirmed", "failed"}:
                await self._deliver_internal_result(guild, user, launch_id, result)
                return
            await asyncio.sleep(delay)

    async def _block_timestamp(self, block_number: int) -> int:
        block = await clanker_rpc("eth_getBlockByNumber", [hex(int(block_number)), False])
        if not isinstance(block, dict) or not block.get("timestamp"):
            raise RuntimeError("The confirmed Base Sepolia block timestamp is unavailable.")
        timestamp = int(str(block["timestamp"]), 16)
        if timestamp <= 0:
            raise RuntimeError("The confirmed Base Sepolia block timestamp is invalid.")
        return timestamp

    async def _backfill_confirmed_receipts(
        self, guild: discord.Guild, records: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        timestamps: Dict[str, int] = {}
        buy_tokens: Dict[str, int] = {}
        for record in records:
            if record.get("status") not in {"internal_confirmed", "external_confirmed"}:
                continue
            launch_id = str(record.get("launch_id") or "")
            if (
                record.get("vault_percentage") and record.get("block_number")
                and not record.get("block_timestamp")
            ):
                try:
                    timestamps[launch_id] = await self._block_timestamp(int(record["block_number"]))
                except (KeyError, TypeError, ValueError, RuntimeError):
                    log.exception("Could not backfill Clanker vault timestamp %s", launch_id)
            dev_buy = (record.get("payload") or {}).get("devBuy") or {}
            if (
                int(dev_buy.get("ethAmountWei") or 0) > 0
                and record.get("creator_buy_in_tokens_atomic") is None
                and record.get("transaction_hash") and record.get("token_address")
            ):
                try:
                    receipt = await clanker_rpc(
                        "eth_getTransactionReceipt", [str(record["transaction_hash"])]
                    )
                    if not isinstance(receipt, dict):
                        raise RuntimeError("receipt unavailable")
                    amount = _creator_buy_in_tokens(
                        receipt, str(record["token_address"]),
                        str(dev_buy.get("recipient") or record.get("token_admin")),
                        int(dev_buy["ethAmountWei"]),
                    )
                    if amount is not None:
                        buy_tokens[launch_id] = amount
                except (KeyError, TypeError, ValueError, RuntimeError):
                    log.exception("Could not backfill Clanker buy-in receipt %s", launch_id)
        if not timestamps and not buy_tokens:
            return records
        async with self.config.guild(guild).audit_log() as audit_log:
            for record in audit_log:
                launch_id = str(record.get("launch_id") or "")
                if launch_id in timestamps:
                    record["block_timestamp"] = timestamps[launch_id]
                if launch_id in buy_tokens:
                    record["creator_buy_in_tokens_atomic"] = buy_tokens[launch_id]
        refreshed = []
        for record in records:
            item = copy.deepcopy(record)
            launch_id = str(item.get("launch_id") or "")
            if launch_id in timestamps:
                item["block_timestamp"] = timestamps[launch_id]
            if launch_id in buy_tokens:
                item["creator_buy_in_tokens_atomic"] = buy_tokens[launch_id]
            refreshed.append(item)
        return refreshed

    async def _track_external_confirmation(
        self, guild_id: int, user_id: int, launch_id: str
    ) -> None:
        await asyncio.sleep(20)
        delays = (30, 45, 60, 90, 120, 180, 300, 300, 300, 900)
        attempt = 0
        while True:
            delay = delays[min(attempt, len(delays) - 1)]
            attempt += 1
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            record = await self.get_launch_record(guild, launch_id)
            if not record or record.get("status") != "external_pending":
                return
            try:
                result = await verify_external_operation(
                    str(record["transaction_hash"]), record["operation"], record["intent"]
                )
            except (KeyError, TypeError, ValueError, RuntimeError):
                await asyncio.sleep(delay)
                continue
            if not result["verified"]:
                await asyncio.sleep(delay)
                continue
            try:
                block_timestamp = await self._block_timestamp(result["block_number"])
            except (TypeError, ValueError, RuntimeError):
                log.exception("Could not resolve Clanker block timestamp %s", launch_id)
                block_timestamp = None
            async with self.config.guild(guild).audit_log() as audit_log:
                match = next(item for item in audit_log if str(item.get("launch_id")) == launch_id)
                match.update({
                    "status": "external_confirmed",
                    "transaction_hash": result["transaction_hash"],
                    "signer_address": result["signer_address"],
                    "block_number": result["block_number"],
                    "block_timestamp": block_timestamp,
                    "token_address": result["token_address"],
                })
                record = copy.deepcopy(match)
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            try:
                wallet = self.bot.get_cog("CryptoWallet")
                register = getattr(wallet, "clanker_register_verified_token", None) if wallet else None
                if callable(register):
                    await register(user, {
                        "contract_address": result["token_address"],
                        "symbol": str(record["symbol"]),
                        "name": str(record["name"]),
                        "decimals": 18,
                    })
                await user.send(
                    embed=self.launch_record_embed(record),
                    view=ClankerReceiptRewardsView(self, record, guild_id),
                )
            except (KeyError, TypeError, ValueError, RuntimeError, discord.HTTPException):
                log.exception("Could not deliver automatic external Clanker confirmation %s", launch_id)
            return

    async def _persist_internal_status(
        self, guild: discord.Guild, launch_id: str, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        async with self.config.guild(guild).audit_log() as audit_log:
            matches = [item for item in audit_log if str(item.get("launch_id")) == launch_id]
            if len(matches) != 1 or matches[0].get("execution_route") != "internal":
                raise RuntimeError("The internal launch record changed during confirmation.")
            record = matches[0]
            record.update({
                "status": "internal_" + result["status"],
                "provider_status": result.get("provider_status"),
                "attempt_id": result.get("attempt_id"),
                "user_operation_hash": result.get("user_operation_hash"),
                "transaction_hash": result.get("transaction_hash"),
                "block_number": result.get("block_number"),
            })
            if result["status"] == "confirmed":
                record.pop("dismissed_by_requester", None)
                record.pop("dismissed_at", None)
            return copy.deepcopy(record)

    async def _deliver_internal_result(
        self, guild: discord.Guild, user: Any, launch_id: str, result: Dict[str, Any]
    ) -> None:
        record = await self.get_launch_record(guild, launch_id)
        if not record:
            return
        if result["status"] == "confirmed" and result.get("transaction_hash"):
            verified = None
            try:
                verified = await verify_internal_receipt(
                    result["transaction_hash"], record["operation"], record["intent"]
                )
                async with self.config.guild(guild).audit_log() as audit_log:
                    match = next(
                        item for item in audit_log
                        if str(item.get("launch_id")) == launch_id
                    )
                    match.update(verified)
                record.update(verified)
                try:
                    block_timestamp = await self._block_timestamp(verified["block_number"])
                    async with self.config.guild(guild).audit_log() as audit_log:
                        match = next(
                            item for item in audit_log
                            if str(item.get("launch_id")) == launch_id
                        )
                        match["block_timestamp"] = block_timestamp
                    record["block_timestamp"] = block_timestamp
                except (KeyError, TypeError, ValueError, RuntimeError):
                    log.exception("Could not resolve Clanker block timestamp %s", launch_id)
            except (KeyError, TypeError, ValueError, RuntimeError):
                log.exception("Could not verify confirmed Clanker receipt %s", launch_id)
                async with self.config.guild(guild).audit_log() as audit_log:
                    match = next(
                        item for item in audit_log
                        if str(item.get("launch_id")) == launch_id
                    )
                    match["status"] = "internal_uncertain"
                    match["provider_status"] = "receipt_verification_failed"
                record["status"] = "internal_uncertain"
                record["provider_status"] = "receipt_verification_failed"
            if verified is not None:
                try:
                    wallet = self.bot.get_cog("CryptoWallet")
                    register = (
                        getattr(wallet, "clanker_register_verified_token", None)
                        if wallet else None
                    )
                    if not callable(register):
                        raise RuntimeError(
                            "CryptoWallet token registration is unavailable."
                        )
                    await register(user, {
                        "contract_address": verified["token_address"],
                        "symbol": str(record["symbol"]),
                        "name": str(record["name"]),
                        "decimals": 18,
                    })
                    record["wallet_registered"] = True
                except (KeyError, TypeError, ValueError, RuntimeError):
                    log.exception(
                        "Confirmed Clanker token %s could not be registered in CryptoWallet",
                        launch_id,
                    )
                    record["wallet_registered"] = False
        embed = self.launch_record_embed(record)
        color = discord.Color.green() if record["status"] == "internal_confirmed" else discord.Color.red()
        embed.color = color
        channel_id = int(record.get("confirmation_channel_id", 0) or 0)
        message_id = int(record.get("confirmation_message_id", 0) or 0)
        if channel_id and message_id:
            try:
                channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                message = await channel.fetch_message(message_id)
                await message.edit(embed=embed, view=ClankerReceiptRewardsView(self, record, int(getattr(guild, "id", 0) or 0)))
            except discord.HTTPException:
                log.exception("Could not update Clanker confirmation card %s", launch_id)
        try:
            await user.send(embed=embed, view=ClankerReceiptRewardsView(self, record, int(getattr(guild, "id", 0) or 0)))
        except discord.HTTPException:
            log.exception("Could not DM Clanker confirmation card %s", launch_id)


    async def get_launch_record(self, guild: discord.Guild, launch_id: str) -> Optional[Dict[str, Any]]:
        audit_log: List[Dict[str, Any]] = await self.config.guild(guild).audit_log()
        needle = launch_id.strip().lower()
        for record in reversed(audit_log):
            record_id = str(record.get("launch_id") or "").lower()
            if record_id == needle or record_id.startswith(needle):
                return record
        return None

    async def get_user_launch_record(
        self, guild: discord.Guild, user_id: int, reference: str
    ) -> Optional[Dict[str, Any]]:
        """Resolve a full internal ID or compact reference within one users records."""
        audit_log: List[Dict[str, Any]] = await self.config.guild(guild).audit_log()
        needle = reference.strip().lower()
        owned = [
            record for record in audit_log
            if int(record.get("requester_id", 0) or 0) == int(user_id)
        ]
        exact = [
            record for record in owned
            if str(record.get("launch_id") or "").lower() == needle
            or self.launch_reference(record, audit_log) == needle
        ]
        return exact[-1] if len(exact) == 1 else None

    async def resume_approval_launch(
        self, guild: discord.Guild, user: Any, launch_id: str,
    ) -> Dict[str, Any]:
        """Restore one never-submitted CryptoWallet approval to verified review."""
        async with self.config.guild(guild).audit_log() as audit_log:
            matches = [item for item in audit_log if str(item.get("launch_id")) == launch_id]
            if len(matches) != 1:
                raise RuntimeError("The CryptoWallet approval is missing or ambiguous.")
            record = matches[0]
            if (
                record.get("status") != "awaiting_cryptowallet_approval"
                or int(record.get("requester_id", 0)) != int(user.id)
            ):
                raise RuntimeError("Only your never-submitted CryptoWallet approval can be resumed.")
            if record.get("transaction_hash") or record.get("user_operation_hash"):
                raise RuntimeError(
                    "This launch has a submitted operation and cannot return to approval."
                )
            if not isinstance(record.get("payload"), dict):
                raise RuntimeError("The approval has no reusable Clanker payload.")
            if not isinstance(record.get("execution_terms"), dict):
                wallet = self.bot.get_cog("CryptoWallet")
                get_terms = getattr(wallet, "clanker_execution_terms", None) if wallet else None
                if not callable(get_terms):
                    raise RuntimeError("CryptoWallet Clanker spending policy is unavailable.")
                terms = get_terms(int((record.get("payload", {}).get("devBuy") or {}).get("ethAmountWei") or 0))
                if not isinstance(terms, dict):
                    raise RuntimeError("CryptoWallet returned an invalid Clanker spending policy.")
                record["execution_terms"] = terms
            record["status"] = "verified"
            record.pop("dismissed_by_requester", None)
            record.pop("dismissed_at", None)
        return await self.refresh_verified_draft(guild, user, launch_id)

    async def retry_failed_launch(
        self, guild: discord.Guild, user: Any, launch_id: str,
    ) -> Dict[str, Any]:
        """Copy one failed internal attempt into a new editable draft."""
        record = await self.get_user_launch_record(guild, user.id, launch_id)
        if not record or record.get("status") != "internal_failed":
            raise RuntimeError("Only your failed internal launch can be retried.")
        payload = copy.deepcopy(record.get("payload"))
        if not isinstance(payload, dict):
            raise RuntimeError("The failed launch has no reusable payload.")
        replacement = self.build_draft_record(user, payload, guild.id)
        replacement["retried_from"] = str(record["launch_id"])
        await self.add_audit_record(guild, replacement)
        return copy.deepcopy(replacement)

    async def dismiss_failed_launch(
        self, guild: discord.Guild, user: Any, launch_id: str,
    ) -> None:
        """Hide one failed internal attempt while retaining its audit record."""
        async with self.config.guild(guild).audit_log() as audit_log:
            matches = [item for item in audit_log if str(item.get("launch_id")) == launch_id]
            if len(matches) != 1:
                raise RuntimeError("The failed launch attempt is missing or ambiguous.")
            record = matches[0]
            if record.get("status") != "internal_failed" or int(record.get("requester_id", 0)) != int(user.id):
                raise RuntimeError("Only your failed internal launch can be removed.")
            record["dismissed_by_requester"] = True
            record["dismissed_at"] = utc_now()

    async def delete_user_drafts(
        self, guild: discord.Guild, user: Any, launch_ids: List[str],
    ) -> int:
        """Delete only requester-owned records that never entered a wallet route."""
        requested = {str(item) for item in launch_ids}
        if not requested:
            raise ValueError("No Clanker drafts were selected.")
        async with self.config.guild(guild).audit_log() as audit_log:
            matches = [(index, item) for index, item in enumerate(audit_log)
                       if str(item.get("launch_id")) in requested]
            if len(matches) != len(requested):
                raise RuntimeError("One or more selected drafts no longer exist.")
            if any(int(item.get("requester_id", 0)) != int(user.id)
                   or item.get("status") not in {"dry_run", "verified"}
                   for _, item in matches):
                raise RuntimeError("Only your unsubmitted drafts can be deleted.")
            for index, _ in reversed(matches):
                del audit_log[index]
            return len(matches)

    async def replace_saved_draft(
        self, guild: discord.Guild, user: Any, launch_id: str,
        replacement: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update one editable draft in place so resuming cannot create duplicates."""
        async with self.config.guild(guild).audit_log() as audit_log:
            matches = [
                (index, item) for index, item in enumerate(audit_log)
                if str(item.get("launch_id")) == launch_id
            ]
            if len(matches) != 1:
                raise RuntimeError("The saved Clanker draft is missing or ambiguous.")
            index, current = matches[0]
            if current.get("status") != "dry_run" or int(current.get("requester_id", 0)) != int(user.id):
                raise RuntimeError("Only your editable draft can be replaced.")
            replacement = copy.deepcopy(replacement)
            replacement["launch_id"] = current["launch_id"]
            replacement["launch_ref"] = current.get("launch_ref")
            replacement["created_at"] = current.get("created_at")
            audit_log[index] = replacement
            return copy.deepcopy(replacement)

    async def refresh_verified_draft(
        self, guild: discord.Guild, user: Any, launch_id: str,
    ) -> Dict[str, Any]:
        """Renew a verified draft's signing window without changing launch values."""
        async with self.config.guild(guild).audit_log() as audit_log:
            matches = [item for item in audit_log if str(item.get("launch_id")) == launch_id]
            if len(matches) != 1:
                raise RuntimeError("The verified Clanker draft is missing or ambiguous.")
            record = matches[0]
            if record.get("status") != "verified" or int(record.get("requester_id", 0)) != int(user.id):
                raise RuntimeError("Only your unsubmitted verified draft can be reopened.")
            payload = copy.deepcopy(record.get("payload"))
            if not isinstance(payload, dict):
                raise RuntimeError("The verified Clanker draft has no immutable payload.")
            now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            intent = self.build_launch_intent(
                guild.id, user.id, launch_id, payload,
                created_at=now, expires_at=now + 900,
            )
            record["execution_created_at"] = now
            record["execution_expires_at"] = now + 900
            record["payload_hash"] = intent.payload_hash
            record["intent"] = intent.to_dict()
            record["operation"] = clanker_deployment_operation(intent).to_dict()
            return copy.deepcopy(record)

    async def prepare_draft_execution(
        self, guild: discord.Guild, user: Any, launch_id: str, signer_address: Optional[str]
    ) -> Dict[str, Any]:
        """Issue a fresh immutable execution window for an unchanged saved draft."""
        async with self.config.guild(guild).audit_log() as audit_log:
            matches = [item for item in audit_log if str(item.get("launch_id")) == launch_id]
            if len(matches) != 1:
                raise RuntimeError("The saved Clanker draft is missing or ambiguous.")
            record = matches[0]
            if record.get("status") not in {"dry_run", "awaiting_external_wallet", "external_pending"} or int(record.get("requester_id", 0)) != int(user.id):
                raise RuntimeError("The saved Clanker draft cannot enter a new execution route.")
            payload = copy.deepcopy(record.get("payload"))
            if not isinstance(payload, dict):
                raise RuntimeError("The saved Clanker draft has no immutable payload.")
            needs_signer = payload.get("tokenAdmin") is None or any(
                item.get("admin") is None or item.get("recipient") is None
                for item in (payload.get("rewards") or {}).get("recipients", [])
            ) or (bool(payload.get("vault")) and payload["vault"].get("recipient") is None) or (bool(payload.get("airdrop")) and payload["airdrop"].get("admin") is None) or (bool(payload.get("devBuy")) and payload["devBuy"].get("recipient") is None)
            if needs_signer and not is_eth_address(str(signer_address or "")):
                raise RuntimeError("This draft needs a valid execution wallet to resolve its blank wallet fields.")
            signer_address = str(signer_address or "").lower()
            if payload.get("tokenAdmin") is None:
                payload["tokenAdmin"] = signer_address.lower()
            recipients = (payload.get("rewards") or {}).get("recipients") or []
            platform_treasury = str(record.get("platform_treasury") or "").lower()
            for reward in recipients:
                is_platform = (str(reward.get("admin") or "").lower() == platform_treasury
                               and str(reward.get("recipient") or "").lower() == platform_treasury)
                if not is_platform:
                    if reward.get("admin") is None:
                        reward["admin"] = signer_address
                    if reward.get("recipient") is None:
                        reward["recipient"] = signer_address
            if payload.get("vault") and payload["vault"].get("recipient") is None:
                payload["vault"]["recipient"] = signer_address
            if payload.get("airdrop") and payload["airdrop"].get("admin") is None:
                payload["airdrop"]["admin"] = signer_address
            if payload.get("devBuy") and payload["devBuy"].get("recipient") is None:
                payload["devBuy"]["recipient"] = signer_address
            intent = self.build_launch_intent(
                guild.id, user.id, launch_id, payload,
                created_at=record.get("execution_created_at"),
                expires_at=record.get("execution_expires_at"),
            )
            operation = clanker_deployment_operation(intent)
            record["payload"] = payload
            record["token_admin"] = payload["tokenAdmin"]
            creator_reward = next((item for item in recipients if not (str(item.get("admin") or "").lower() == platform_treasury and str(item.get("recipient") or "").lower() == platform_treasury)), {})
            record["creator_reward_recipient"] = creator_reward.get("recipient")
            record["payload_hash"] = intent.payload_hash
            record["intent"] = intent.to_dict()
            record["operation"] = operation.to_dict()
            return dict(record)

    async def estimate_launch_network_fee(
        self, operation: Dict[str, Any], signer_address: str
    ) -> Dict[str, int]:
        """Estimate gas for the exact prepared launch at the current network fee rate."""
        call = {
            "from": str(signer_address),
            "to": str(operation["to"]),
            "data": str(operation["data"]),
            "value": hex(int(operation.get("value", 0))),
        }
        gas_price_raw = await clanker_rpc("eth_gasPrice", [])
        gas_price_wei = int(str(gas_price_raw), 16)
        try:
            estimated_gas_raw = await clanker_rpc("eth_estimateGas", [call])
            estimated_gas = int(str(estimated_gas_raw), 16)
            estimate_kind = "simulation"
        except (TypeError, ValueError, RuntimeError) as exc:
            estimated_gas = 8_000_000
            estimate_kind = "safety_ceiling"
            log.warning(
                "Clanker launch gas simulation failed; using the reviewed gas ceiling (%s)",
                type(exc).__name__,
            )
        return {
            "estimated_gas": estimated_gas,
            "gas_price_wei": gas_price_wei,
            "estimated_fee_wei": estimated_gas * gas_price_wei,
            "estimate_kind": estimate_kind,
        }

    async def mark_draft_verified(
        self, guild: discord.Guild, user: Any, launch_id: str
    ) -> Dict[str, Any]:
        """Freeze one freshly materialized draft for its Discord review card."""
        async with self.config.guild(guild).audit_log() as audit_log:
            matches = [item for item in audit_log if str(item.get("launch_id")) == launch_id]
            if len(matches) != 1:
                raise RuntimeError("The Clanker draft changed during verification.")
            record = matches[0]
            if (record.get("status") != "dry_run"
                    or int(record.get("requester_id", 0)) != int(user.id)
                    or not record.get("payload_hash")
                    or not isinstance(record.get("intent"), dict)
                    or not isinstance(record.get("operation"), dict)):
                raise RuntimeError("The Clanker draft cannot be verified.")
            record["status"] = "verified"
            record["verified_at"] = utc_now()
            return copy.deepcopy(record)

    def build_external_template(self, guild_id: int, requester_id: int, record: Dict[str, Any]) -> Dict[str, Any]:
        """Create a signed browser template whose null wallet roles default to its signer."""
        source = copy.deepcopy(record["payload"])
        fallback = str(record.get("platform_treasury") or "")
        materialized = copy.deepcopy(source)
        if materialized.get("tokenAdmin") is None:
            materialized["tokenAdmin"] = fallback
        platform_treasury = fallback.lower()
        for reward in materialized["rewards"]["recipients"]:
            is_platform = (str(reward.get("admin") or "").lower() == platform_treasury and str(reward.get("recipient") or "").lower() == platform_treasury)
            if not is_platform:
                if reward.get("admin") is None:
                    reward["admin"] = fallback
                if reward.get("recipient") is None:
                    reward["recipient"] = fallback
        if materialized.get("vault") and materialized["vault"].get("recipient") is None:
            materialized["vault"]["recipient"] = fallback
        if materialized.get("airdrop") and materialized["airdrop"].get("admin") is None:
            materialized["airdrop"]["admin"] = fallback
        intent = self.build_launch_intent(
            guild_id, requester_id, str(record["launch_id"]), materialized,
            created_at=int(record["execution_created_at"]),
            expires_at=int(record["execution_expires_at"]),
        ).to_dict()
        intent["payload_hash"] = None
        if source.get("tokenAdmin") is None:
            intent["token"]["admin"] = None
        for index, reward in enumerate(source["rewards"]["recipients"]):
            if reward.get("admin") is None:
                intent["rewards"][index]["admin"] = None
            if reward.get("recipient") is None:
                intent["rewards"][index]["recipient"] = None
        if source.get("vault") and source["vault"].get("recipient") is None:
            intent["vault"]["recipient"] = None
        if source.get("airdrop") and source["airdrop"].get("admin") is None:
            intent["airdrop"]["admin"] = None
        return intent

    async def _open_clanker_card(
        self, ctx: commands.Context, symbol: Optional[str] = None, name: Optional[str] = None
    ) -> None:
        settings = await self.settings_for_guild(ctx.guild)
        if not settings["enabled"]:
            await ctx.send("Clanker launch requests are disabled in this server.")
            return
        if not settings["treasury_address"]:
            await ctx.send("A bot owner must configure the SickGaming treasury address first.")
            return
        if not await self.check_launch_controls(ctx, settings):
            return
        normalized_symbol = None
        if symbol is not None:
            normalized_symbol = symbol.strip().upper().lstrip("$")
            if not SYMBOL_RE.fullmatch(normalized_symbol):
                await ctx.send("Token symbols must be 2-12 uppercase letters or numbers.")
                return
        normalized_name = name.strip() if name else None
        if normalized_name and len(normalized_name.encode("utf-8")) > 64:
            await ctx.send("Token names must be 1-64 UTF-8 bytes.")
            return
        creator_address = None
        view = ClankerDraftView(
            self, ctx, settings, symbol=normalized_symbol, name=normalized_name,
            creator_address=creator_address,
        )
        await ctx.send(embed=view.embed(), view=view)

    @commands.guild_only()
    @commands.command(name="clank")
    async def clank(
        self, ctx: commands.Context, symbol: str, *, name: Optional[str] = None
    ):
        """Start clanking a token with a ticker and optional token name."""
        await self._open_clanker_card(ctx, symbol, name)

    @commands.check(clanker_guild_or_dm_history)
    @commands.group(name="clanker", invoke_without_command=True, usage="")
    async def clanker(
        self, ctx: commands.Context, symbol: Optional[str] = None, *, name: Optional[str] = None
    ):
        """Manage Clanker token launches, drafts, rewards, and history.

        Clanker creates tokens and liquidity on Base. Start a new token with
        `[p]clank <symbol> [token name]`.
        """
        await ctx.send_help()

    @clanker.command(name="status")
    async def clanker_status(self, ctx: commands.Context):
        """Show Clanker status.

        Displays launch availability and this server's configured controls.
        """
        settings = await self.settings_for_guild(ctx.guild)
        try:
            companion_url = await self.companion_session_url()
        except RuntimeError:
            companion_url = "Not configured"
        embed = discord.Embed(title="Clanker status", color=discord.Color.blue())
        embed.add_field(name="Enabled", value=str(settings["enabled"]), inline=True)
        embed.add_field(name="Execution", value="Protected CryptoWallet or external-wallet handoff", inline=False)
        embed.add_field(name="Platform treasury", value=settings["treasury_address"] or "Not set", inline=False)
        embed.add_field(name="Platform split", value=f"{settings['platform_bps']} bps", inline=True)
        launch_channel = ctx.guild.get_channel(settings.get("launch_channel_id") or 0)
        approval_channel = ctx.guild.get_channel(settings.get("approval_channel_id") or 0)
        allowed_role = ctx.guild.get_role(settings.get("allowed_role_id") or 0)
        blocked_role = ctx.guild.get_role(settings.get("blocked_role_id") or 0)
        embed.add_field(name="Launch channel", value=launch_channel.mention if launch_channel else "Any", inline=True)
        embed.add_field(name="Approval/log channel", value=approval_channel.mention if approval_channel else "Not set", inline=True)
        embed.add_field(name="Allowed role", value=allowed_role.mention if allowed_role else "Any", inline=True)
        embed.add_field(name="Blocked role", value=blocked_role.mention if blocked_role else "None", inline=True)
        embed.add_field(name="Launch cooldown", value=f"{int(settings.get('launch_cooldown_seconds') or 0)}s", inline=True)
        daily_max = int(settings.get("daily_max_per_user") or 0)
        embed.add_field(name="Daily max", value=str(daily_max) if daily_max else "Unlimited", inline=True)
        embed.add_field(
            name="Vault",
            value=(
                f"{settings['vault_percentage']}% · lock {settings['vault_lockup_seconds']}s"
                if settings.get("vault_enabled") else "Disabled"
            ),
            inline=True,
        )
        embed.add_field(name="Shared companion", value=companion_url, inline=False)
        embed.add_field(
            name="Airdrop",
            value=(
                f"{settings['airdrop_amount']} tokens · lock {settings['airdrop_lockup_seconds']}s"
                if settings["airdrop_enabled"] and settings["airdrop_amount"]
                else "Disabled"
            ),
            inline=True,
        )
        embed.set_footer(text="Base Sepolia only · no private keys are stored by this cog")
        await ctx.send(embed=embed)

    @clanker.command(name="card", aliases=("create",))
    async def clanker_card(self, ctx: commands.Context):
        """Open an empty draft.

        Opens the interactive launch card for token, reward, vault, and airdrop details.
        """
        await self._open_clanker_card(ctx)

    @clanker.command(name="launch")
    async def clanker_launch(
        self,
        ctx: commands.Context,
        symbol: str,
        *,
        name: Optional[str] = None,
    ):
        """Open a prefilled draft.

        Starts the launch card with a ticker and optional token name already filled in.
        """
        await self._open_clanker_card(ctx, symbol, name)

    @clanker.command(name="audit")
    @checks.mod_or_permissions(manage_guild=True)
    async def clanker_audit(self, ctx: commands.Context, limit: commands.Range[int, 1, 20] = 10):
        """Show the audit log.

        Displays recent Clanker draft and launch records for server moderators.
        """
        audit_log: List[Dict[str, Any]] = await self.config.guild(ctx.guild).audit_log()
        if not audit_log:
            await ctx.send("No Clanker launch requests have been recorded.")
            return
        lines = [self.launch_record_line(r, audit_log) for r in reversed(audit_log[-limit:])]
        await ctx.send(box("\n".join(lines)))

    @clanker.command(name="drafts")
    async def clanker_drafts(self, ctx: commands.Context, limit: commands.Range[int, 1, 20] = 10):
        """List your drafts.

        Shows your editable and verified drafts that have not entered a wallet route.
        """
        if ctx.guild is None:
            audit_log = []
            for guild_id, guild_data in (await self.config.all_guilds()).items():
                guild_log = list(guild_data.get("audit_log") or [])
                guild = self.bot.get_guild(int(guild_id))
                for record in guild_log:
                    item = copy.deepcopy(record)
                    item["history_guild_name"] = (
                        guild.name if guild is not None else "Server {}".format(guild_id)
                    )
                    item["history_reference"] = self.launch_reference(record, guild_log)
                    audit_log.append(item)
            audit_log.sort(key=lambda item: str(item.get("created_at") or ""))
        else:
            audit_log = await self.config.guild(ctx.guild).audit_log()
        drafts = [
            record for record in audit_log
            if record.get("status") in {"dry_run", "verified"}
            and record.get("requester_id") == ctx.author.id
        ]
        if not drafts:
            await ctx.send("You have no saved Clanker drafts.")
            return
        embed = discord.Embed(
            title="Your Clanker drafts",
            description=(
                "Not submitted to a wallet. Editable drafts can be changed; verified drafts "
                "are ready for a final route choice."
            ),
            color=discord.Color.blurple(),
        )
        for record in reversed(drafts[-limit:]):
            reference = (
                record.get("launch_ref") or record.get("history_reference")
                or self.launch_reference(record, audit_log)
            )
            symbol = str(record.get("symbol") or "?").upper()
            status = (
                "✅ Verified — ready to launch"
                if record.get("status") == "verified" else "📝 Editable draft"
            )
            created = str(record.get("created_at") or "")
            try:
                moment = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
                created = "{} ({})".format(
                    discord.utils.format_dt(moment, style="f"),
                    discord.utils.format_dt(moment, style="R"),
                )
            except ValueError:
                created = created or "Unknown"
            details = "**Status:** " + status + "\n**Created:** " + created
            if record.get("history_guild_name"):
                details += "\n**Server:** " + str(record["history_guild_name"])
            embed.add_field(
                name=chr(36) + symbol + "  •  " + str(reference),
                value=details,
                inline=False,
            )
        if ctx.guild is None:
            embed.description = (
                "Not submitted to a wallet. Most recent first across your shared servers. "
                "Reopen or edit a draft from the server where it was created."
            )
            await ctx.send(embed=embed)
            return
        settings = await self.config.guild(ctx.guild).all()
        await ctx.send(
            embed=embed,
            view=ClankerDraftHistoryView(self, ctx, drafts[-limit:], settings),
        )

    @clanker.command(name="draft")
    async def clanker_draft(self, ctx: commands.Context, launch_id: str):
        """Open one draft.

        Reopens one of your saved drafts by its short launch reference.
        """
        record = await self.get_user_launch_record(ctx.guild, ctx.author.id, launch_id)
        if (
            not record
            or record.get("status") not in {"dry_run", "verified"}
            or record.get("requester_id") != ctx.author.id
        ):
            await ctx.send("No saved Clanker draft of yours matched that ID.")
            return
        await ctx.send(embed=self.launch_record_embed(record))

    @clanker.command(name="draftremove", aliases=("removedraft", "deletedraft"))
    async def clanker_draftremove(self, ctx: commands.Context, launch_id: str):
        """Delete one draft.

        Opens a confirmation before permanently deleting one of your unsubmitted drafts.
        """
        record = await self.get_user_launch_record(ctx.guild, ctx.author.id, launch_id)
        if not record or record.get("status") not in {"dry_run", "verified"}:
            await ctx.send("No removable Clanker draft of yours matched that reference.")
            return
        reference = record.get("launch_ref") or launch_id
        embed = discord.Embed(
            title="Delete Clanker draft?",
            description=(
                "**$" + "{} • {}**\nThis draft has not entered a wallet route. "
                "Deletion is permanent and does not affect any on-chain token."
            ).format(str(record.get("symbol") or "?").upper(), reference),
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed, view=ClankerDeleteDraftsView(
            self, ctx.guild, ctx.author.id, [str(record["launch_id"])]
        ))

    @clanker.command(name="draftsremoveall", aliases=("removealldrafts", "deletedrafts"))
    async def clanker_draftsremoveall(self, ctx: commands.Context):
        """Delete all drafts.

        Opens a confirmation before deleting all of your unsubmitted drafts.
        """
        audit_log: List[Dict[str, Any]] = await self.config.guild(ctx.guild).audit_log()
        drafts = [item for item in audit_log
                  if int(item.get("requester_id", 0)) == int(ctx.author.id)
                  and item.get("status") in {"dry_run", "verified"}]
        if not drafts:
            await ctx.send("You have no unsubmitted Clanker drafts to delete.")
            return
        embed = discord.Embed(
            title="Delete all Clanker drafts?",
            description=(
                "This will permanently delete **{}** unsubmitted draft{}. "
                "Submitted, pending, uncertain, failed, and confirmed launch activity "
                "cannot be removed by this action."
            ).format(len(drafts), "" if len(drafts) == 1 else "s"),
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed, view=ClankerDeleteDraftsView(
            self, ctx.guild, ctx.author.id, [str(item["launch_id"]) for item in drafts]
        ))

    @clanker.command(name="launches", aliases=("history", "records"))
    async def clanker_launches(self, ctx: commands.Context, limit: commands.Range[int, 1, 20] = 10):
        """List your launches.

        Shows launch attempts that entered an internal or external wallet route.
        """
        if ctx.guild is None:
            audit_log = []
            for guild_id, guild_data in (await self.config.all_guilds()).items():
                guild_log = list(guild_data.get("audit_log") or [])
                guild = self.bot.get_guild(int(guild_id))
                if guild is not None:
                    guild_log = await self._backfill_confirmed_receipts(guild, guild_log)
                for record in guild_log:
                    item = copy.deepcopy(record)
                    item["history_guild_name"] = (
                        guild.name if guild is not None else "Server {}".format(guild_id)
                    )
                    audit_log.append(item)
            audit_log.sort(key=lambda item: str(item.get("created_at") or ""))
        else:
            audit_log = await self.config.guild(ctx.guild).audit_log()
            audit_log = await self._backfill_confirmed_receipts(ctx.guild, audit_log)
        launches = [
            record for record in audit_log
            if record.get("status") not in {"dry_run", "verified"}
            and not record.get("dismissed_by_requester")
            and int(record.get("requester_id", 0) or 0) == int(ctx.author.id)
        ]
        if not launches:
            await ctx.send("No Clanker drafts have entered an execution route.")
            return
        embed = self.launch_list_embed(launches[-limit:], audit_log)
        if ctx.guild is None:
            embed.description = (
                "Most recent first across your shared servers. Reopen and manage a launch "
                "from the server where it was created."
            )
            await ctx.send(embed=embed)
            return
        settings = await self.config.guild(ctx.guild).all()
        await ctx.send(embed=embed, view=ClankerLaunchHistoryView(
            self, ctx, launches[-limit:], settings
        ))

    @clanker.command(name="dismiss")
    async def clanker_dismiss(self, ctx: commands.Context, launch_id: str):
        "Hide one inactive launch attempt from the requesters personal history."
        record = await self.get_user_launch_record(ctx.guild, ctx.author.id, launch_id)
        if not record:
            await ctx.send("No matching Clanker launch belongs to you.")
            return
        dismissible = {"awaiting_cryptowallet_approval", "internal_failed"}
        if record.get("status") not in dismissible:
            await ctx.send(
                "Only failed internal attempts or approvals that were never submitted can "
                "be dismissed. Pending, uncertain, external-wallet, and confirmed launches "
                "always remain visible."
            )
            return
        internal_id = str(record.get("launch_id") or "")
        async with self.config.guild(ctx.guild).audit_log() as audit_log:
            matches = [
                item for item in audit_log
                if str(item.get("launch_id") or "") == internal_id
                and int(item.get("requester_id", 0) or 0) == int(ctx.author.id)
            ]
            if len(matches) != 1:
                await ctx.send("That launch record changed before it could be dismissed.")
                return
            matches[0]["dismissed_by_requester"] = True
            matches[0]["dismissed_at"] = utc_now()
        reference = record.get("launch_ref") or self.launch_reference(record, [record])
        await ctx.send(
            f"Dismissed {reference} from your launch list. "
            "The server audit record was retained."
        )

    @clanker.command(name="rewardverify")
    async def clanker_rewardverify(
        self, ctx: commands.Context, launch_id: str, transaction_hash: str
    ):
        """Verify an external reward claim.

        Reconciles an external-wallet reward transaction for your token.
        """
        record = await self.get_user_launch_record(ctx.guild, ctx.author.id, launch_id)
        if not record:
            await ctx.send("No matching confirmed Clanker launch belongs to you.")
            return
        recipients = (record.get("payload") or {}).get("rewards", {}).get("recipients") or []
        try:
            result = await verify_external_collection(
                transaction_hash, str(record["token_address"]), str(record["token_admin"]),
                len(recipients), clanker_rpc,
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await ctx.send("Clanker could not verify that reward collection: " + str(exc))
            return
        async with self.config.guild(ctx.guild).audit_log() as audit_log:
            match = next(item for item in audit_log if str(item.get("launch_id")) == str(record["launch_id"]))
            match["reward_collection"] = {"route": "external", **result}
        if result["status"] == "pending":
            await ctx.send("That external collection is still pending; run this command again after confirmation.")
        else:
            await ctx.send("External reward collection " + chr(96) + result["status"] + chr(96)
                           + " and reconciled for " + chr(96)
                           + str(record.get("launch_ref") or record["launch_id"]) + chr(96) + ".")

    @clanker.command(name="claimall")
    async def clanker_claimall(self, ctx: commands.Context):
        """Review all rewards.

        DMs token selection, combined claims, and profitable treasury withdrawal review.
        """
        audit_log: List[Dict[str, Any]] = await self.config.guild(ctx.guild).audit_log()
        records = [
            item for item in audit_log
            if int(item.get("requester_id", 0) or 0) == int(ctx.author.id)
            and item.get("status") in {"internal_confirmed", "external_confirmed"}
            and item.get("token_address")
        ]
        if not records:
            await ctx.send("You have no confirmed Clanker launches with reward data.")
            return
        try:
            async with ctx.typing():
                embed = await self.reward_preflight_embed(records, portfolio=True)
                await ctx.author.send(embed=embed, view=ClankerClaimAllView(
                    self, records, ctx.author.id, ctx.guild.id
                ))
        except discord.Forbidden:
            await ctx.send("I could not DM your reward review. Enable DMs and try again.")
            return
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await ctx.send("Clanker rewards are temporarily unavailable: {}".format(exc))
            return
        await ctx.send("I sent your Clanker reward portfolio and gas preflight by DM.")

    @clanker.command(name="claimplatform")
    @commands.is_owner()
    async def clanker_claimplatform(self, ctx: commands.Context):
        """Review platform rewards.

        Reviews profitable platform-only treasury deposits across this server.
        """
        audit_log: List[Dict[str, Any]] = await self.config.guild(ctx.guild).audit_log()
        records = [item for item in audit_log
                   if item.get("status") in {"internal_confirmed", "external_confirmed"}
                   and item.get("token_address") and item.get("platform_treasury")]
        if not records:
            await ctx.send("No confirmed platform reward records are available.")
            return
        try:
            embed, claims, profitable = await self.platform_withdrawal_review(records)
            view = ClankerTreasuryWithdrawalView(
                self, records[0], claims, ctx.author.id, ctx.guild.id, platform_only=True
            ) if claims and profitable else None
            await ctx.author.send(embed=embed, view=view)
        except discord.Forbidden:
            await ctx.send("I could not DM the platform treasury review.")
            return
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await ctx.send("Platform reward review unavailable: " + str(exc))
            return
        await ctx.send("I sent the platform-only treasury review by DM.")

    @clanker.command(name="launchinfo", aliases=("record", "info"))
    @checks.mod_or_permissions(manage_guild=True)
    async def clanker_launchinfo(self, ctx: commands.Context, launch_id: str):
        """Show one launch.

        Displays the detailed receipt for one launch reference.
        """
        record = await self.get_launch_record(ctx.guild, launch_id)
        if not record:
            await ctx.send("No Clanker launch record matched that ID.")
            return
        await ctx.send(embed=self.launch_record_embed(record))

    @clanker.command(name="refresh")
    @commands.is_owner()
    async def clanker_refresh(self, ctx: commands.Context, launch_id: str):
        """Recover launch status.

        Reconciles a persisted CryptoWallet launch after approval or restart.
        """
        record = await self.get_user_launch_record(ctx.guild, ctx.author.id, launch_id)
        if not record or int(record.get("requester_id", 0)) != int(ctx.author.id):
            await ctx.send("No matching internal-wallet launch belongs to you.")
            return
        try:
            result = await self.refresh_internal_wallet_status(ctx.author, record)
            await self._persist_internal_status(
                ctx.guild, str(record["launch_id"]), result
            )
            if result["status"] in {"confirmed", "failed", "uncertain"}:
                await self._deliver_internal_result(
                    ctx.guild, ctx.author, str(record["launch_id"]), result
                )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await ctx.send(f"Clanker could not refresh CryptoWallet status: {exc}")
            return
        await ctx.send(f"CryptoWallet Clanker status: `{result['status']}`.")

    @clanker.command(name="external")
    async def clanker_external(self, ctx: commands.Context, launch_id: str):
        """Use an external wallet.

        DMs a requester-bound companion handoff for the exact launch transaction.
        """
        record = await self.get_user_launch_record(ctx.guild, ctx.author.id, launch_id)
        if not record:
            await ctx.send("No Clanker launch record matched that ID.")
            return
        if int(record.get("requester_id", 0)) != int(ctx.author.id):
            await ctx.send("Only the launch requester can choose its execution wallet.")
            return
        if record.get("status") != "dry_run":
            await ctx.send("That launch has already entered an execution route.")
            return
        execution_created_at = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        execution_expires_at = execution_created_at + 900
        template_record = {**record, "execution_created_at": execution_created_at,
                           "execution_expires_at": execution_expires_at}
        try:
            intent_template = self.build_external_template(
                ctx.guild.id, ctx.author.id, template_record
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await ctx.send(f"Clanker could not prepare the saved draft: {exc}")
            return
        handoff = {"version": 1, "kind": "clanker-v4-external-template",
                   "requester_id": str(ctx.author.id), "expires_at": execution_expires_at,
                   "intent": intent_template, "operation": None,
                   "verification_command": f"{ctx.clean_prefix}clanker verify {record['launch_id']} <transaction_hash>"}
        try:
            external_url = await self.create_external_wallet_handoff(ctx.author, handoff)
            await ctx.author.send(
                "Review and submit your exact Base Sepolia Clanker operation here:\n"
                + external_url
                + "\nThis protected link is short-lived and bound to your launch."
            )
        except discord.Forbidden:
            await ctx.send("I could not DM the external-wallet handoff. Enable DMs and try again.")
            return
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await ctx.send(f"Clanker could not create the external-wallet handoff: {exc}")
            return
        async with self.config.guild(ctx.guild).audit_log() as audit_log:
            matches = [item for item in audit_log if str(item.get("launch_id")) == str(record["launch_id"])]
            if len(matches) != 1 or matches[0].get("status") != "dry_run":
                await ctx.send("The launch changed before its external route could be saved.")
                return
            matches[0]["status"] = "awaiting_external_wallet"
            matches[0]["execution_route"] = "external"
            matches[0]["execution_created_at"] = execution_created_at
            matches[0]["execution_expires_at"] = execution_expires_at
            matches[0]["intent_template"] = intent_template
        await ctx.send("I sent the exact external-wallet operation and verification command by DM.")

    @clanker.command(name="verify")
    async def clanker_verify(self, ctx: commands.Context, launch_id: str, transaction_hash: str):
        """Verify an external launch.

        Checks a Base Sepolia transaction against the exact saved launch operation.
        """
        record = await self.get_user_launch_record(ctx.guild, ctx.author.id, launch_id)
        if not record or int(record.get("requester_id", 0)) != int(ctx.author.id):
            await ctx.send("No matching external-wallet launch belongs to you.")
            return
        if record.get("status") not in {"awaiting_external_wallet", "external_pending"}:
            await ctx.send("That launch is not awaiting external-wallet verification.")
            return
        bound_hash = str(record.get("transaction_hash") or "").lower()
        if bound_hash and bound_hash != str(transaction_hash).lower():
            await ctx.send("That launch is already bound to a different pending transaction.")
            return
        if record.get("status") == "external_pending" and bound_hash:
            await ctx.send(
                "That pending transaction is already bound. Clanker is reconciling it "
                "automatically; no additional verification request is needed."
            )
            return
        try:
            if not record.get("operation") or not record.get("intent"):
                transaction = await clanker_rpc("eth_getTransactionByHash", [transaction_hash])
                signer_address = str((transaction or {}).get("from") or "")
                if not is_eth_address(signer_address):
                    raise RuntimeError("The external transaction signer is not available yet.")
                record = await self.prepare_draft_execution(
                    ctx.guild, ctx.author, str(record["launch_id"]), signer_address
                )
            result = await verify_external_operation(transaction_hash, record["operation"], record["intent"])
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await ctx.send(f"External Clanker verification failed: {exc}")
            return
        if not result["verified"]:
            async with self.config.guild(ctx.guild).audit_log() as audit_log:
                for item in audit_log:
                    if str(item.get("launch_id")) == str(record["launch_id"]):
                        item["status"] = "external_pending"
                        item["transaction_hash"] = result["transaction_hash"]
            self._start_external_confirmation_task(
                ctx.guild.id, ctx.author.id, str(record["launch_id"])
            )
            await ctx.send(
                "That transaction is pending a Base Sepolia receipt. Clanker will reconcile "
                "it automatically; you do not need to run this command again."
            )
            return
        async with self.config.guild(ctx.guild).audit_log() as audit_log:
            matches = [item for item in audit_log if str(item.get("launch_id")) == str(record["launch_id"])]
            if len(matches) != 1:
                raise RuntimeError("The launch record changed during verification.")
            matches[0].update({"status": "external_confirmed",
                "transaction_hash": result["transaction_hash"],
                "signer_address": result["signer_address"],
                "block_number": result["block_number"],
                "token_address": result["token_address"]})
        await ctx.send(f"Verified Clanker token `{result['token_address']}` in Base Sepolia block {result['block_number']}. Transaction: `{result['transaction_hash']}`")
    @clanker.command(name="airdropproofs", aliases=("proofs", "airdropexport"))
    @checks.mod_or_permissions(manage_guild=True)
    async def clanker_airdropproofs(self, ctx: commands.Context, launch_id: str):
        """Export airdrop proofs.

        Exports generated Merkle proof metadata for a launch record.
        """
        record = await self.get_launch_record(ctx.guild, launch_id)
        if not record:
            await ctx.send("No Clanker launch record matched that ID.")
            return
        proof_export = record.get("airdrop_proofs")
        if not proof_export:
            await ctx.send("That launch record does not have generated airdrop proofs.")
            return
        content = json.dumps(proof_export, indent=2, sort_keys=True).encode("utf-8")
        filename = f"clanker-airdrop-{record.get('launch_id', 'proofs')}.json"
        await ctx.send(
            "Generated Clanker airdrop proof export. Keep this with launch records for claimant tooling.",
            file=discord.File(io.BytesIO(content), filename=filename),
        )
