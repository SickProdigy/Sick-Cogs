import datetime
import json
import logging
import re
import secrets
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple
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
DEFAULT_CLANKER_SUPPLY = 100_000_000_000
MAX_EXTENSION_PERCENTAGE = 90
MIN_AIRDROP_LOCKUP_SECONDS = 604800
ETH_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,12}$")
MERKLE_ROOT_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def is_eth_address(value: str) -> bool:
    return bool(ETH_ADDRESS_RE.fullmatch((value or "").strip()))


def format_tokens(amount: int) -> str:
    return f"{amount:,}"


def parse_token_amount(value: str, supply: int) -> Tuple[int, str]:
    """Parse either a whole token amount or percentage of supply."""
    raw = (value or "").strip().replace(",", "").replace("_", "")
    if not raw:
        raise ValueError("Amount is required.")
    try:
        if raw.endswith("%"):
            percentage = Decimal(raw[:-1].strip())
            if percentage <= 0:
                raise ValueError("Percentage amounts must be positive.")
            amount = int((Decimal(supply) * percentage) / Decimal(100))
            if amount <= 0:
                raise ValueError("Percentage amount rounds down to zero tokens.")
            return amount, f"{percentage.normalize()}%"
        if "." in raw:
            decimal_amount = Decimal(raw)
            if decimal_amount != decimal_amount.to_integral_value():
                raise ValueError("Fixed token amounts must be whole tokens.")
            amount = int(decimal_amount)
        else:
            amount = int(raw)
    except (InvalidOperation, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc):
            raise
        raise ValueError("Amount must be a whole token count or a percentage like 1.5%.") from exc
    if amount <= 0:
        raise ValueError("Token amounts must be positive.")
    return amount, format_tokens(amount)


