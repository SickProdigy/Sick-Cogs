import datetime
import io
import json
import logging
import re
import secrets
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

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
from .helpers import (
    build_airdrop_merkle_tree,
    format_tokens,
    is_eth_address,
    parse_airdrop_lines,
    utc_now,
    validate_airdrop_total,
)
from .admin import ClankerAdminMixin
from .views import ClankerDraftView

log = logging.getLogger("red.Sick-Cogs.Clanker")

BASE_SEPOLIA_RPCS = ("https://sepolia.base.org", "https://sepolia-preconf.base.org")
TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
MAX_RPC_BYTES = 1024 * 1024
TOKEN_CREATED_TOPIC = "0x9299d1d1a88d8e1abdc591ae7a167a6bc63a8f17d695804e9091ee33aa89fb67"


async def clanker_rpc(method: str, params: list[Any]) -> Any:
    """Call bounded public Base Sepolia RPC endpoints."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    timeout = aiohttp.ClientTimeout(total=15)
    last_error = None
    for url in BASE_SEPOLIA_RPCS:
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as response:
                    raw = await response.content.read(MAX_RPC_BYTES + 1)
                    if response.status != 200 or len(raw) > MAX_RPC_BYTES:
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
    expected_admin = str((intent.get("token") or {}).get("admin") or "").lower()
    if not ADDRESS_RE.fullmatch(token_address) or token_admin != expected_admin:
        raise ValueError("The Clanker token event does not match the immutable launch admin.")
    code = str(await clanker_rpc("eth_getCode", [token_address, "latest"]) or "").lower()
    if code in {"", "0x", "0x0"}:
        raise ValueError("The reported Clanker token has no deployed bytecode.")
    return {"verified": True, "status": "confirmed", "transaction_hash": returned_hash,
            "signer_address": sender, "block_number": block_number, "token_address": token_address}



class Clanker(ClankerAdminMixin, commands.Cog):
    """Prepare and orchestrate Clanker token launch requests on Base Sepolia."""

    __author__ = ["SickProdigy"]
    __version__ = "0.1.0"

    default_guild = {
        "enabled": False,
        "treasury_address": None,
        "platform_bps": 2000,
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
        self.config.register_guild(**self.default_guild)
        self.user_cooldowns: Dict[Tuple[int, int], datetime.datetime] = {}

    async def red_delete_data_for_user(self, **kwargs):
        """This cog stores no per-user profile data."""
        return

    @staticmethod
    def validate_https_url(url: str) -> bool:
        parsed = urlparse((url or "").strip())
        return parsed.scheme == "https" and bool(parsed.netloc)

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
    ) -> Dict[str, Any]:
        clean_name = " ".join(str(name or "").strip().split())
        clean_symbol = str(symbol or "").strip().upper().lstrip("$")
        if not clean_name or len(clean_name.encode("utf-8")) > 64:
            raise ValueError("Token name must contain 1 through 64 UTF-8 bytes.")
        if not SYMBOL_RE.fullmatch(clean_symbol):
            raise ValueError("Token symbol must contain 2 through 12 uppercase letters or numbers.")
        if not is_eth_address(primary_beneficiary):
            raise ValueError("Token admin must be a valid EVM address.")
        if not is_eth_address(platform_address):
            raise ValueError("Platform treasury must be a valid EVM address.")
        if image_url and not Clanker.validate_https_url(image_url):
            raise ValueError("Image URL must be HTTPS.")
        if not 0 <= platform_bps <= 10_000:
            raise ValueError("Platform reward bps must be from 0 through 10000.")
        recipients = []
        creator_bps = 10_000 - platform_bps
        if creator_bps:
            recipients.append(ClankerReward(
                primary_beneficiary, primary_beneficiary, creator_bps,
            ).to_dict())
        if platform_bps:
            recipients.append(ClankerReward(
                platform_address, platform_address, platform_bps,
            ).to_dict())
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
    def build_launch_intent(
        guild_id: int, requester_id: int, launch_id: str, payload: Dict[str, Any],
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
        created_at = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        return ClankerLaunchIntent.create(
            launch_id=launch_id, guild_id=guild_id, requester_id=requester_id,
            token_admin=str(payload["tokenAdmin"]), name=str(payload["name"]),
            symbol=str(payload["symbol"]), image=str(payload.get("image") or ""),
            metadata=payload.get("metadata") or {}, context=payload.get("context") or {},
            pool=pool, rewards=rewards, vault=vault, airdrop=airdrop,
            created_at=created_at, expires_at=created_at + 900,
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
    def launch_record_line(record: Dict[str, Any]) -> str:
        launch_id = record.get("launch_id", "legacy")
        created = record.get("created_at", "unknown")
        status = record.get("status", "unknown")
        symbol = record.get("symbol", "?")
        requester = record.get("requester_name", record.get("requester_id", "?"))
        return f"{launch_id} · {created} · {status} · ${symbol} by {requester}"

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
        title = f"Clanker launch {record.get('launch_id', 'legacy')}"
        embed = discord.Embed(title=title, color=discord.Color.blue())
        embed.add_field(name="Status", value=record.get("status", "unknown"), inline=True)
        embed.add_field(name="Token", value=f"{record.get('name', '?')} (${record.get('symbol', '?')})", inline=False)
        embed.add_field(name="Created", value=record.get("created_at", "unknown"), inline=False)
        embed.add_field(name="Requester", value=record.get("requester_name", record.get("requester_id", "?")), inline=True)
        embed.add_field(name="Chain", value=record.get("chain", "base-sepolia"), inline=True)
        embed.add_field(name="Supply", value=str(record.get("supply", "unknown")), inline=True)
        embed.add_field(name="Creator/token admin", value=record.get("token_admin") or "unknown", inline=False)
        embed.add_field(
            name="Creator rewards",
            value=f"Creator {record.get('creator_bps', '?')} bps / platform {record.get('platform_bps', '?')} bps",
            inline=False,
        )
        embed.add_field(name="Platform treasury", value=record.get("platform_treasury") or "unknown", inline=False)
        if record.get("vault_percentage"):
            embed.add_field(
                name="Vault",
                value=(
                    f"{record.get('vault_percentage')}% of supply · "
                    f"recipient {record.get('vault_recipient') or 'token admin'}"
                ),
                inline=False,
            )
        if record.get("airdrop_amount"):
            proof_export = record.get("airdrop_proofs") or {}
            proof_note = ""
            if proof_export:
                proof_note = f"\n{proof_export.get('recipient_count', 0)} generated proofs · {proof_export.get('schema', 'unknown schema')}"
            embed.add_field(
                name="Airdrop",
                value=(
                    f"{record.get('airdrop_amount')} tokens · "
                    f"root {record.get('airdrop_merkle_root') or 'missing'}{proof_note}"
                ),
                inline=False,
            )
        embed.set_footer(text="Bounded per-guild launch record · no wallet secrets")
        return embed

    async def create_internal_wallet_approval(self, user: Any, record: Dict[str, Any]) -> Dict[str, Any]:
        """Ask CryptoWallet to approve exactly the operation owned by this record."""

        wallet = self.bot.get_cog("CryptoWallet")
        create_approval = getattr(wallet, "clanker_create_internal_approval", None) if wallet else None
        if not callable(create_approval):
            raise RuntimeError("CryptoWallet is unavailable or does not support Clanker approvals.")
        launch = record.get("intent")
        operation = record.get("operation")
        if not isinstance(launch, dict) or not isinstance(operation, dict):
            raise ValueError("This launch record does not contain an immutable operation.")
        if (
            int(record.get("requester_id", 0)) != int(user.id)
            or str(record.get("launch_id")) != str(launch.get("launch_id"))
            or str(record.get("payload_hash", "")).lower() != str(launch.get("payload_hash", "")).lower()
        ):
            raise ValueError("The launch record identity binding is invalid.")
        result = await create_approval(user, launch, operation)
        expected = {"route", "launch_id", "source_payload_hash", "signing_intent_id",
                    "signing_payload_hash", "approval_url", "expires_at"}
        if (
            not isinstance(result, dict) or set(result) != expected
            or result.get("route") != "internal"
            or str(result.get("launch_id")) != str(record["launch_id"])
            or str(result.get("source_payload_hash", "")).lower() != str(record["payload_hash"]).lower()
            or not self.validate_https_url(str(result.get("approval_url") or ""))
            or int(result.get("expires_at", 0)) <= int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        ):
            raise RuntimeError("CryptoWallet returned an invalid Clanker approval binding.")
        return result

    async def mark_internal_approval(self, guild: discord.Guild, launch_id: str, result: Dict[str, Any]) -> None:
        """Persist safe signer references without storing the one-time approval URL."""

        async with self.config.guild(guild).audit_log() as audit_log:
            matches = [item for item in audit_log if str(item.get("launch_id")) == launch_id]
            if len(matches) != 1:
                raise RuntimeError("The Clanker launch record changed before approval was saved.")
            record = matches[0]
            if record.get("status") != "dry_run":
                raise RuntimeError("This Clanker launch has already entered an execution route.")
            record["status"] = "awaiting_cryptowallet_approval"
            record["execution_route"] = "internal"
            record["signing_intent_id"] = result["signing_intent_id"]
            record["signing_payload_hash"] = result["signing_payload_hash"]
            record["approval_expires_at"] = int(result["expires_at"])

    async def get_launch_record(self, guild: discord.Guild, launch_id: str) -> Optional[Dict[str, Any]]:
        audit_log: List[Dict[str, Any]] = await self.config.guild(guild).audit_log()
        needle = launch_id.strip().lower()
        for record in reversed(audit_log):
            record_id = str(record.get("launch_id") or "").lower()
            if record_id == needle or record_id.startswith(needle):
                return record
        return None

    @commands.guild_only()
    @commands.group(name="clanker", aliases=("clank",), invoke_without_command=True)
    async def clanker(self, ctx: commands.Context):
        """Create and inspect Clanker token launch requests."""
        await ctx.send_help()

    @clanker.command(name="status")
    async def clanker_status(self, ctx: commands.Context):
        """Show whether Clanker launch requests are enabled."""
        settings = await self.config.guild(ctx.guild).all()
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

    @clanker.command(name="card", aliases=("create", "draft"))
    async def clanker_card(self, ctx: commands.Context):
        """Open an interactive launch-card draft with optional airdrops."""
        settings = await self.config.guild(ctx.guild).all()
        if not settings["enabled"]:
            await ctx.send("Clanker launch requests are disabled in this server.")
            return
        if not settings["treasury_address"]:
            await ctx.send("A bot owner must configure the SickGaming treasury address first.")
            return
        if not await self.check_launch_controls(ctx, settings):
            return
        view = ClankerDraftView(self, ctx, settings)
        await ctx.send(embed=view.embed(), view=view)

    @clanker.command(name="launch")
    async def clanker_launch(
        self,
        ctx: commands.Context,
        symbol: str,
        name: str,
        primary_beneficiary: str,
        image_url: Optional[str] = None,
        *,
        description: Optional[str] = None,
    ):
        """Prepare a Clanker token launch request."""
        settings = await self.config.guild(ctx.guild).all()
        if not settings["enabled"]:
            await ctx.send("Clanker launch requests are disabled in this server.")
            return
        if not settings["treasury_address"]:
            await ctx.send("A bot owner must configure the SickGaming treasury address first.")
            return
        if not await self.check_launch_controls(ctx, settings):
            return
        symbol = symbol.strip().upper().lstrip("$")
        if not SYMBOL_RE.fullmatch(symbol):
            await ctx.send("Token symbols must be 2-12 uppercase letters or numbers.")
            return
        if not name.strip() or len(name.strip().encode("utf-8")) > 64:
            await ctx.send("Token names must be 1-64 UTF-8 bytes.")
            return
        if not is_eth_address(primary_beneficiary):
            await ctx.send("Primary beneficiary must be a valid EVM address.")
            return
        if image_url and not self.validate_https_url(image_url):
            await ctx.send("Image URL must be an HTTPS URL. Upload the image somewhere stable first.")
            return
        if description and len(description) > 500:
            await ctx.send("Description must be 500 characters or less.")
            return
        if settings["airdrop_enabled"]:
            merkle_root = settings["airdrop_merkle_root"]
            if not merkle_root or not MERKLE_ROOT_RE.fullmatch(merkle_root):
                await ctx.send("Airdrop is enabled but no valid 32-byte Merkle root is configured.")
                return
            if int(settings["airdrop_amount"]) <= 0:
                await ctx.send("Airdrop is enabled but the airdrop amount is not configured.")
                return
            airdrop_admin = settings["airdrop_admin"]
            if airdrop_admin and not is_eth_address(airdrop_admin):
                await ctx.send("Configured airdrop admin must be a valid EVM address.")
                return

        try:
            payload = self.build_payload(
                symbol,
                name,
                primary_beneficiary,
                settings["treasury_address"],
                int(settings["platform_bps"]),
                bool(settings["airdrop_enabled"]),
                settings["airdrop_merkle_root"],
                int(settings["airdrop_amount"]),
                int(settings["airdrop_lockup_seconds"]),
                int(settings["airdrop_vesting_seconds"]),
                settings["airdrop_admin"],
                ctx.author.id,
                image_url,
                description,
                bool(settings.get("vault_enabled")),
                int(settings.get("vault_percentage") or 0),
                int(settings.get("vault_lockup_seconds") or MIN_VAULT_LOCKUP_SECONDS),
                int(settings.get("vault_vesting_seconds") or 0),
                settings.get("vault_recipient"),
            )
        except ValueError as exc:
            await ctx.send(str(exc))
            return
        record = self.build_audit_record(ctx.author, payload, ctx.guild.id)
        proof_export = settings.get("airdrop_proof_export")
        if proof_export and proof_export.get("root", "").lower() == str(record.get("airdrop_merkle_root") or "").lower():
            record["airdrop_proofs"] = proof_export
        await self.add_audit_record(ctx.guild, record)
        await self.notify_approval_channel(ctx.guild, settings, record)

        embed = discord.Embed(
            title="Clanker launch draft prepared",
            description="Saved for review; choose an execution wallet in a later step.",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Token", value=f"{payload['name']} (${payload['symbol']})", inline=False)
        embed.add_field(name="Supply", value=str(DEFAULT_CLANKER_SUPPLY), inline=True)
        embed.add_field(name="Chain", value="Base Sepolia", inline=True)
        embed.add_field(
            name="Beneficiaries",
            value=humanize_list([
                f"Creator {record['creator_bps']} bps",
                f"Bot owner {record['platform_bps']} bps",
            ]),
            inline=False,
        )
        if image_url:
            embed.set_thumbnail(url=image_url)
        if payload.get("airdrop"):
            embed.add_field(
                name="Airdrop",
                value=(
                    f"{payload['airdrop']['amount']} tokens · "
                    f"lock {payload['airdrop']['lockupDuration']}s"
                ),
                inline=False,
            )
        await ctx.send(embed=embed)

    @clanker.command(name="audit")
    @checks.mod_or_permissions(manage_guild=True)
    async def clanker_audit(self, ctx: commands.Context, limit: commands.Range[int, 1, 20] = 10):
        """Show recent Clanker launch request audit records."""
        audit_log: List[Dict[str, Any]] = await self.config.guild(ctx.guild).audit_log()
        if not audit_log:
            await ctx.send("No Clanker launch requests have been recorded.")
            return
        lines = [self.launch_record_line(r) for r in reversed(audit_log[-limit:])]
        await ctx.send(box("\n".join(lines)))

    @clanker.command(name="launches", aliases=("history", "records"))
    @checks.mod_or_permissions(manage_guild=True)
    async def clanker_launches(self, ctx: commands.Context, limit: commands.Range[int, 1, 20] = 10):
        """List recent Clanker launch records with their launch IDs."""
        audit_log: List[Dict[str, Any]] = await self.config.guild(ctx.guild).audit_log()
        if not audit_log:
            await ctx.send("No Clanker launch requests have been recorded.")
            return
        lines = [self.launch_record_line(r) for r in reversed(audit_log[-limit:])]
        await ctx.send(box("\n".join(lines)))

    @clanker.command(name="launchinfo", aliases=("record", "info"))
    @checks.mod_or_permissions(manage_guild=True)
    async def clanker_launchinfo(self, ctx: commands.Context, launch_id: str):
        """Show details for one Clanker launch audit record."""
        record = await self.get_launch_record(ctx.guild, launch_id)
        if not record:
            await ctx.send("No Clanker launch record matched that ID.")
            return
        await ctx.send(embed=self.launch_record_embed(record))

    @clanker.command(name="internal", aliases=("wallet",))
    async def clanker_internal(self, ctx: commands.Context, launch_id: str):
        """Open protected CryptoWallet approval for your saved launch."""
        record = await self.get_launch_record(ctx.guild, launch_id)
        if not record:
            await ctx.send("No Clanker launch record matched that ID.")
            return
        if int(record.get("requester_id", 0)) != int(ctx.author.id):
            await ctx.send("Only the launch requester can choose its signing wallet.")
            return
        if record.get("status") != "dry_run":
            await ctx.send("That launch has already entered an execution route.")
            return
        try:
            result = await self.create_internal_wallet_approval(ctx.author, record)
            await ctx.author.send("Review and approve your Base Sepolia Clanker launch here:\n" + result["approval_url"] + "\nThis protected link is short-lived and bound to your Discord account.")
            await self.mark_internal_approval(ctx.guild, str(record["launch_id"]), result)
        except discord.Forbidden:
            await ctx.send("I could not DM the protected approval link. Enable DMs and try again.")
            return
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await ctx.send(f"Clanker could not start CryptoWallet approval: {exc}")
            return
        await ctx.send("I sent your protected CryptoWallet approval link by DM.")

    @clanker.command(name="external")
    async def clanker_external(self, ctx: commands.Context, launch_id: str):
        """DM the requester the exact transaction for an external wallet."""
        record = await self.get_launch_record(ctx.guild, launch_id)
        if not record:
            await ctx.send("No Clanker launch record matched that ID.")
            return
        if int(record.get("requester_id", 0)) != int(ctx.author.id):
            await ctx.send("Only the launch requester can choose its execution wallet.")
            return
        if record.get("status") != "dry_run":
            await ctx.send("That launch has already entered an execution route.")
            return
        operation = record.get("operation")
        intent = record.get("intent")
        if not isinstance(operation, dict) or not isinstance(intent, dict):
            await ctx.send("That launch does not contain an immutable external-wallet operation.")
            return
        if int(intent.get("expires_at", 0)) <= int(datetime.datetime.now(datetime.timezone.utc).timestamp()):
            await ctx.send("That immutable launch has expired; create and review a new draft.")
            return
        handoff = {"version": 1, "kind": "clanker-v4-external-handoff",
                   "requester_id": str(ctx.author.id), "expires_at": intent.get("expires_at"),
                   "operation": operation}
        content = json.dumps(handoff, indent=2, sort_keys=True).encode("utf-8")
        try:
            await ctx.author.send(
                "External Base Sepolia Clanker handoff. Review every field before submitting it with your wallet. "
                f"After confirmation run `{ctx.clean_prefix}clanker verify {record['launch_id']} <transaction_hash>`. ",
                file=discord.File(io.BytesIO(content), filename=f"clanker-{record['launch_id']}.json"),
            )
        except discord.Forbidden:
            await ctx.send("I could not DM the external-wallet handoff. Enable DMs and try again.")
            return
        async with self.config.guild(ctx.guild).audit_log() as audit_log:
            matches = [item for item in audit_log if str(item.get("launch_id")) == str(record["launch_id"])]
            if len(matches) != 1 or matches[0].get("status") != "dry_run":
                await ctx.send("The launch changed before its external route could be saved.")
                return
            matches[0]["status"] = "awaiting_external_wallet"
            matches[0]["execution_route"] = "external"
        await ctx.send("I sent the exact external-wallet operation and verification command by DM.")

    @clanker.command(name="verify")
    async def clanker_verify(self, ctx: commands.Context, launch_id: str, transaction_hash: str):
        """Verify an external Base Sepolia transaction against the saved operation."""
        record = await self.get_launch_record(ctx.guild, launch_id)
        if not record or int(record.get("requester_id", 0)) != int(ctx.author.id):
            await ctx.send("No matching external-wallet launch belongs to you.")
            return
        if record.get("status") not in {"awaiting_external_wallet", "external_pending"}:
            await ctx.send("That launch is not awaiting external-wallet verification.")
        await ctx.send(f"Verified Clanker token `{result['token_address']}` in Base Sepolia block {result['block_number']}. Transaction: `{result['transaction_hash']}`")
        try:
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
            await ctx.send("That transaction is still pending a Base Sepolia receipt. Try verification again shortly.")
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
        await ctx.send(f"Verified the exact Clanker operation in Base Sepolia block {result['block_number']}. Transaction: `{result['transaction_hash']}`")
        await ctx.send(f"Verified Clanker token `{result['token_address']}` in Base Sepolia block {result['block_number']}. Transaction: `{result['transaction_hash']}`")
    @clanker.command(name="airdropproofs", aliases=("proofs", "airdropexport"))
    @checks.mod_or_permissions(manage_guild=True)
    async def clanker_airdropproofs(self, ctx: commands.Context, launch_id: str):
        """Export generated airdrop Merkle proofs for a launch record."""
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
