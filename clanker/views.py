import copy
import json
from typing import Any, Dict, Optional, TYPE_CHECKING

import discord
from redbot.core import commands
from redbot.core.utils.chat_formatting import box, humanize_list

from .constants import (
    DEFAULT_CLANKER_SUPPLY,
    MERKLE_ROOT_RE,
    MIN_AIRDROP_LOCKUP_SECONDS,
    MIN_VAULT_LOCKUP_SECONDS,
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
            max_length=64,
        )
        self.symbol_input = discord.ui.TextInput(
            label="Symbol / ticker",
            default=view.draft.get("symbol") or "",
            min_length=2,
            max_length=12,
        )
        self.creator_input = discord.ui.TextInput(
            label="Creator wallet / token admin",
            default=view.draft.get("primary_beneficiary") or "",
            required=False,
            max_length=42,
        )
        self.creator_treasury_input = discord.ui.TextInput(
            label="Creator reward treasury",
            default=view.draft.get("creator_reward_recipient") or view.draft.get("primary_beneficiary") or "",
            required=False,
            max_length=42,
        )
        self.image_input = discord.ui.TextInput(
            label="Image URL (optional HTTPS)",
            default=view.draft.get("image_url") or "",
            required=False,
            max_length=300,
        )
        for item in (self.name_input, self.symbol_input, self.creator_input, self.creator_treasury_input, self.image_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            symbol = str(self.symbol_input.value).strip().upper().lstrip("$")
            creator = str(self.creator_input.value).strip()
            creator_treasury = str(self.creator_treasury_input.value).strip()
            image_url = str(self.image_input.value).strip()
            if not SYMBOL_RE.fullmatch(symbol):
                raise ValueError("Token symbols must be 2-12 uppercase letters or numbers.")
            if not str(self.name_input.value).strip():
                raise ValueError("Token name is required.")
            if creator and not is_eth_address(creator):
                raise ValueError("Token administrator must be a valid EVM address.")
            if creator_treasury and not is_eth_address(creator_treasury):
                raise ValueError("Creator reward treasury must be a valid EVM address.")
            if image_url:
                await self.view_ref.cog.validate_remote_image(image_url)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.view_ref.draft.update(
            {
                "name": str(self.name_input.value).strip(),
                "symbol": symbol,
                "supply": DEFAULT_CLANKER_SUPPLY,
                "primary_beneficiary": creator or None,
                "creator_reward_recipient": creator_treasury or None,
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
    MIN_VAULT_LOCKUP_SECONDS,
                MIN_AIRDROP_LOCKUP_SECONDS,
    MIN_VAULT_LOCKUP_SECONDS,
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


class ClankerRewardReviewView(discord.ui.View):
    """Owner-bound controls for one coin; never performs a portfolio sweep."""

    def __init__(self, cog: "Clanker", record: Dict[str, Any], user_id: int, guild_id: int):
        super().__init__(timeout=900)
        self.cog = cog
        self.record = copy.deepcopy(record)
        self.user_id = int(user_id)
        self.guild_id = int(guild_id)
        self.processing = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the person who opened this reward review can use it.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Collect this coin", emoji="📥", style=discord.ButtonStyle.success)
    async def collect(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.processing:
            await interaction.response.send_message("This collection is already processing.", ephemeral=True)
            return
        self.processing = True
        button.disabled = True
        await interaction.response.edit_message(view=self)
        try:
            result = await self.cog.collect_launch_rewards_internal(interaction.user, self.record, self.guild_id)
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            self.processing = False
            button.disabled = False
            await interaction.edit_original_response(view=self)
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        status = str(result["provider_status"])
        operation = str(result.get("user_operation_hash") or "")
        transaction = str(result.get("transaction_hash") or "")
        lines = ["Submitted collection for this coin only. Status: " + chr(96) + status + chr(96) + "."]
        if transaction:
            lines.append("[Transaction](https://sepolia.basescan.org/tx/" + transaction + ")")
        elif operation:
            lines.append("Operation: " + chr(96) + operation[:10] + "…" + operation[-8:] + chr(96))
        lines.append("No treasury-wide deposited rewards were withdrawn.")
        await interaction.followup.send(chr(10).join(lines), ephemeral=True)


class ClankerReceiptRewardsView(discord.ui.View):
    """Open a private reward preflight scoped to one confirmed launch."""

    def __init__(self, cog: "Clanker", record: Dict[str, Any], guild_id: int):
        super().__init__(timeout=900)
        self.cog = cog
        self.record = copy.deepcopy(record)
        self.guild_id = int(guild_id)

    @discord.ui.button(label="Rewards", emoji="\U0001f4b0", style=discord.ButtonStyle.primary)
    async def rewards(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            embed = await self.cog.reward_preflight_embed([self.record], portfolio=False)
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await interaction.followup.send(
                "Clanker rewards are temporarily unavailable: {}".format(exc),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=embed,
            view=ClankerRewardReviewView(self.cog, self.record, interaction.user.id, self.guild_id),
            ephemeral=True,
        )


class ClankerVerifiedView(discord.ui.View):
    """Owner-bound launch controls for one immutable reviewed operation."""

    def __init__(
        self, cog: "Clanker", ctx: commands.Context, record: Dict[str, Any],
        settings: Dict[str, Any], draft: Dict[str, Any],
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.ctx = ctx
        self.record = record
        self.settings = settings
        self.draft = copy.deepcopy(draft)
        self.user_id = ctx.author.id
        self.processing = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the person who verified this launch can use its controls.",
            ephemeral=True,
        )
        return False

    def embed(self) -> discord.Embed:
        record = self.record
        payload = record["payload"]
        creator_bps = int(record.get("creator_bps", 0))
        platform_bps = int(record.get("platform_bps", 0))
        if creator_bps + platform_bps != 10_000:
            creator_bps = 10_000 - platform_bps
        creator_recipient = (
            record.get("creator_reward_recipient") or payload["tokenAdmin"]
        )
        platform_treasury = record.get("platform_treasury")
        embed = discord.Embed(
            title="Verified Clanker launch",
            description=(
                "These values are frozen for this card. Review them carefully, then "
                "launch with your authorized CryptoWallet."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Token", value=f"{record['name']} (${record['symbol']})", inline=False
        )
        embed.add_field(name="Network", value="Base Sepolia", inline=True)
        embed.add_field(name="Supply", value=format_tokens(DEFAULT_CLANKER_SUPPLY), inline=True)
        embed.add_field(name="Token administrator", value=payload["tokenAdmin"], inline=False)
        terms = record["execution_terms"]
        embed.add_field(
            name="Gas limit", value=f"{int(terms['gas_limit']):,} gas", inline=True
        )
        embed.add_field(
            name="Native value",
            value=(
                "0 ETH" if int(terms["native_value_wei"]) == 0
                else f"{int(terms['native_value_wei'])} wei"
            ),
            inline=True,
        )
        embed.add_field(
            name="Gas payment",
            value=(
                f"Sponsored by {terms['gas_payer']} (no wallet gas charge)"
                if terms["gas_sponsored"]
                else f"Paid by {terms['gas_payer']}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Creator reward recipient",
            value=str(creator_recipient),
            inline=False,
        )
        embed.add_field(
            name="Creator reward share", value=f"{creator_bps} bps", inline=True
        )
        embed.add_field(
            name="Platform reward share", value=f"{platform_bps} bps", inline=True
        )
        embed.add_field(
            name="Platform treasury", value=str(platform_treasury), inline=False
        )
        vault = payload.get("vault")
        embed.add_field(
            name="Vault",
            value=(
                f"{vault['percentage']}% → {vault['recipient']} · lock {vault['lockupDuration']}s"
                if vault else "Disabled"
            ),
            inline=False,
        )
        airdrop = payload.get("airdrop")
        embed.add_field(
            name="Airdrop",
            value=(
                f"{format_tokens(int(airdrop['amount']))} · root {airdrop['merkleRoot']}"
                if airdrop else "Disabled"
            ),
            inline=False,
        )
        embed.add_field(
            name="Status",
            value=(
                "Submission outcome unknown — status recovery required"
                if record.get("status") == "internal_uncertain"
                else "Verified and not submitted"
            ),
            inline=False,
        )
        embed.add_field(name="Launch ID", value=f"`{record.get('launch_ref') or record['launch_id']}`", inline=False)
        embed.add_field(
            name="Payload fingerprint",
            value=f"`{record['payload_hash']}`",
            inline=False,
        )
        embed.set_footer(
            text="Immutable review · Base Sepolia only · launching cannot be undone"
        )
        if payload.get("image"):
            embed.set_thumbnail(url=payload["image"])
        return embed

    def disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    @discord.ui.button(
        label="Back to Edit", emoji="↩️", style=discord.ButtonStyle.secondary
    )
    async def back_to_edit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.processing:
            await interaction.response.send_message(
                "This verified launch is already being processed.", ephemeral=True
            )
            return
        self.processing = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.cog.discard_verified_draft(
                self.ctx.guild, interaction.user, str(self.record["launch_id"])
            )
            draft_view = ClankerDraftView(self.cog, self.ctx, self.settings)
            draft_view.draft = copy.deepcopy(self.draft)
            self.disable_controls()
            await interaction.message.edit(embed=draft_view.embed(), view=draft_view)
            await interaction.followup.send(
                "Returned to editing. The previous verification was discarded; verify "
                "again after making changes.",
                ephemeral=True,
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        finally:
            self.processing = False

    @discord.ui.button(
        label="Launch with CryptoWallet", emoji="🚀", style=discord.ButtonStyle.success
    )
    async def launch_internal(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.processing:
            await interaction.response.send_message(
                "This verified launch is already being processed.", ephemeral=True
            )
            return
        self.processing = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.cog.launch_verified_internal(interaction.user, self.record)
            await self.cog.mark_verified_internal_result(
                self.ctx.guild, str(self.record["launch_id"]), result
            )
            if result["status"] == "authorization_required":
                await interaction.followup.send(
                    "CryptoWallet authorization is missing or expired. I sent its protected "
                    "authorization link by DM. Complete it, then press this Launch button again "
                    "before the verified card expires.",
                    ephemeral=True,
                )
                return
            self.record["status"] = "internal_" + result["status"]
            self.disable_controls()
            await interaction.message.edit(embed=self.embed(), view=self)
            if result["status"] == "submitted":
                await self.cog.schedule_internal_confirmation(
                    self.ctx.guild, interaction.user, self.record, interaction.message
                )
            detail = result.get("transaction_hash") or result.get("user_operation_hash")
            if result["status"] == "uncertain":
                message = (
                    "CryptoWallet could not prove whether the provider accepted this launch. "
                    f"Do not create another one. Use `{self.ctx.clean_prefix}clanker refresh "
                    f"{self.record.get('launch_ref') or self.record['launch_id']}` to recover the same submission attempt."
                )
            else:
                message = (
                    f"Clanker launch `{result['status']}`."
                    + (f" Operation: `{detail}`" if detail else "")
                )
            await interaction.followup.send(message, ephemeral=True)
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await interaction.followup.send(
                f"Clanker launch was not submitted: {exc}", ephemeral=True
            )
        finally:
            self.processing = False

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.disable_controls()
        await interaction.response.edit_message(embed=self.embed(), view=self)
        await interaction.followup.send(
            "Verified launch canceled. Nothing was submitted.", ephemeral=True
        )


class ClankerDraftView(discord.ui.View):
    def __init__(
        self,
        cog: "Clanker",
        ctx: commands.Context,
        settings: Dict[str, Any],
        *,
        symbol: Optional[str] = None,
        name: Optional[str] = None,
        creator_address: Optional[str] = None,
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.ctx = ctx
        self.settings = settings
        self.user_id = ctx.author.id
        self.processing = False
        self.draft: Dict[str, Any] = {
            "name": name,
            "symbol": symbol,
            "supply": DEFAULT_CLANKER_SUPPLY,
            "primary_beneficiary": creator_address,
            "creator_reward_recipient": creator_address,
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
        return all(self.draft.get(key) for key in ("name", "symbol", "supply"))

    def airdrop_execution_blocker(self) -> Optional[str]:
        airdrop = self.draft.get("airdrop")
        if not airdrop:
            return None
        if airdrop.get("amount", 0) <= 0:
            return "Airdrop amount must be positive."
        if not airdrop.get("merkleRoot"):
            return "Airdrop recipient lists need a Merkle root before execution. Preview/export is available now."
        return None

    def build_current_payload(self) -> Dict[str, Any]:
        airdrop = self.draft.get("airdrop") or {}
        return self.cog.build_draft_payload(
            self.draft["symbol"],
            self.draft["name"],
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
            bool(self.settings.get("vault_enabled")),
            int(self.settings.get("vault_percentage") or 0),
            int(self.settings.get("vault_lockup_seconds") or MIN_VAULT_LOCKUP_SECONDS),
            int(self.settings.get("vault_vesting_seconds") or 0),
            self.settings.get("vault_recipient"),
            creator_reward_recipient=self.draft.get("creator_reward_recipient"),
        )

    def embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Clanker launch draft",
            description="Fill the card, preview it, then verify the exact launch.",
            color=discord.Color.blurple(),
        )
        token = "Not set"
        if self.draft.get("name") or self.draft.get("symbol"):
            token = f"{self.draft.get('name') or 'Unnamed'} (${self.draft.get('symbol') or '?'})"
        embed.add_field(name="Token", value=token, inline=False)
        embed.add_field(name="Supply", value=f"{format_tokens(DEFAULT_CLANKER_SUPPLY)} (fixed v4)", inline=True)
        embed.add_field(name="Token administrator", value=self.draft.get("primary_beneficiary") or "Signer wallet (resolved at execution)", inline=False)
        embed.add_field(name="Image", value="Set" if self.draft.get("image_url") else "Not set", inline=True)
        embed.add_field(name="Description", value="Set" if self.draft.get("description") else "Not set", inline=True)
        embed.add_field(
            name="Creator rewards",
            value=f"Creator {10000 - int(self.settings['platform_bps'])} bps / platform {int(self.settings['platform_bps'])} bps",
            inline=False,
        )
        embed.add_field(name="Creator reward treasury", value=self.draft.get("creator_reward_recipient") or "Signer wallet (resolved at execution)", inline=False)
        if self.settings.get("vault_enabled"):
            vault_percentage = int(self.settings.get("vault_percentage") or 0)
            vault_lockup = int(self.settings.get("vault_lockup_seconds") or MIN_VAULT_LOCKUP_SECONDS)
            vault_recipient = self.settings.get("vault_recipient") or self.draft.get("primary_beneficiary") or "token admin"
            embed.add_field(
                name="Vault",
                value=f"{vault_percentage}% · lock {vault_lockup}s · recipient {vault_recipient}",
                inline=False,
            )
        else:
            embed.add_field(name="Vault", value="Disabled / optional", inline=False)
        airdrop = self.draft.get("airdrop")
        if airdrop:
            status = "ready for execution" if airdrop.get("merkleRoot") else "preview only: Merkle root needed"
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
        embed.set_footer(text="Base Sepolia only · no private keys stored · airdrops allocate supply, rewards split LP/creator fees")
        if self.draft.get("image_url"):
            embed.set_thumbnail(url=self.draft["image_url"])
        return embed

    async def refresh(self, interaction: discord.Interaction, message: str):
        await interaction.response.edit_message(embed=self.embed(), view=self)
        await interaction.followup.send(message, ephemeral=True)

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
        try:
            payload = self.build_current_payload()
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(box(json.dumps(payload, indent=2)[:1800], lang="json"), ephemeral=True)

    @discord.ui.button(label="Verify", emoji="✅", style=discord.ButtonStyle.success)
    async def save_launch(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.processing:
            await interaction.response.send_message(
                "This launch draft is already being verified.", ephemeral=True
            )
            return
        if not self.is_ready():
            await interaction.response.send_message(
                "Fill out token basics before verification.", ephemeral=True
            )
            return
        if not self.settings.get("treasury_address"):
            await interaction.response.send_message(
                "A bot owner must configure the SickGaming treasury address first.",
                ephemeral=True,
            )
            return
        blocker = self.airdrop_execution_blocker()
        if blocker:
            await interaction.response.send_message(blocker, ephemeral=True)
            return
        self.processing = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            payload = self.build_current_payload()
            record = self.cog.build_draft_record(interaction.user, payload, self.ctx.guild.id)
            wallet = self.cog.bot.get_cog("CryptoWallet")
            resolve_address = (
                getattr(wallet, "clanker_requester_address", None) if wallet else None
            )
            if not callable(resolve_address):
                raise RuntimeError("CryptoWallet public-address resolution is unavailable.")
            signer_address = await resolve_address(interaction.user)
            get_terms = getattr(wallet, "clanker_execution_terms", None)
            if not callable(get_terms):
                raise RuntimeError("CryptoWallet Clanker spending policy is unavailable.")
            execution_terms = get_terms()
            if not isinstance(execution_terms, dict):
                raise RuntimeError("CryptoWallet returned an invalid Clanker spending policy.")
            record["execution_terms"] = execution_terms
            await self.cog.add_audit_record(self.ctx.guild, record)
            record = await self.cog.prepare_draft_execution(
                self.ctx.guild, interaction.user, str(record["launch_id"]), signer_address
            )
            record = await self.cog.mark_draft_verified(
                self.ctx.guild, interaction.user, str(record["launch_id"])
            )
            await self.cog.notify_approval_channel(
                self.ctx.guild, self.settings, record
            )
            self.disable_controls()
            verified_view = ClankerVerifiedView(
                self.cog, self.ctx, record, self.settings, self.draft
            )
            await interaction.message.edit(
                embed=verified_view.embed(), view=verified_view
            )
            await interaction.followup.send(
                "Verified card created. Review every frozen value, then use its Launch "
                "button. Any changes require a new card.",
                ephemeral=True,
            )
        except (ValueError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
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
