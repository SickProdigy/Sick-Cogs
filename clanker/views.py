import json
from typing import Any, Dict, Optional, TYPE_CHECKING

import discord
from redbot.core import commands
from redbot.core.utils.chat_formatting import box, humanize_list

from .constants import (
    DEFAULT_CLANKER_SUPPLY,
    MERKLE_ROOT_RE,
    MIN_AIRDROP_LOCKUP_SECONDS,
    SYMBOL_RE,
)
from .helpers import (
    build_airdrop_merkle_tree,
    format_tokens,
    is_eth_address,
    normalize_seconds,
    parse_airdrop_lines,
    parse_token_amount,
    validate_airdrop_total,
)

if TYPE_CHECKING:
    from .clanker import Clanker


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
            merkle_export = None
            generated_root = False
            if recipients:
                merkle_export = build_airdrop_merkle_tree(recipients)
                if not merkle_root:
                    merkle_root = merkle_export["root"]
                    generated_root = True
                elif merkle_root.lower() != merkle_export["root"].lower():
                    raise ValueError("Provided Merkle root does not match the recipient rows generated by this cog.")
            lockup = normalize_seconds(
                str(self.lockup_input.value),
                MIN_AIRDROP_LOCKUP_SECONDS,
                MIN_AIRDROP_LOCKUP_SECONDS,
            )
            vesting = normalize_seconds(str(self.vesting_input.value), 0, 0)
            validate_airdrop_total(total_amount, supply)
        except (ValueError, RuntimeError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.view_ref.draft["airdrop"] = {
            "recipients": recipients,
            "amount": total_amount,
            "total_input": total_input or None,
            "total_label": total_label,
            "merkleRoot": merkle_root,
            "merkleExport": merkle_export,
            "lockupDuration": lockup,
            "vestingDuration": vesting,
            "preview_only": False,
        }
        message = "Airdrop saved."
        if generated_root:
            message += " Merkle root/proofs generated from the recipient rows."
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
                if self.settings.get("approval_required"):
                    record["status"] = "pending_approval"
                else:
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
                        await self.cog.notify_approval_channel(self.ctx.guild, self.settings, record)
                        await interaction.followup.send(str(exc), ephemeral=True)
                        return
            await self.cog.add_audit_record(self.ctx.guild, record)
            await self.cog.notify_approval_channel(self.ctx.guild, self.settings, record)
            self.disable_controls()
            await interaction.message.edit(embed=self.embed(), view=self)
            messages = {
                "submitted": "Clanker launch submitted.",
                "pending_approval": "Clanker launch is pending owner approval before live submission.",
            }
            await interaction.followup.send(
                messages.get(record["status"], "Clanker launch prepared as a dry-run audit record."),
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
