import datetime
import io
import json
import logging
import secrets
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiohttp
import discord
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box

from .constants import (
    BASE_CHAIN_ID,
    CONFIG_IDENTIFIER,
    DEFAULT_CLANKER_SUPPLY,
    MAX_AUDIT_RECORDS,
    MERKLE_ROOT_RE,
    MIN_AIRDROP_LOCKUP_SECONDS,
    SYMBOL_RE,
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
from .views import ClankerDraftView

log = logging.getLogger("red.Sick-Cogs.Clanker")


class Clanker(ClankerAdminMixin, commands.Cog):
    """Prepare and optionally submit Clanker token launch requests on Base."""

    __author__ = ["SickProdigy", "chatgpt-codex"]
    __version__ = "0.1.0"

    default_guild = {
        "enabled": False,
        "api_base_url": None,
        "api_submit_path": "tokens",
        "submit_enabled": False,
        "treasury_address": None,
        "platform_bps": 2000,
        "launch_channel_id": None,
        "approval_channel_id": None,
        "approval_required": False,
        "allowed_role_id": None,
        "blocked_role_id": None,
        "launch_cooldown_seconds": 60,
        "daily_max_per_user": 0,
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
        self.session: Optional[aiohttp.ClientSession] = None
        self.user_cooldowns: Dict[Tuple[int, int], datetime.datetime] = {}

    async def red_delete_data_for_user(self, **kwargs):
        """This cog stores no per-user profile data."""
        return

    def cog_unload(self):
        if self.session and not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=45)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": "Sick-Cogs-Clanker/0.1"},
            )
        return self.session

    async def get_api_token(self) -> Optional[str]:
        tokens = await self.bot.get_shared_api_tokens("clanker")
        token = tokens.get("api_token") or tokens.get("token")
        return token.strip() if token else None

    @staticmethod
    def validate_https_url(url: str) -> bool:
        parsed = urlparse((url or "").strip())
        return parsed.scheme == "https" and bool(parsed.netloc)

    @staticmethod
    def build_payload(
        symbol: str,
        name: str,
        supply: int,
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
    ) -> Dict[str, Any]:
        payload = {
            "chainId": BASE_CHAIN_ID,
            "chain": "base",
            "symbol": symbol,
            "name": name.strip(),
            "supply": str(supply),
            "description": description or "",
            "imageUrl": image_url or "",
            "requesterDiscordId": str(requester_id),
            "tokenAdmin": primary_beneficiary,
            "rewards": {
                "recipients": [
                    {
                        "admin": primary_beneficiary,
                        "recipient": primary_beneficiary,
                        "bps": 10000 - platform_bps,
                        "token": "Both",
                    },
                    {
                        "admin": platform_address,
                        "recipient": platform_address,
                        "bps": platform_bps,
                        "token": "Both",
                    },
                ]
            },
        }
        if airdrop_enabled and airdrop_merkle_root and airdrop_amount > 0:
            validate_airdrop_total(airdrop_amount, supply)
            payload["airdrop"] = {
                "merkleRoot": airdrop_merkle_root,
                "amount": airdrop_amount,
                "lockupDuration": airdrop_lockup_seconds,
            }
            if airdrop_vesting_seconds > 0:
                payload["airdrop"]["vestingDuration"] = airdrop_vesting_seconds
            if airdrop_admin:
                payload["airdrop"]["admin"] = airdrop_admin
        return payload

    async def submit_payload(
        self,
        api_base_url: str,
        api_submit_path: str,
        token: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        endpoint = urljoin(api_base_url.rstrip("/") + "/", (api_submit_path or "tokens").lstrip("/"))
        session = await self.get_session()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        async with session.post(endpoint, json=payload, headers=headers) as response:
            text = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"Clanker API returned HTTP {response.status}: {text[:500]}")
            try:
                return await response.json()
            except aiohttp.ContentTypeError:
                return {"raw": text}

    @staticmethod
    def new_launch_id(symbol: str) -> str:
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"{symbol.lower()}-{timestamp}-{secrets.token_hex(3)}"

    @staticmethod
    def extract_api_references(response: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Pull common launch references from a Clanker response without trusting one schema."""
        if not isinstance(response, dict):
            return {}
        wanted = {
            "id",
            "requestId",
            "launchId",
            "tokenAddress",
            "contractAddress",
            "poolAddress",
            "transactionHash",
            "txHash",
            "url",
            "poolUrl",
        }
        found: Dict[str, Any] = {}

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in wanted and key not in found and child not in (None, ""):
                        found[key] = child
                    visit(child)
            elif isinstance(value, list):
                for child in value[:20]:
                    visit(child)

        visit(response)
        return found

    @staticmethod
    def build_audit_record(
        requester: Any,
        payload: Dict[str, Any],
        status: str = "dry_run",
    ) -> Dict[str, Any]:
        airdrop = payload.get("airdrop") or {}
        return {
            "launch_id": Clanker.new_launch_id(payload["symbol"]),
            "created_at": utc_now(),
            "requester_id": requester.id,
            "requester_name": str(requester),
            "symbol": payload["symbol"],
            "name": payload["name"],
            "chain": payload.get("chain", "base"),
            "supply": payload.get("supply"),
            "token_admin": payload.get("tokenAdmin"),
            "platform_treasury": payload["rewards"]["recipients"][1]["recipient"],
            "creator_bps": payload["rewards"]["recipients"][0]["bps"],
            "platform_bps": payload["rewards"]["recipients"][1]["bps"],
            "airdrop_amount": airdrop.get("amount", 0),
            "airdrop_merkle_root": airdrop.get("merkleRoot"),
            "airdrop_proofs": airdrop.get("merkleExport"),
            "payload": payload,
            "status": status,
            "api_response": None,
            "api_refs": {},
        }

    @staticmethod
    def launch_record_line(record: Dict[str, Any]) -> str:
        launch_id = record.get("launch_id", "legacy")
        created = record.get("created_at", "unknown")
        status = record.get("status", "unknown")
        symbol = record.get("symbol", "?")
        requester = record.get("requester_name", record.get("requester_id", "?"))
        refs = record.get("api_refs") or {}
        token_address = refs.get("tokenAddress") or refs.get("contractAddress")
        suffix = f" · token {token_address}" if token_address else ""
        return f"{launch_id} · {created} · {status} · ${symbol} by {requester}{suffix}"

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
        embed.add_field(name="Chain", value=record.get("chain", "base"), inline=True)
        embed.add_field(name="Supply", value=str(record.get("supply", "unknown")), inline=True)
        embed.add_field(name="Creator/token admin", value=record.get("token_admin") or "unknown", inline=False)
        embed.add_field(
            name="Creator rewards",
            value=f"Creator {record.get('creator_bps', '?')} bps / platform {record.get('platform_bps', '?')} bps",
            inline=False,
        )
        embed.add_field(name="Platform treasury", value=record.get("platform_treasury") or "unknown", inline=False)
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
        refs = record.get("api_refs") or {}
        if refs:
            reference_lines = [f"{key}: {value}" for key, value in refs.items()]
            embed.add_field(name="API references", value=box("\n".join(reference_lines)[:900]), inline=False)
        elif record.get("api_response"):
            embed.add_field(name="API response", value=box(str(record["api_response"])[:900]), inline=False)
        if record.get("approved_at"):
            embed.add_field(name="Approved/submitted", value=record["approved_at"], inline=False)
        if record.get("rejected_at"):
            embed.add_field(name="Rejected", value=record["rejected_at"], inline=True)
            embed.add_field(name="Reason", value=record.get("rejection_reason") or "No reason provided.", inline=False)
        embed.set_footer(text="Audit records are bounded per guild and do not contain API tokens or wallet secrets")
        return embed

    async def get_launch_record(self, guild: discord.Guild, launch_id: str) -> Optional[Dict[str, Any]]:
        audit_log: List[Dict[str, Any]] = await self.config.guild(guild).audit_log()
        needle = launch_id.strip().lower()
        for record in reversed(audit_log):
            record_id = str(record.get("launch_id") or "").lower()
            if record_id == needle or record_id.startswith(needle):
                return record
        return None

    async def update_launch_record(self, guild: discord.Guild, launch_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        needle = launch_id.strip().lower()
        async with self.config.guild(guild).audit_log() as audit_log:
            for record in reversed(audit_log):
                record_id = str(record.get("launch_id") or "").lower()
                if record_id == needle or record_id.startswith(needle):
                    record.update(updates)
                    return dict(record)
        return None

    async def submit_approved_launch(self, guild: discord.Guild, settings: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
        if not settings.get("submit_enabled"):
            raise RuntimeError("Clanker submit mode is disabled.")
        if not settings.get("api_base_url"):
            raise RuntimeError("Submit mode is enabled but no Clanker API URL is configured.")
        token = await self.get_api_token()
        if not token:
            raise RuntimeError("Submit mode is enabled but no Clanker API token is configured.")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("This launch record does not have a stored payload to submit.")
        response = await self.submit_payload(settings["api_base_url"], settings.get("api_submit_path") or "tokens", token, payload)
        return {
            "status": "submitted",
            "approved_at": utc_now(),
            "api_response": response,
            "api_refs": self.extract_api_references(response),
        }

    @commands.guild_only()
    @commands.group(name="clanker", aliases=("clank",), invoke_without_command=True)
    async def clanker(self, ctx: commands.Context):
        """Create and inspect Clanker token launch requests."""
        await ctx.send_help()

    @clanker.command(name="status")
    async def clanker_status(self, ctx: commands.Context):
        """Show whether Clanker launch requests are enabled."""
        settings = await self.config.guild(ctx.guild).all()
        token = await self.get_api_token()
        embed = discord.Embed(title="Clanker status", color=discord.Color.blue())
        embed.add_field(name="Enabled", value=str(settings["enabled"]), inline=True)
        embed.add_field(name="Submit mode", value=str(settings["submit_enabled"]), inline=True)
        embed.add_field(name="API URL", value="Set" if settings["api_base_url"] else "Not set", inline=True)
        embed.add_field(name="Submit path", value=settings.get("api_submit_path") or "tokens", inline=True)
        embed.add_field(name="API token", value="Set" if token else "Not set", inline=True)
        embed.add_field(name="Platform treasury", value=settings["treasury_address"] or "Not set", inline=False)
        embed.add_field(name="Platform split", value=f"{settings['platform_bps']} bps", inline=True)
        launch_channel = ctx.guild.get_channel(settings.get("launch_channel_id") or 0)
        approval_channel = ctx.guild.get_channel(settings.get("approval_channel_id") or 0)
        allowed_role = ctx.guild.get_role(settings.get("allowed_role_id") or 0)
        blocked_role = ctx.guild.get_role(settings.get("blocked_role_id") or 0)
        embed.add_field(name="Launch channel", value=launch_channel.mention if launch_channel else "Any", inline=True)
        embed.add_field(name="Approval/log channel", value=approval_channel.mention if approval_channel else "Not set", inline=True)
        embed.add_field(name="Approval required", value=str(settings.get("approval_required", False)), inline=True)
        embed.add_field(name="Allowed role", value=allowed_role.mention if allowed_role else "Any", inline=True)
        embed.add_field(name="Blocked role", value=blocked_role.mention if blocked_role else "None", inline=True)
        embed.add_field(name="Launch cooldown", value=f"{int(settings.get('launch_cooldown_seconds') or 0)}s", inline=True)
        daily_max = int(settings.get("daily_max_per_user") or 0)
        embed.add_field(name="Daily max", value=str(daily_max) if daily_max else "Unlimited", inline=True)
        embed.add_field(
            name="Airdrop",
            value=(
                f"{settings['airdrop_amount']} tokens · lock {settings['airdrop_lockup_seconds']}s"
                if settings["airdrop_enabled"] and settings["airdrop_amount"]
                else "Disabled"
            ),
            inline=True,
        )
        embed.set_footer(text="Base chain only · no private keys are stored by this cog")
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
        supply: commands.Range[int, 1, 10**18],
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
        if not name.strip() or len(name.strip()) > 80:
            await ctx.send("Token names must be 1-80 characters.")
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

        payload = self.build_payload(
            symbol,
            name,
            supply,
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
        )
        record = self.build_audit_record(ctx.author, payload)
        proof_export = settings.get("airdrop_proof_export")
        if proof_export and proof_export.get("root", "").lower() == str(record.get("airdrop_merkle_root") or "").lower():
            record["airdrop_proofs"] = proof_export
        if settings["submit_enabled"]:
            if settings.get("approval_required"):
                record["status"] = "pending_approval"
            else:
                if not settings["api_base_url"]:
                    await ctx.send("Submit mode is enabled but no Clanker API URL is configured.")
                    return
                token = await self.get_api_token()
                if not token:
                    await ctx.send("Submit mode is enabled but no Clanker API token is configured.")
                    return
                async with ctx.typing():
                    try:
                        record["api_response"] = await self.submit_payload(settings["api_base_url"], token, payload)
                        record["api_refs"] = self.extract_api_references(record["api_response"])
                        record["status"] = "submitted"
                    except RuntimeError as exc:
                        record["status"] = "failed"
                        await self.add_audit_record(ctx.guild, record)
                        await self.notify_approval_channel(ctx.guild, settings, record)
                        await ctx.send(str(exc))
                        return
        await self.add_audit_record(ctx.guild, record)
        await self.notify_approval_channel(ctx.guild, settings, record)

        submitted = record["status"] == "submitted"
        if record["status"] == "submitted":
            title_status = "submitted"
            description = None
        elif record["status"] == "pending_approval":
            title_status = "pending approval"
            description = "Stored for owner approval before live Clanker submission."
        else:
            title_status = "prepared"
            description = "Dry-run only. Enable submit mode after the API contract is reviewed."
        embed = discord.Embed(
            title=f"Clanker launch {title_status}",
            description=description,
            color=discord.Color.green() if submitted else discord.Color.gold(),
        )
        embed.add_field(name="Token", value=f"{payload['name']} (${payload['symbol']})", inline=False)
        embed.add_field(name="Supply", value=payload["supply"], inline=True)
        embed.add_field(name="Chain", value="Base", inline=True)
        embed.add_field(
            name="Beneficiaries",
            value=humanize_list([
                f"Creator {payload['rewards']['recipients'][0]['bps']} bps",
                f"Bot owner {payload['rewards']['recipients'][1]['bps']} bps",
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
        if submitted and record["api_response"]:
            embed.add_field(name="API response", value=box(str(record["api_response"])[:900]), inline=False)
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

    @clanker.command(name="approve")
    @checks.is_owner()
    async def clanker_approve(self, ctx: commands.Context, launch_id: str):
        """Approve and submit a pending Clanker launch record."""
        settings = await self.config.guild(ctx.guild).all()
        record = await self.get_launch_record(ctx.guild, launch_id)
        if not record:
            await ctx.send("No Clanker launch record matched that ID.")
            return
        if record.get("status") != "pending_approval":
            await ctx.send(f"Launch `{record.get('launch_id', launch_id)}` is `{record.get('status', 'unknown')}`, not pending approval.")
            return
        async with ctx.typing():
            try:
                updates = await self.submit_approved_launch(ctx.guild, settings, record)
            except RuntimeError as exc:
                updates = {"status": "failed", "approved_at": utc_now(), "api_response": {"error": str(exc)}, "api_refs": {}}
                updated = await self.update_launch_record(ctx.guild, launch_id, updates)
                if updated:
                    await self.notify_approval_channel(ctx.guild, settings, updated)
                await ctx.send(str(exc))
                return
        updated = await self.update_launch_record(ctx.guild, launch_id, updates)
        if not updated:
            await ctx.send("Launch record disappeared before it could be updated.")
            return
        await self.notify_approval_channel(ctx.guild, settings, updated)
        await ctx.send("Clanker launch approved and submitted.", embed=self.launch_record_embed(updated))

    @clanker.command(name="reject")
    @checks.is_owner()
    async def clanker_reject(self, ctx: commands.Context, launch_id: str, *, reason: Optional[str] = None):
        """Reject a pending Clanker launch record without submitting it."""
        settings = await self.config.guild(ctx.guild).all()
        record = await self.get_launch_record(ctx.guild, launch_id)
        if not record:
            await ctx.send("No Clanker launch record matched that ID.")
            return
        if record.get("status") != "pending_approval":
            await ctx.send(f"Launch `{record.get('launch_id', launch_id)}` is `{record.get('status', 'unknown')}`, not pending approval.")
            return
        updates = {"status": "rejected", "rejected_at": utc_now(), "rejection_reason": reason or "No reason provided."}
        updated = await self.update_launch_record(ctx.guild, launch_id, updates)
        if not updated:
            await ctx.send("Launch record disappeared before it could be updated.")
            return
        await self.notify_approval_channel(ctx.guild, settings, updated)
        await ctx.send("Clanker launch rejected.", embed=self.launch_record_embed(updated))
