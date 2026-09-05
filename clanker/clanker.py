import datetime
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import aiohttp
import discord
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, humanize_list

log = logging.getLogger("red.Sick-Cogs.Clanker")
CONFIG_IDENTIFIER = 1100110011
BASE_CHAIN_ID = 8453
MAX_AUDIT_RECORDS = 100
ETH_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,12}$")


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def is_eth_address(value: str) -> bool:
    return bool(ETH_ADDRESS_RE.fullmatch((value or "").strip()))


class Clanker(commands.Cog):
    """Prepare and optionally submit Clanker token launch requests on Base."""

    __author__ = ["SickProdigy", "chatgpt-codex"]
    __version__ = "0.1.0"

    default_guild = {
        "enabled": False,
        "api_base_url": None,
        "submit_enabled": False,
        "treasury_address": None,
        "platform_bps": 1000,
        "audit_log": [],
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**self.default_guild)
        self.session: Optional[aiohttp.ClientSession] = None

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
        requester_id: int,
        image_url: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
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

    async def submit_payload(self, api_base_url: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = urljoin(api_base_url.rstrip("/") + "/", "tokens")
        session = await self.get_session()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        async with session.post(endpoint, json=payload, headers=headers) as response:
            text = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"Clanker API returned HTTP {response.status}: {text[:500]}")
            try:
                return await response.json()
            except aiohttp.ContentTypeError:
                return {"raw": text}

    async def add_audit_record(self, guild: discord.Guild, record: Dict[str, Any]):
        async with self.config.guild(guild).audit_log() as audit_log:
            audit_log.append(record)
            del audit_log[:-MAX_AUDIT_RECORDS]

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
        embed.add_field(name="API token", value="Set" if token else "Not set", inline=True)
        embed.add_field(name="Platform treasury", value=settings["treasury_address"] or "Not set", inline=False)
        embed.add_field(name="Platform split", value=f"{settings['platform_bps']} bps", inline=True)
        embed.set_footer(text="Base chain only · no private keys are stored by this cog")
        await ctx.send(embed=embed)

    @clanker.command(name="launch")
    @commands.cooldown(1, 60, commands.BucketType.user)
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

        payload = self.build_payload(
            symbol,
            name,
            supply,
            primary_beneficiary,
            settings["treasury_address"],
            int(settings["platform_bps"]),
            ctx.author.id,
            image_url,
            description,
        )
        record = {
            "created_at": utc_now(),
            "requester_id": ctx.author.id,
            "requester_name": str(ctx.author),
            "symbol": symbol,
            "name": name.strip(),
            "status": "dry_run",
            "api_response": None,
        }
        if settings["submit_enabled"]:
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
                    record["status"] = "submitted"
                except RuntimeError as exc:
                    record["status"] = "failed"
                    await self.add_audit_record(ctx.guild, record)
                    await ctx.send(str(exc))
                    return
        await self.add_audit_record(ctx.guild, record)

        submitted = record["status"] == "submitted"
        embed = discord.Embed(
            title=f"Clanker launch {'submitted' if submitted else 'prepared'}",
            description=None if submitted else "Dry-run only. Enable submit mode after the API contract is reviewed.",
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
        lines = [
            f"{r.get('created_at', 'unknown')} · {r.get('status', 'unknown')} · ${r.get('symbol', '?')} by {r.get('requester_name', r.get('requester_id', '?'))}"
            for r in reversed(audit_log[-limit:])
        ]
        await ctx.send(box("\n".join(lines)))

    @commands.guild_only()
    @commands.group(name="clankerset")
    @checks.is_owner()
    async def clankerset(self, ctx: commands.Context):
        """Owner-only Clanker configuration."""
        pass

    @clankerset.command(name="view")
    async def clankerset_view(self, ctx: commands.Context):
        """Show Clanker configuration without secrets."""
        await self.clanker_status(ctx)

    @clankerset.command(name="enabled")
    async def clankerset_enabled(self, ctx: commands.Context, enabled: bool):
        """Enable or disable Clanker launch requests."""
        await self.config.guild(ctx.guild).enabled.set(enabled)
        await ctx.send(f"Clanker launch requests are now {'enabled' if enabled else 'disabled'}.")

    @clankerset.command(name="submit")
    async def clankerset_submit(self, ctx: commands.Context, enabled: bool):
        """Enable or disable live API submission."""
        await self.config.guild(ctx.guild).submit_enabled.set(enabled)
        await ctx.send(f"Clanker launch mode set to {'live API submission' if enabled else 'dry-run/review only'}.")

    @clankerset.command(name="apiurl")
    async def clankerset_apiurl(self, ctx: commands.Context, api_base_url: str):
        """Set the Clanker API base URL."""
        api_base_url = api_base_url.strip().rstrip("/")
        if not self.validate_https_url(api_base_url):
            await ctx.send("API URL must be HTTPS.")
            return
        await self.config.guild(ctx.guild).api_base_url.set(api_base_url)
        await ctx.send("Clanker API URL saved.")

    @clankerset.command(name="apitoken")
    async def clankerset_apitoken(self, ctx: commands.Context, *, api_token: str):
        """Set the Clanker API bearer token in shared API token storage."""
        await self.bot.set_shared_api_tokens("clanker", api_token=api_token.strip())
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        await ctx.send("Clanker API token saved.")

    @clankerset.command(name="treasury")
    async def clankerset_treasury(self, ctx: commands.Context, treasury_address: str):
        """Set the bot-owner/SickGaming platform treasury EVM address."""
        treasury_address = treasury_address.strip()
        if not is_eth_address(treasury_address):
            await ctx.send("Treasury address must be a valid EVM address.")
            return
        await self.config.guild(ctx.guild).treasury_address.set(treasury_address)
        await ctx.send("SickGaming treasury address saved.")

    @clankerset.command(name="platformbps")
    async def clankerset_platformbps(self, ctx: commands.Context, basis_points: commands.Range[int, 0, 10000]):
        """Set the bot-owner platform reward basis points for launch requests."""
        await self.config.guild(ctx.guild).platform_bps.set(basis_points)
        await ctx.send(f"Bot-owner platform split set to {basis_points} bps.")

    @clankerset.group(name="audit")
    async def clankerset_audit(self, ctx: commands.Context):
        """Manage Clanker audit records."""
        pass

    @clankerset_audit.command(name="clear")
    async def clankerset_audit_clear(self, ctx: commands.Context):
        """Clear Clanker audit records for this guild."""
        await self.config.guild(ctx.guild).audit_log.set([])
        await ctx.send("Clanker audit log cleared.")
