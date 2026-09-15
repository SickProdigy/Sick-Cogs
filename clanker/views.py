import copy
import json
import re
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


_VAULT_DURATION_UNITS = {"h": 3600, "d": 86400, "w": 604800, "m": 2592000, "y": 31536000}


def parse_vault_duration(value: str, *, allow_zero: bool = False) -> int:
    raw = str(value or "").strip().lower()
    if allow_zero and raw in {"", "0", "none", "off"}:
        return 0
    match = re.fullmatch(r"([1-9][0-9]*)\s*([hdwmy])", raw)
    if not match:
        raise ValueError("Use a duration like 7d, 2w, 6m, or 1y (m means 30-day month).")
    return int(match.group(1)) * _VAULT_DURATION_UNITS[match.group(2)]


def format_vault_duration(seconds: int) -> str:
    amount = int(seconds or 0)
    if amount == 0:
        return "none"
    for unit, size in (("y", 31536000), ("m", 2592000), ("w", 604800), ("d", 86400), ("h", 3600)):
        if amount % size == 0:
            return str(amount // size) + unit
    return str(amount // 86400) + "d"


def describe_vault_duration(seconds: int) -> str:
    amount = int(seconds or 0)
    for singular, plural, size in (("year", "years", 31536000), ("month", "months", 2592000),
                                   ("week", "weeks", 604800), ("day", "days", 86400),
                                   ("hour", "hours", 3600)):
        if amount and amount % size == 0:
            count = amount // size
            return "{} {}".format(count, singular if count == 1 else plural)
    return format_vault_duration(amount)


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
            name = " ".join(str(self.name_input.value).strip().split())
            creator = str(self.creator_input.value).strip()
            creator_treasury = str(self.creator_treasury_input.value).strip()
            image_url = str(self.image_input.value).strip()
            if not SYMBOL_RE.fullmatch(symbol):
                raise ValueError("Token symbols must be 2-12 uppercase letters or numbers.")
            if not name or len(name.encode("utf-8")) > 64:
                raise ValueError("Token names must be 1-64 UTF-8 bytes.")
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
                "name": name,
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


class ClankerVaultModal(discord.ui.Modal):
    def __init__(self, view: "ClankerDraftView"):
        super().__init__(title="Optional token vault")
        self.view_ref = view
        vault = view.draft.get("vault") or {}
        self.percentage_input = discord.ui.TextInput(
            label="Supply percentage (blank disables)", default=str(vault.get("percentage") or ""),
            placeholder="Starter example: 10", required=False, max_length=2,
        )
        self.lockup_input = discord.ui.TextInput(
            label="Lockup duration (min 7d; d/w/m/y)",
            default=format_vault_duration(vault.get("lockupDuration") or 6 * 2592000),
            placeholder="Suggested: 6m", max_length=10,
        )
        self.vesting_input = discord.ui.TextInput(
            label="Vesting duration (optional; d/w/m/y)",
            default=(format_vault_duration(vault.get("vestingDuration"))
                     if vault.get("vestingDuration") else ""),
            placeholder="Blank = full unlock; example: 3m", max_length=10,
        )
        self.recipient_input = discord.ui.TextInput(
            label="Recipient (blank uses signer wallet)", default=str(vault.get("recipient") or ""),
            required=False, max_length=42,
        )
        for item in (self.percentage_input, self.lockup_input, self.vesting_input, self.recipient_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        raw_percentage = str(self.percentage_input.value).strip()
        if not raw_percentage:
            self.view_ref.draft["vault"] = None
            await self.view_ref.refresh(interaction, "Vault disabled for this launch.")
            return
        try:
            percentage = int(raw_percentage)
            lockup = parse_vault_duration(str(self.lockup_input.value))
            vesting = parse_vault_duration(str(self.vesting_input.value), allow_zero=True)
            recipient = str(self.recipient_input.value).strip()
            if not 1 <= percentage <= 90:
                raise ValueError("Vault percentage must be from 1 through 90.")
            if lockup < MIN_VAULT_LOCKUP_SECONDS:
                raise ValueError("Vault lockup must be at least seven days.")
            if not 0 <= vesting <= 315360000:
                raise ValueError("Vault vesting cannot exceed 10 years.")
            if recipient and not is_eth_address(recipient):
                raise ValueError("Vault recipient must be a valid EVM address.")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        airdrop_amount = int((self.view_ref.draft.get("airdrop") or {}).get("amount") or 0)
        supply = int(self.view_ref.draft.get("supply") or DEFAULT_CLANKER_SUPPLY)
        if percentage * 100 + airdrop_amount * 10_000 // supply > 9_000:
            await interaction.response.send_message(
                "Clanker vault and airdrop allocations cannot exceed 90% of supply.", ephemeral=True
            )
            return
        self.view_ref.draft["vault"] = {
            "percentage": percentage, "lockupDuration": lockup,
            "vestingDuration": vesting, "recipient": recipient or None,
        }
        await self.view_ref.refresh(interaction, "Vault saved for this launch.")


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


class ClankerTreasuryWithdrawalView(discord.ui.View):
    """Final approval for a clearly treasury-wide reward withdrawal."""

    def __init__(self, cog: "Clanker", record: Dict[str, Any], claims: list[dict], user_id: int, guild_id: int, *, platform_only: bool = False):
        super().__init__(timeout=900)
        self.cog = cog
        self.record = copy.deepcopy(record)
        self.claims = copy.deepcopy(claims)
        self.user_id = int(user_id)
        self.guild_id = int(guild_id)
        self.platform_only = bool(platform_only)
        self.processing = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Only the reviewing token administrator can approve this withdrawal.", ephemeral=True)
        return False

    @discord.ui.button(label="Withdraw treasury balances", emoji="🏦", style=discord.ButtonStyle.danger)
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.processing:
            await interaction.response.send_message("This withdrawal is already processing.", ephemeral=True)
            return
        self.processing = True
        button.disabled = True
        await interaction.response.edit_message(view=self)
        try:
            result = await (self.cog.withdraw_platform_treasury_internal(interaction.user, self.record, self.claims, self.guild_id)
                            if self.platform_only else self.cog.withdraw_launch_treasuries_internal(interaction.user, self.record, self.claims, self.guild_id))
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            self.processing = False
            button.disabled = False
            await interaction.edit_original_response(view=self)
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        detail = str(result.get("transaction_hash") or result.get("user_operation_hash") or "")
        await interaction.followup.send("Treasury-wide withdrawal submitted. Status: " + chr(96)
            + str(result.get("provider_status")) + chr(96)
            + (" · reference " + chr(96) + detail[:10] + "…" + detail[-8:] + chr(96) if detail else ""), ephemeral=True)


class ClankerRewardReviewView(discord.ui.View):
    """Owner-bound, one-approval reward claims for a single coin."""

    def __init__(
        self, cog: "Clanker", record: Dict[str, Any], user_id: int, guild_id: int,
        *, snapshot: Dict[str, Any],
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.record = copy.deepcopy(record)
        self.user_id = int(user_id)
        self.guild_id = int(guild_id)
        self.processing = False
        self.claim_token.label = "Claim $" + str(record.get("symbol") or "TOKEN").upper()
        self._set_snapshot(snapshot)

    def _set_snapshot(self, snapshot: Dict[str, Any]) -> None:
        self.snapshot = copy.deepcopy(snapshot)
        token = str(self.record.get("token_address") or "").lower()
        self.claims = [
            {"owner": row["owner"], "asset": row["asset"]}
            for row in self.snapshot.get("treasuries", [])
        ]
        self.weth_claims = [
            item for item in self.claims
            if item["asset"] == "0x4200000000000000000000000000000000000006"
        ]
        self.token_claims = [item for item in self.claims if item["asset"] == token]
        self.claim_all.disabled = not self.claims
        self.claim_weth.disabled = not self.weth_claims
        self.claim_token.disabled = not self.token_claims

    @staticmethod
    def _snapshot_fingerprint(snapshot: Dict[str, Any]) -> tuple:
        rows = tuple(sorted(
            (str(row.get("owner") or ""), str(row.get("asset") or ""),
             int(row.get("amount_wei") or 0), row.get("claim_gas"))
            for row in snapshot.get("treasuries", [])
        ))
        return int(snapshot.get("gas_price_wei") or 0), rows

    def _claims_for(self, scope: str) -> list[dict]:
        if scope == "weth":
            return self.weth_claims
        if scope == "token":
            return self.token_claims
        return self.claims

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the person who opened this reward review can use it.", ephemeral=True
        )
        return False

    async def _claim(self, interaction: discord.Interaction, scope: str, label: str):
        if self.processing:
            await interaction.response.send_message("A reward claim is already processing.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            embed, fresh = await self.cog.reward_preflight_embed(
                [self.record], portfolio=False, include_snapshot=True
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await interaction.followup.send("Could not recheck rewards: " + str(exc), ephemeral=True)
            return
        if self._snapshot_fingerprint(fresh) != self._snapshot_fingerprint(self.snapshot):
            self._set_snapshot(fresh)
            await interaction.edit_original_response(embed=embed, view=self)
            await interaction.followup.send(
                "Claimable balances or gas changed. The card was refreshed; review it and click again.",
                ephemeral=True,
            )
            return
        claims = self._claims_for(scope)
        if not claims:
            self._set_snapshot(fresh)
            await interaction.edit_original_response(embed=embed, view=self)
            await interaction.followup.send("There are no rewards available for that choice.", ephemeral=True)
            return
        self.processing = True
        self.claim_all.disabled = True
        self.claim_weth.disabled = True
        self.claim_token.disabled = True
        await interaction.edit_original_response(view=self)
        try:
            result = await self.cog.withdraw_launch_treasuries_internal(
                interaction.user, self.record, claims, self.guild_id
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            self.processing = False
            self._set_snapshot(self.snapshot)
            await interaction.edit_original_response(view=self)
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        status = str(result.get("provider_status") or "submitted")
        operation = str(result.get("user_operation_hash") or "")
        transaction = str(result.get("transaction_hash") or "")
        lines = [label + " submitted through CryptoWallet. Status: " + chr(96) + status + chr(96) + "."]
        if transaction:
            lines.append("[Transaction](https://sepolia.basescan.org/tx/" + transaction + ")")
        elif operation:
            lines.append("Operation: " + chr(96) + operation[:10] + "…" + operation[-8:] + chr(96))
        await interaction.followup.send(chr(10).join(lines), ephemeral=True)

    @discord.ui.button(label="Claim all rewards", emoji="💰", style=discord.ButtonStyle.success)
    async def claim_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._claim(interaction, "all", "All available rewards")

    @discord.ui.button(label="Claim WETH", style=discord.ButtonStyle.primary)
    async def claim_weth(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._claim(interaction, "weth", "WETH rewards")

    @discord.ui.button(label="Claim token", style=discord.ButtonStyle.primary)
    async def claim_token(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._claim(interaction, "token", self.claim_token.label + " rewards")


class ClankerClaimAllSelect(discord.ui.Select):
    def __init__(self, view_ref: "ClankerClaimAllView"):
        self.view_ref = view_ref
        page_records = view_ref.page_records()
        options = [discord.SelectOption(
            label=("$" + str(item.get("symbol") or "?").upper() + " · " + str(item.get("launch_ref") or item.get("launch_id")))[:100],
            value=str(item.get("launch_id")),
            default=str(item.get("launch_id")) in view_ref.selected_ids,
        ) for item in page_records]
        super().__init__(placeholder="Select tokens to collect on this page", min_values=0,
                         max_values=max(1, len(options)), options=options)

    async def callback(self, interaction: discord.Interaction):
        page_ids = {str(item.get("launch_id")) for item in self.view_ref.page_records()}
        self.view_ref.selected_ids.difference_update(page_ids)
        self.view_ref.selected_ids.update(self.values)
        await interaction.response.edit_message(view=self.view_ref)


class ClankerClaimAllView(discord.ui.View):
    """Paginated portfolio selection with separate collection and withdrawal actions."""

    def __init__(self, cog: "Clanker", records: list[dict], user_id: int, guild_id: int):
        super().__init__(timeout=900)
        self.cog = cog
        self.records = copy.deepcopy(records)
        self.user_id = int(user_id)
        self.guild_id = int(guild_id)
        self.page = 0
        self.selected_ids: set[str] = set()
        self._rebuild_select()

    def page_records(self):
        return self.records[self.page * 10:(self.page + 1) * 10]

    def _rebuild_select(self):
        for item in list(self.children):
            if isinstance(item, ClankerClaimAllSelect):
                self.remove_item(item)
        if self.page_records():
            self.add_item(ClankerClaimAllSelect(self))
        self.previous.disabled = self.page == 0
        self.next.disabled = (self.page + 1) * 10 >= len(self.records)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Only the portfolio owner can use these controls.", ephemeral=True)
        return False

    async def _show_page(self, interaction: discord.Interaction):
        self._rebuild_select()
        embed = await self.cog.reward_preflight_embed(self.records, portfolio=True, offset=self.page * 10)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=2)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await self._show_page(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=2)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        await self._show_page(interaction)

    @discord.ui.button(label="Collect selected", emoji="📥", style=discord.ButtonStyle.primary, row=3)
    async def collect_selected(self, interaction: discord.Interaction, button: discord.ui.Button):
        selected = [item for item in self.records if str(item.get("launch_id")) in self.selected_ids]
        if not selected:
            await interaction.response.send_message("Select at least one token first.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        submitted, failures = [], []
        for record in selected:
            try:
                await self.cog.collect_launch_rewards_internal(interaction.user, record, self.guild_id)
                submitted.append(str(record.get("launch_ref") or record.get("launch_id")))
            except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                failures.append(str(record.get("launch_ref") or record.get("launch_id")) + ": " + str(exc))
        lines = ["Submitted: " + (", ".join(submitted) if submitted else "none")]
        if failures:
            lines.append("Not submitted:" + chr(10) + chr(10).join(failures))
        lines.append("Profit cannot be known before collection; exact amounts arrive from each confirmed receipt.")
        await interaction.followup.send(chr(10).join(lines), ephemeral=True)

    @discord.ui.button(label="Claim all profitable", emoji="💰", style=discord.ButtonStyle.success, row=3)
    async def profitable(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            embed, claims, profitable = await self.cog.treasury_withdrawal_review(self.records)
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await interaction.followup.send("Portfolio withdrawal review unavailable: " + str(exc), ephemeral=True)
            return
        view = ClankerTreasuryWithdrawalView(self.cog, self.records[0], claims, self.user_id, self.guild_id) if claims and profitable else None
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


def draft_values_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Restore editable card values from a persisted route-neutral payload."""
    payload = copy.deepcopy(record.get("payload") or {})
    rewards = (payload.get("rewards") or {}).get("recipients") or []
    platform = str(record.get("platform_treasury") or "").lower()
    creator = next(
        (item for item in rewards if str(item.get("recipient") or "").lower() != platform),
        {},
    )
    airdrop = payload.get("airdrop")
    return {
        "name": payload.get("name"),
        "symbol": payload.get("symbol"),
        "supply": DEFAULT_CLANKER_SUPPLY,
        "primary_beneficiary": payload.get("tokenAdmin"),
        "creator_reward_recipient": creator.get("recipient"),
        "image_url": payload.get("image") or None,
        "description": (payload.get("metadata") or {}).get("description") or None,
        "vault": copy.deepcopy(payload.get("vault")),
        "airdrop": ({
            "recipients": [],
            "amount": int(airdrop.get("amount") or 0),
            "total_input": str(airdrop.get("amount") or ""),
            "total_label": "saved total",
            "merkleRoot": airdrop.get("merkleRoot"),
            "merkleExport": record.get("airdrop_proofs"),
            "lockupDuration": int(airdrop.get("lockupDuration") or MIN_AIRDROP_LOCKUP_SECONDS),
            "vestingDuration": int(airdrop.get("vestingDuration") or 0),
            "preview_only": False,
        } if airdrop else None),
    }


class ClankerDeleteDraftButton(discord.ui.Button):
    def __init__(self, cog: "Clanker", guild: Any, user_id: int, launch_id: str):
        super().__init__(label="Delete draft", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
        self.cog = cog
        self.guild = guild
        self.user_id = int(user_id)
        self.launch_id = str(launch_id)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the draft owner can delete it.", ephemeral=True)
            return
        try:
            deleted = await self.cog.delete_user_drafts(self.guild, interaction.user, [self.launch_id])
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        embed = discord.Embed(
            title="Clanker draft deleted",
            description="Deleted {} unsubmitted draft. Nothing was sent to a wallet.".format(deleted),
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)


class ClankerDeleteDraftsView(discord.ui.View):
    def __init__(self, cog: "Clanker", guild: Any, user_id: int, launch_ids: list[str]):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.user_id = int(user_id)
        self.launch_ids = list(launch_ids)
        self.processing = False
        if len(self.launch_ids) == 1:
            self.confirm.label = "Delete draft"
            self.cancel.label = "Keep draft"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the draft owner can confirm this deletion.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Delete all drafts", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.processing:
            await interaction.response.send_message("Draft deletion is already running.", ephemeral=True)
            return
        self.processing = True
        try:
            deleted = await self.cog.delete_user_drafts(self.guild, interaction.user, self.launch_ids)
            embed = discord.Embed(
                title="Clanker draft deleted" if deleted == 1 else "Clanker drafts deleted",
                description="Deleted {} unsubmitted draft{}. Nothing was sent to a wallet.".format(
                    deleted, "" if deleted == 1 else "s"
                ),
                color=discord.Color.red(),
            )
            await interaction.response.edit_message(embed=embed, view=None)
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            self.processing = False
            await interaction.response.send_message(str(exc), ephemeral=True)

    @discord.ui.button(label="Keep drafts", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Draft deletion canceled.", embed=None, view=None)


class ClankerDraftSelect(discord.ui.Select):
    def __init__(self, parent: "ClankerDraftHistoryView"):
        self.parent_view = parent
        options = []
        for index, record in enumerate(parent.records):
            reference = str(record.get("launch_ref") or record.get("launch_id") or "unknown")
            symbol = str(record.get("symbol") or "?").upper()
            status = "Verified — ready to launch" if record.get("status") == "verified" else "Editable draft"
            options.append(discord.SelectOption(
                label=("$" + symbol + " • " + reference)[:100],
                value=str(index),
                description=status,
            ))
        super().__init__(placeholder="Choose a draft to reopen", options=options)

    async def callback(self, interaction: discord.Interaction):
        record = self.parent_view.records[int(self.values[0])]
        draft = draft_values_from_record(record)
        if record.get("status") == "verified":
            try:
                record = await self.parent_view.cog.refresh_verified_draft(
                    self.parent_view.ctx.guild, interaction.user, str(record["launch_id"])
                )
            except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
            view = ClankerVerifiedView(
                self.parent_view.cog, self.parent_view.ctx, record,
                self.parent_view.settings, draft,
            )
        else:
            view = ClankerDraftView(
                self.parent_view.cog, self.parent_view.ctx, self.parent_view.settings,
                saved_record=record,
            )
            view.draft = draft
        view.add_item(ClankerDeleteDraftButton(
            self.parent_view.cog, self.parent_view.ctx.guild,
            self.parent_view.user_id, str(record["launch_id"]),
        ))
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)


class ClankerDraftHistoryView(discord.ui.View):
    def __init__(
        self, cog: "Clanker", ctx: commands.Context, records: list[Dict[str, Any]],
        settings: Dict[str, Any],
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.ctx = ctx
        self.records = copy.deepcopy(list(reversed(records)))
        self.settings = copy.deepcopy(settings)
        self.user_id = int(ctx.author.id)
        self.add_item(ClankerDraftSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the person whose drafts are listed can open these cards.", ephemeral=True
        )
        return False


class ClankerRemoveFailedView(discord.ui.View):
    def __init__(self, cog: "Clanker", guild: Any, user_id: int, launch_id: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.user_id = int(user_id)
        self.launch_id = str(launch_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the launch owner can remove this failed attempt.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Remove failed attempt", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.cog.dismiss_failed_launch(self.guild, interaction.user, self.launch_id)
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.edit_message(
            content="Failed launch attempt removed from your activity. Its audit record was retained.",
            embed=None, view=None,
        )

    @discord.ui.button(label="Keep attempt", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Failed launch attempt kept.", embed=None, view=None
        )


class ClankerApprovalResumeView(discord.ui.View):
    def __init__(
        self, cog: "Clanker", ctx: commands.Context, record: Dict[str, Any],
        settings: Dict[str, Any],
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.ctx = ctx
        self.record = copy.deepcopy(record)
        self.settings = copy.deepcopy(settings)
        self.user_id = int(ctx.author.id)
        self.processing = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the launch owner can resume this approval.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Resume approval", emoji="▶️", style=discord.ButtonStyle.primary)
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.processing:
            await interaction.response.send_message(
                "This approval is already being resumed.", ephemeral=True
            )
            return
        self.processing = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            record = await self.cog.resume_approval_launch(
                self.ctx.guild, interaction.user, str(self.record["launch_id"])
            )
            view = ClankerVerifiedView(
                self.cog, self.ctx, record, self.settings,
                draft_values_from_record(record),
            )
            await interaction.message.edit(embed=view.embed(), view=view)
            await interaction.followup.send(
                "Approval resumed on the same launch record. Review the renewed immutable "
                "card, then use its launch control.",
                ephemeral=True,
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            self.processing = False
            await interaction.followup.send(str(exc), ephemeral=True)


class ClankerFailedLaunchView(discord.ui.View):
    def __init__(
        self, cog: "Clanker", ctx: commands.Context, record: Dict[str, Any],
        settings: Dict[str, Any],
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.ctx = ctx
        self.record = copy.deepcopy(record)
        self.settings = copy.deepcopy(settings)
        self.user_id = int(ctx.author.id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the launch owner can manage this failed attempt.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Retry as new draft", emoji="↩️", style=discord.ButtonStyle.primary)
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            record = await self.cog.retry_failed_launch(
                self.ctx.guild, interaction.user, str(self.record["launch_id"])
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        view = ClankerDraftView(
            self.cog, self.ctx, self.settings, saved_record=record
        )
        view.draft = draft_values_from_record(record)
        view.add_item(ClankerDeleteDraftButton(
            self.cog, self.ctx.guild, self.user_id, str(record["launch_id"])
        ))
        await interaction.response.send_message(
            content="A new editable draft was created. The failed attempt remains in activity until removed.",
            embed=view.embed(), view=view, ephemeral=True,
        )

    @discord.ui.button(label="Remove failed attempt", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Remove failed launch attempt?",
            description="This only hides the failed attempt from your activity; its audit record remains.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=ClankerRemoveFailedView(
                self.cog, self.ctx.guild, self.user_id, str(self.record["launch_id"])
            ),
            ephemeral=True,
        )


class ClankerLaunchSelect(discord.ui.Select):
    """Requester-bound selection for reopening one launch receipt."""

    def __init__(self, parent: "ClankerLaunchHistoryView"):
        self.parent_view = parent
        options = []
        for index, record in enumerate(parent.records):
            reference = str(record.get("launch_ref") or record.get("launch_id") or "unknown")
            symbol = str(record.get("symbol") or "?").upper()
            status = parent.cog.launch_status_label(str(record.get("status") or "unknown"))
            options.append(discord.SelectOption(
                label=("$" + symbol + " • " + reference)[:100],
                value=str(index),
                description=status[:100],
            ))
        super().__init__(placeholder="Choose a launch to reopen", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        record = self.parent_view.records[int(self.values[0])]
        status = str(record.get("status") or "")
        view = None
        if status in {"internal_confirmed", "external_confirmed"} and record.get("token_address"):
            view = ClankerReceiptRewardsView(
                self.parent_view.cog, record, self.parent_view.ctx.guild.id,
                self.parent_view,
            )
        elif status == "awaiting_cryptowallet_approval":
            view = ClankerApprovalResumeView(
                self.parent_view.cog, self.parent_view.ctx, record,
                self.parent_view.settings,
            )
        elif status == "internal_failed":
            view = ClankerFailedLaunchView(
                self.parent_view.cog, self.parent_view.ctx, record,
                self.parent_view.settings,
            )
        if view is None:
            view = discord.ui.View(timeout=900)
        if not any(isinstance(item, ClankerBackToLaunchesButton) for item in view.children):
            view.add_item(ClankerBackToLaunchesButton(self.parent_view))
        await interaction.response.edit_message(
            embed=self.parent_view.cog.launch_record_embed(record), view=view
        )


class ClankerLaunchHistoryView(discord.ui.View):
    """Let one requester reopen receipt cards from their launch history."""

    def __init__(
        self, cog: "Clanker", ctx: commands.Context, records: list[Dict[str, Any]],
        settings: Dict[str, Any],
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.ctx = ctx
        self.records = copy.deepcopy(list(reversed(records)))
        self.settings = copy.deepcopy(settings)
        self.user_id = int(ctx.author.id)
        self.add_item(ClankerLaunchSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the person whose launches are listed can open these cards.", ephemeral=True
        )
        return False


class ClankerBackToLaunchesButton(discord.ui.Button):
    def __init__(self, history_view: "ClankerLaunchHistoryView"):
        super().__init__(label="Back to launch activity", emoji="↩️", style=discord.ButtonStyle.secondary, row=4)
        self.history_view = history_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            embed=self.history_view.cog.launch_list_embed(
                list(reversed(self.history_view.records)),
                list(reversed(self.history_view.records)),
            ),
            view=self.history_view,
        )


class ClankerReceiptRewardsView(discord.ui.View):
    """Open a private reward preflight scoped to one confirmed launch."""

    def __init__(
        self, cog: "Clanker", record: Dict[str, Any], guild_id: int,
        history_view: Optional["ClankerLaunchHistoryView"] = None,
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.record = copy.deepcopy(record)
        self.guild_id = int(guild_id)
        self.history_view = history_view
        if history_view is not None:
            self.add_item(ClankerBackToLaunchesButton(history_view))

    @discord.ui.button(label="Rewards", emoji="\U0001f4b0", style=discord.ButtonStyle.primary)
    async def rewards(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            embed, snapshot = await self.cog.reward_preflight_embed(
                [self.record], portfolio=False, include_snapshot=True
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            await interaction.followup.send(
                "Clanker rewards are temporarily unavailable: {}".format(exc),
                ephemeral=True,
            )
            return
        view = ClankerRewardReviewView(
            self.cog, self.record, interaction.user.id, self.guild_id,
            snapshot=snapshot,
        )
        if self.history_view is not None:
            view.add_item(ClankerBackToLaunchesButton(self.history_view))
        await interaction.message.edit(embed=embed, view=view)


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
        vault_summary = "Disabled"
        if vault:
            vaulted_tokens = DEFAULT_CLANKER_SUPPLY * int(vault["percentage"]) // 100
            vesting = (
                describe_vault_duration(vault.get("vestingDuration") or 0)
                if vault.get("vestingDuration") else "none (full unlock after lockup)"
            )
            vault_summary = chr(10).join((
                "Supply Percentage: {}% ({})".format(vault["percentage"], format_tokens(vaulted_tokens)),
                "Lockup: {}".format(describe_vault_duration(vault["lockupDuration"])),
                "Vesting: {}".format(vesting),
                "Recipient: {}".format(vault["recipient"]),
            ))
        embed.add_field(name="Vault", value=vault_summary, inline=False)
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
        saved_record: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.ctx = ctx
        self.settings = settings
        self.user_id = ctx.author.id
        self.processing = False
        self.saved_record = copy.deepcopy(saved_record) if saved_record else None
        self.draft: Dict[str, Any] = {
            "name": name,
            "symbol": symbol,
            "supply": DEFAULT_CLANKER_SUPPLY,
            "primary_beneficiary": creator_address,
            "creator_reward_recipient": creator_address,
            "image_url": None,
            "description": None,
            "vault": ({
                "percentage": int(settings.get("vault_percentage") or 0),
                "lockupDuration": int(settings.get("vault_lockup_seconds") or MIN_VAULT_LOCKUP_SECONDS),
                "vestingDuration": int(settings.get("vault_vesting_seconds") or 0),
                "recipient": settings.get("vault_recipient"),
            } if settings.get("vault_enabled") else None),
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
            bool(self.draft.get("vault")),
            int((self.draft.get("vault") or {}).get("percentage") or 0),
            int((self.draft.get("vault") or {}).get("lockupDuration") or MIN_VAULT_LOCKUP_SECONDS),
            int((self.draft.get("vault") or {}).get("vestingDuration") or 0),
            (self.draft.get("vault") or {}).get("recipient"),
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
        vault = self.draft.get("vault")
        if vault:
            recipient = vault.get("recipient") or self.draft.get("primary_beneficiary") or "signer wallet"
            vesting = int(vault.get("vestingDuration") or 0)
            vaulted_tokens = DEFAULT_CLANKER_SUPPLY * int(vault["percentage"]) // 100
            vesting_text = (
                describe_vault_duration(vesting) if vesting
                else "none (full unlock after lockup)"
            )
            detail = chr(10).join((
                "Supply Percentage: {}% ({})".format(vault["percentage"], format_tokens(vaulted_tokens)),
                "Lockup: {}".format(describe_vault_duration(vault["lockupDuration"])),
                "Vesting: {}".format(vesting_text),
                "Recipient: {}".format(recipient),
            ))
            embed.add_field(name="Vault", value=detail, inline=False)
        else:
            embed.add_field(
                name="Vault · optional",
                value=(
                    "Reserves part of the supply so it cannot circulate immediately. "
                    "Starter example: 10% Supply Percentage, 6m Lockup.\n"
                    "10% of the supply will be vaulted and locked for 6 months. Vesting is "
                    "optional and releases it gradually after the lockup.\n"
                    "Duration units: d = days, w = weeks, m = 30-day months, y = years."
                ),
                inline=False,
            )
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

    @discord.ui.button(label="Vault", emoji="🔒", style=discord.ButtonStyle.secondary)
    async def edit_vault(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ClankerVaultModal(self))

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
            if self.saved_record:
                record = await self.cog.replace_saved_draft(
                    self.ctx.guild, interaction.user,
                    str(self.saved_record["launch_id"]), record,
                )
            else:
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