def parse_airdrop_lines(lines: str, supply: int) -> Tuple[List[Dict[str, Any]], int]:
    """Parse Discord airdrop recipient lines into normalized preview rows.

    Accepted examples:
    - 0xabc...=1%
    - 0xabc..., 250000000
    - 0xabc... 0.25%
    """
    recipients = []
    for index, raw_line in enumerate((lines or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "=" in line:
            address, amount_text = line.split("=", 1)
        elif "," in line:
            address, amount_text = line.split(",", 1)
        else:
            parts = line.split(None, 1)
            if len(parts) != 2:
                raise ValueError(f"Line {index}: use `address amount`, `address=amount`, or `address, amount`.")
            address, amount_text = parts
        address = address.strip()
        if not is_eth_address(address):
            raise ValueError(f"Line {index}: `{address}` is not a valid EVM address.")
        amount, label = parse_token_amount(amount_text, supply)
        recipients.append({"address": address, "amount": amount, "input": label})
    total = sum(row["amount"] for row in recipients)
    return recipients, total


def normalize_seconds(value: str, default: int, minimum: int = 0) -> int:
    text = (value or "").strip().replace(",", "").replace("_", "")
    if not text:
        return default
    try:
        seconds = int(text)
    except ValueError as exc:
        raise ValueError("Durations must be seconds as a whole number.") from exc
    if seconds < minimum:
        raise ValueError(f"Duration must be at least {minimum} seconds.")
    return seconds


class ClankerBasicsModal(discord.ui.Modal):
    def __init__(self, view: "ClankerDraftView"):
        super().__init__(title="Clanker token basics")
        self.view_ref = view
        self.name_input = discord.ui.TextInput(
            label="Token name",
            default=view.draft.get("name") or "",
            max_length=80,
        )
        self.symbol_input = discord.ui.TextInput(
            label="Symbol / ticker",
            default=view.draft.get("symbol") or "",
            min_length=2,
            max_length=12,
        )
        self.supply_input = discord.ui.TextInput(
            label="Supply",
            default=str(view.draft.get("supply") or DEFAULT_CLANKER_SUPPLY),
            max_length=24,
        )
        self.creator_input = discord.ui.TextInput(
            label="Creator wallet / token admin",
            default=view.draft.get("primary_beneficiary") or "",
            max_length=42,
        )
        self.image_input = discord.ui.TextInput(
            label="Image URL (optional HTTPS)",
            default=view.draft.get("image_url") or "",
            required=False,
            max_length=300,
        )
        for item in (self.name_input, self.symbol_input, self.supply_input, self.creator_input, self.image_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            symbol = str(self.symbol_input.value).strip().upper().lstrip("$")
            supply = int(str(self.supply_input.value).replace(",", "").replace("_", "").strip())
            creator = str(self.creator_input.value).strip()
            image_url = str(self.image_input.value).strip()
            if not SYMBOL_RE.fullmatch(symbol):
                raise ValueError("Token symbols must be 2-12 uppercase letters or numbers.")
            if not str(self.name_input.value).strip():
                raise ValueError("Token name is required.")
            if supply <= 0 or supply > 10**18:
                raise ValueError("Supply must be between 1 and 1e18 tokens.")
            if not is_eth_address(creator):
                raise ValueError("Creator wallet must be a valid EVM address.")
            if image_url and not self.view_ref.cog.validate_https_url(image_url):
                raise ValueError("Image URL must be HTTPS.")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.view_ref.draft.update(
            {
                "name": str(self.name_input.value).strip(),
                "symbol": symbol,
                "supply": supply,
                "primary_beneficiary": creator,
                "image_url": image_url or None,
            }
        )
        await self.view_ref.refresh(interaction, "Basics saved.")


class ClankerDetailsModal(discord.ui.Modal):
    def __init__(self, view: "ClankerDraftView"):
        super().__init__(title="Optional token details")
        self.view_ref = view
        self.description_input = discord.ui.TextInput(
            label="Description (optional)",
            default=view.draft.get("description") or "",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
        )
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.view_ref.draft["description"] = str(self.description_input.value).strip() or None
        await self.view_ref.refresh(interaction, "Optional details saved.")


class ClankerAirdropModal(discord.ui.Modal):
    def __init__(self, view: "ClankerDraftView"):
        super().__init__(title="Optional Clanker airdrop")
        self.view_ref = view
        airdrop = view.draft.get("airdrop") or {}
        recipient_default = "\n".join(
            f"{row['address']}={row.get('input') or row['amount']}" for row in airdrop.get("recipients", [])[:20]
        )
        self.recipients_input = discord.ui.TextInput(
            label="Recipients: address=amount or address=percent",
            default=recipient_default,
            placeholder="0xabc...=1%\n0xdef...=250000000",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1800,
        )
        self.total_input = discord.ui.TextInput(
            label="Total amount if using prebuilt Merkle root",
            default=str(airdrop.get("total_input") or ""),
            placeholder="1.5% or 1500000000",
            required=False,
            max_length=40,
        )
        self.root_input = discord.ui.TextInput(
            label="Merkle root (required to submit)",
            default=airdrop.get("merkleRoot") or "",
            required=False,
            max_length=66,
        )
        self.lockup_input = discord.ui.TextInput(
            label="Lockup seconds",
            default=str(airdrop.get("lockupDuration") or MIN_AIRDROP_LOCKUP_SECONDS),
            required=False,
            max_length=12,
        )
        self.vesting_input = discord.ui.TextInput(
            label="Vesting seconds (optional, 0 disables)",
            default=str(airdrop.get("vestingDuration") or 0),
            required=False,
            max_length=12,
        )
        for item in (self.recipients_input, self.total_input, self.root_input, self.lockup_input, self.vesting_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        supply = int(self.view_ref.draft.get("supply") or DEFAULT_CLANKER_SUPPLY)
        try:
            recipients, recipients_total = parse_airdrop_lines(str(self.recipients_input.value), supply)
            total_input = str(self.total_input.value).strip()
            if total_input:
                total_amount, total_label = parse_token_amount(total_input, supply)
            else:
                total_amount, total_label = recipients_total, "recipient total"
            merkle_root = str(self.root_input.value).strip() or None
            if merkle_root and not MERKLE_ROOT_RE.fullmatch(merkle_root):
                raise ValueError("Merkle root must be 0x plus 64 hex characters.")
            if recipients and total_input and total_amount != recipients_total:
                raise ValueError("Airdrop total does not match the sum of recipient rows.")
            lockup = normalize_seconds(
                str(self.lockup_input.value),
                MIN_AIRDROP_LOCKUP_SECONDS,
                MIN_AIRDROP_LOCKUP_SECONDS,
            )
            vesting = normalize_seconds(str(self.vesting_input.value), 0, 0)
            max_amount = int((Decimal(supply) * Decimal(MAX_EXTENSION_PERCENTAGE)) / Decimal(100))
            if total_amount <= 0:
                raise ValueError("Provide recipient rows or a total airdrop amount, or use Clear Airdrop.")
            if total_amount > max_amount:
                raise ValueError("Airdrop allocation cannot exceed 90% of supply.")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.view_ref.draft["airdrop"] = {
            "recipients": recipients,
            "amount": total_amount,
            "total_input": total_input or None,
            "total_label": total_label,
            "merkleRoot": merkle_root,
            "lockupDuration": lockup,
            "vestingDuration": vesting,
            "preview_only": bool(recipients and not merkle_root),
        }
        message = "Airdrop saved."
        if recipients and not merkle_root:
            message += " Recipient lists are preview-only until a matching Merkle root/proofs are generated."
        await self.view_ref.refresh(interaction, message)


class ClankerDraftView(discord.ui.View):
    def __init__(self, cog: "Clanker", ctx: commands.Context, settings: Dict[str, Any]):
        super().__init__(timeout=900)
        self.cog = cog
        self.ctx = ctx
        self.settings = settings
        self.user_id = ctx.author.id
        self.processing = False
        self.live_confirmed = False
        self.draft: Dict[str, Any] = {
            "name": None,
            "symbol": None,
            "supply": DEFAULT_CLANKER_SUPPLY,
            "primary_beneficiary": None,
            "image_url": None,
            "description": None,
            "airdrop": None,
        }

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Only the launch-card creator can edit this draft.", ephemeral=True)
        return False

    def is_ready(self) -> bool:
        return all(self.draft.get(key) for key in ("name", "symbol", "supply", "primary_beneficiary"))

    def airdrop_submit_blocker(self) -> Optional[str]:
        airdrop = self.draft.get("airdrop")
        if not airdrop:
            return None
        if airdrop.get("amount", 0) <= 0:
            return "Airdrop amount must be positive."
        if not airdrop.get("merkleRoot"):
            return "Airdrop recipient lists need a Merkle root before live submission. Preview/export is available now."
        return None

    def build_current_payload(self) -> Dict[str, Any]:
        airdrop = self.draft.get("airdrop") or {}
        return self.cog.build_payload(
            self.draft["symbol"],
            self.draft["name"],
            int(self.draft["supply"]),
            self.draft["primary_beneficiary"],
            self.settings["treasury_address"],
            int(self.settings["platform_bps"]),
            bool(airdrop),
            airdrop.get("merkleRoot"),
            int(airdrop.get("amount") or 0),
            int(airdrop.get("lockupDuration") or MIN_AIRDROP_LOCKUP_SECONDS),
            int(airdrop.get("vestingDuration") or 0),
            self.settings.get("airdrop_admin"),
            self.user_id,
            self.draft.get("image_url"),
            self.draft.get("description"),
        )

    def embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Clanker launch draft",
            description="Fill the card, preview the payload, then submit when ready.",
            color=discord.Color.blurple(),
        )
        token = "Not set"
        if self.draft.get("name") or self.draft.get("symbol"):
            token = f"{self.draft.get('name') or 'Unnamed'} (${self.draft.get('symbol') or '?'})"
        embed.add_field(name="Token", value=token, inline=False)
        embed.add_field(name="Supply", value=format_tokens(int(self.draft.get("supply") or DEFAULT_CLANKER_SUPPLY)), inline=True)
        embed.add_field(name="Creator wallet", value=self.draft.get("primary_beneficiary") or "Not set", inline=False)
        embed.add_field(name="Image", value="Set" if self.draft.get("image_url") else "Not set", inline=True)
        embed.add_field(name="Description", value="Set" if self.draft.get("description") else "Not set", inline=True)
        embed.add_field(
            name="Creator rewards",
            value=f"Creator {10000 - int(self.settings['platform_bps'])} bps / platform {int(self.settings['platform_bps'])} bps",
            inline=False,
        )
        airdrop = self.draft.get("airdrop")
        if airdrop:
            status = "ready for submit" if airdrop.get("merkleRoot") else "preview only: Merkle root needed"
            embed.add_field(
                name="Airdrop",
                value=(
                    f"{format_tokens(int(airdrop['amount']))} tokens · {len(airdrop.get('recipients', []))} listed recipients · "
                    f"lock {airdrop['lockupDuration']}s · {status}"
                ),
                inline=False,
            )
        else:
            embed.add_field(name="Airdrop", value="Disabled / optional", inline=False)
        embed.add_field(name="Status", value="Ready" if self.is_ready() else "Needs basics", inline=True)
        if self.settings.get("submit_enabled"):
            embed.add_field(
                name="Live submit confirmation",
                value="Armed for next submit click" if self.live_confirmed else "Required before live API call",
                inline=True,
            )
        embed.set_footer(text="Base chain only · no private keys stored · airdrops allocate supply, rewards split LP/creator fees")
        if self.draft.get("image_url"):
            embed.set_thumbnail(url=self.draft["image_url"])
        return embed

    async def refresh(self, interaction: discord.Interaction, message: str):
        self.live_confirmed = False
        await interaction.response.edit_message(embed=self.embed(), view=self)
        await interaction.followup.send(message, ephemeral=True)

    def live_confirmation_summary(self) -> str:
        airdrop = self.draft.get("airdrop") or {}
        lines = [
            "Live Clanker API submission is enabled for this server.",
            "Review this summary, then click **Submit Launch** again to send it.",
            "",
            f"Token: {self.draft.get('name')} (${self.draft.get('symbol')})",
            f"Supply: {format_tokens(int(self.draft.get('supply') or 0))}",
            f"Creator/token admin: {self.draft.get('primary_beneficiary')}",
            f"Creator rewards: {10000 - int(self.settings['platform_bps'])} bps creator / {int(self.settings['platform_bps'])} bps platform",
            f"Platform treasury: {self.settings.get('treasury_address')}",
            f"API URL: {self.settings.get('api_base_url') or 'Not configured'}",
        ]
        if airdrop:
            lines.extend(
                [
                    f"Airdrop amount: {format_tokens(int(airdrop.get('amount') or 0))}",
                    f"Airdrop lockup: {int(airdrop.get('lockupDuration') or MIN_AIRDROP_LOCKUP_SECONDS)} seconds",
                    f"Airdrop vesting: {int(airdrop.get('vestingDuration') or 0)} seconds",
                    f"Airdrop Merkle root: {airdrop.get('merkleRoot') or 'Missing'}",
                ]
            )
        else:
            lines.append("Airdrop: disabled")
        return "\n".join(lines)

    def disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Edit Basics", emoji="🧾", style=discord.ButtonStyle.primary)
    async def edit_basics(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ClankerBasicsModal(self))

    @discord.ui.button(label="Optional Details", emoji="📝", style=discord.ButtonStyle.secondary)
    async def optional_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ClankerDetailsModal(self))

    @discord.ui.button(label="Airdrop", emoji="🎁", style=discord.ButtonStyle.secondary)
    async def edit_airdrop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ClankerAirdropModal(self))

    @discord.ui.button(label="Preview Payload", emoji="🔍", style=discord.ButtonStyle.secondary)
    async def preview_payload(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_ready():
            await interaction.response.send_message("Fill out token basics before previewing the payload.", ephemeral=True)
            return
        payload = self.build_current_payload()
        await interaction.response.send_message(box(json.dumps(payload, indent=2)[:1800], lang="json"), ephemeral=True)

    @discord.ui.button(label="Submit Launch", emoji="🚀", style=discord.ButtonStyle.success)
    async def submit_launch(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.processing:
            await interaction.response.send_message("This launch draft is already being processed.", ephemeral=True)
            return
        if not self.is_ready():
            await interaction.response.send_message("Fill out token basics before submitting.", ephemeral=True)
            return
        if not self.settings.get("treasury_address"):
            await interaction.response.send_message("A bot owner must configure the SickGaming treasury address first.", ephemeral=True)
            return
        blocker = self.airdrop_submit_blocker()
        if blocker:
            await interaction.response.send_message(blocker, ephemeral=True)
            return
        if self.settings.get("submit_enabled") and not self.live_confirmed:
            self.live_confirmed = True
            await interaction.response.edit_message(embed=self.embed(), view=self)
            await interaction.followup.send(self.live_confirmation_summary(), ephemeral=True)
            return
        self.processing = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            payload = self.build_current_payload()
            record = self.cog.build_audit_record(interaction.user, payload)
            if self.settings.get("submit_enabled"):
                if not self.settings.get("api_base_url"):
                    await interaction.followup.send("Submit mode is enabled but no Clanker API URL is configured.", ephemeral=True)
                    return
                token = await self.cog.get_api_token()
                if not token:
                    await interaction.followup.send("Submit mode is enabled but no Clanker API token is configured.", ephemeral=True)
                    return
                try:
                    record["api_response"] = await self.cog.submit_payload(self.settings["api_base_url"], token, payload)
                    record["api_refs"] = self.cog.extract_api_references(record["api_response"])
                    record["status"] = "submitted"
                except RuntimeError as exc:
                    record["status"] = "failed"
                    await self.cog.add_audit_record(self.ctx.guild, record)
                    await interaction.followup.send(str(exc), ephemeral=True)
                    return
            await self.cog.add_audit_record(self.ctx.guild, record)
            self.disable_controls()
            await interaction.message.edit(embed=self.embed(), view=self)
            await interaction.followup.send(
                "Clanker launch submitted." if record["status"] == "submitted" else "Clanker launch prepared as a dry-run audit record.",
                ephemeral=True,
            )
        finally:
            self.processing = False

    @discord.ui.button(label="Clear Airdrop", emoji="🧹", style=discord.ButtonStyle.secondary, row=1)
    async def clear_airdrop(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.draft["airdrop"] = None
        await self.refresh(interaction, "Airdrop cleared.")

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.disable_controls()
        await interaction.response.edit_message(embed=self.embed(), view=self)
        await interaction.followup.send("Clanker launch draft canceled.", ephemeral=True)


class Clanker(commands.Cog):
    """Prepare and optionally submit Clanker token launch requests on Base."""

    __author__ = ["SickProdigy", "chatgpt-codex"]
    __version__ = "0.1.0"

    default_guild = {
        "enabled": False,
        "api_base_url": None,
        "submit_enabled": False,
        "treasury_address": None,
        "platform_bps": 2000,
        "airdrop_enabled": False,
        "airdrop_merkle_root": None,
        "airdrop_amount": 0,
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

    async def add_audit_record(self, guild: discord.Guild, record: Dict[str, Any]):
        async with self.config.guild(guild).audit_log() as audit_log:
            audit_log.append(record)
            del audit_log[:-MAX_AUDIT_RECORDS]

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
            embed.add_field(
                name="Airdrop",
                value=(
                    f"{record.get('airdrop_amount')} tokens · "
                    f"root {record.get('airdrop_merkle_root') or 'missing'}"
                ),
                inline=False,
            )
        refs = record.get("api_refs") or {}
        if refs:
            reference_lines = [f"{key}: {value}" for key, value in refs.items()]
            embed.add_field(name="API references", value=box("\n".join(reference_lines)[:900]), inline=False)
        elif record.get("api_response"):
            embed.add_field(name="API response", value=box(str(record["api_response"])[:900]), inline=False)
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
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def clanker_card(self, ctx: commands.Context):
        """Open an interactive launch-card draft with optional airdrops."""
        settings = await self.config.guild(ctx.guild).all()
        if not settings["enabled"]:
            await ctx.send("Clanker launch requests are disabled in this server.")
            return
        if not settings["treasury_address"]:
            await ctx.send("A bot owner must configure the SickGaming treasury address first.")
            return
        view = ClankerDraftView(self, ctx, settings)
        await ctx.send(embed=view.embed(), view=view)

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
                    record["api_refs"] = self.extract_api_references(record["api_response"])
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

    @clankerset.group(name="airdrop")
    async def clankerset_airdrop(self, ctx: commands.Context):
        """Manage optional airdrop defaults for launch requests."""
        pass

    @clankerset_airdrop.command(name="enabled")
    async def clankerset_airdrop_enabled(self, ctx: commands.Context, enabled: bool):
        """Enable or disable a configured Clanker airdrop."""
        await self.config.guild(ctx.guild).airdrop_enabled.set(enabled)
        await ctx.send(f"Airdrop defaults are now {'enabled' if enabled else 'disabled'}.")

    @clankerset_airdrop.command(name="root")
    async def clankerset_airdrop_root(self, ctx: commands.Context, merkle_root: str):
        """Set the Merkle root for a prepared airdrop recipient list."""
        merkle_root = merkle_root.strip()
        if not MERKLE_ROOT_RE.fullmatch(merkle_root):
            await ctx.send("Merkle root must be a 32-byte hex string, like 0x plus 64 hex characters.")
            return
        await self.config.guild(ctx.guild).airdrop_merkle_root.set(merkle_root)
        await ctx.send("Airdrop Merkle root saved.")

    @clankerset_airdrop.command(name="amount")
    async def clankerset_airdrop_amount(self, ctx: commands.Context, amount: commands.Range[int, 0, 10**18]):
        """Set total token amount reserved for the configured airdrop."""
        await self.config.guild(ctx.guild).airdrop_amount.set(amount)
        await ctx.send(f"Airdrop amount set to {amount} tokens.")

    @clankerset_airdrop.command(name="lockup")
    async def clankerset_airdrop_lockup(self, ctx: commands.Context, seconds: int):
        """Set airdrop lockup in seconds; Clanker minimum is 7 days."""
        if seconds < MIN_AIRDROP_LOCKUP_SECONDS or seconds > 315360000:
            await ctx.send("Airdrop lockup must be between 604800 and 315360000 seconds.")
            return
        await self.config.guild(ctx.guild).airdrop_lockup_seconds.set(seconds)
        await ctx.send(f"Airdrop lockup set to {seconds} seconds.")

    @clankerset_airdrop.command(name="vesting")
    async def clankerset_airdrop_vesting(self, ctx: commands.Context, seconds: commands.Range[int, 0, 315360000]):
        """Set optional airdrop vesting duration in seconds; 0 disables vesting."""
        await self.config.guild(ctx.guild).airdrop_vesting_seconds.set(seconds)
        await ctx.send(f"Airdrop vesting set to {seconds} seconds.")

    @clankerset_airdrop.command(name="admin")
    async def clankerset_airdrop_admin(self, ctx: commands.Context, admin_address: Optional[str] = None):
        """Set an optional airdrop admin address; omit to clear it."""
        if not admin_address:
            await self.config.guild(ctx.guild).airdrop_admin.set(None)
            await ctx.send("Airdrop admin cleared; Clanker will use its default.")
            return
        admin_address = admin_address.strip()
        if not is_eth_address(admin_address):
            await ctx.send("Airdrop admin must be a valid EVM address.")
            return
        await self.config.guild(ctx.guild).airdrop_admin.set(admin_address)
        await ctx.send("Airdrop admin saved.")


    @clankerset.group(name="audit")
    async def clankerset_audit(self, ctx: commands.Context):
        """Manage Clanker audit records."""
        pass

    @clankerset_audit.command(name="clear")
    async def clankerset_audit_clear(self, ctx: commands.Context):
        """Clear Clanker audit records for this guild."""
        await self.config.guild(ctx.guild).audit_log.set([])
        await ctx.send("Clanker audit log cleared.")
