from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import discord
from red_commons.logging import getLogger
from redbot.core import bank
from redbot.core.utils.chat_formatting import humanize_list, humanize_timedelta

from .abc import RoleToolsMixin

log = getLogger("red.Sick-Cogs.RoleTools")


class ShopRoleSelect(discord.ui.Select):
    def __init__(self, cog, guild: discord.Guild, role_ids):
        self.cog = cog
        options = [
            discord.SelectOption(label=guild.get_role(role_id).name[:100], value=str(role_id))
            for role_id in role_ids if guild.get_role(role_id)
        ]
        super().__init__(
            placeholder="Choose a role to buy", options=options,
            custom_id=f"roletools:shop:select:{guild.id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        role = interaction.guild.get_role(int(self.values[0]))
        role_ids = await self.cog.shop_role_ids(interaction.guild)
        if role is None or role.id not in role_ids:
            await interaction.response.send_message("That shop offer is no longer available.", ephemeral=True)
            return
        details = await self.cog.role_offer_details(interaction.guild, role)
        renewing = role in interaction.user.roles and details["purchase_mode"] == "renewable"
        await interaction.response.defer(ephemeral=True)
        await interaction.message.edit(
            embed=await self.cog.role_shop_embed(interaction.guild),
            view=RoleShopView(self.cog, interaction.guild, role_ids),
        )
        await interaction.followup.send(
            embed=await self.cog.shop_confirmation_embed(interaction.user, role),
            view=ShopConfirmView(self.cog, interaction.user, role, renewing=renewing),
            ephemeral=True,
        )


class RoleShopView(discord.ui.View):
    def __init__(self, cog, guild: discord.Guild, role_ids):
        super().__init__(timeout=None)
        self.add_item(ShopRoleSelect(cog, guild, role_ids))


class ShopConfirmView(discord.ui.View):
    def __init__(
        self, cog, author: discord.Member, role: discord.Role, *, renewing: bool = False
    ):
        super().__init__(timeout=120)
        self.cog, self.author, self.role = cog, author, role
        self.finished = False
        if renewing:
            self.confirm.label = "Confirm renewal"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Open the role shop yourself to make a purchase.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm purchase", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            await interaction.response.send_message("This purchase has already been handled.", ephemeral=True)
            return
        self.finished = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        ok, message = await self.cog.purchase_shop_role(interaction.user, self.role)
        await interaction.edit_original_response(
            embed=discord.Embed(
                title="Purchase complete" if ok else "Purchase not completed",
                description=message,
                color=discord.Color.green() if ok else discord.Color.red(),
            ),
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.finished = True
        await interaction.response.edit_message(content="Purchase cancelled.", embed=None, view=None)


class ShopOfferRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "RoleShopOfferManagerView", *, add: bool):
        super().__init__(
            placeholder="Configure or add a shop offer" if add else "Remove shop offers",
            min_values=1, max_values=1 if add else 25, row=0 if add else 1,
        )
        self.parent_view, self.add = parent, add

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.add:
            role = self.values[0]
            details = await self.parent_view.cog.role_offer_details(interaction.guild, role)
            await interaction.response.send_modal(
                ShopOfferConfigModal(
                    self.parent_view,
                    role,
                    cost=details["cost"],
                    duration=details["duration"],
                    group_name=details["group_name"],
                    purchase_mode=details["purchase_mode"],
                    max_purchases=details["max_purchases"],
                )
            )
            return
        changed, notes = await self.parent_view.cog.update_shop_roles(
            interaction.guild, list(self.values), add=self.add
        )
        embed = await self.parent_view.cog.shop_offer_manager_embed(interaction.guild)
        summary = f"Removed {changed} offer(s)."
        if notes:
            summary += "\n" + "\n".join(notes)
        embed.add_field(name="Last change", value=summary[:1024], inline=False)
        await interaction.response.edit_message(
            embed=embed,
            view=RoleShopOfferManagerView(self.parent_view.cog, self.parent_view.author),
        )


class ShopOfferConfigModal(discord.ui.Modal, title="Configure shop offer"):
    def __init__(
        self, parent: "RoleShopOfferManagerView", role: discord.Role, *,
        cost, duration, group_name, purchase_mode, max_purchases,
    ):
        super().__init__()
        self.parent_view, self.role, self.group_name = parent, role, group_name
        self.cost_input = discord.ui.TextInput(
            label="Price in Red credits (0 = free)",
            default=str(int(cost or 0)),
            max_length=20,
        )
        self.duration_input = discord.ui.TextInput(
            label="Duration in minutes (0 = permanent)",
            default=str(int(duration or 0) // 60),
            max_length=12,
        )
        self.mode_input = discord.ui.TextInput(
            label="Purchase mode: one-time or renewable",
            default=str(purchase_mode),
            max_length=10,
        )
        self.limit_input = discord.ui.TextInput(
            label="Maximum purchases (0 = unlimited)",
            default=str(int(max_purchases or 0)),
            max_length=10,
        )
        self.add_item(self.cost_input)
        self.add_item(self.duration_input)
        self.add_item(self.mode_input)
        self.add_item(self.limit_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if await bank.is_global() and not await self.parent_view.cog.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "Only the bot owner can configure shop prices while Red Bank is global.",
                ephemeral=True,
            )
            return
        try:
            cost = int(str(self.cost_input.value).strip() or "0")
            duration_minutes = int(str(self.duration_input.value).strip() or "0")
            max_purchases = int(str(self.limit_input.value).strip() or "0")
        except ValueError:
            await interaction.response.send_message(
                "Price, duration, and maximum purchases must be whole numbers.", ephemeral=True
            )
            return
        purchase_mode = str(self.mode_input.value).strip().lower()
        ok, message = await self.parent_view.cog.configure_shop_offer(
            interaction.guild, self.role, cost, duration_minutes,
            purchase_mode=purchase_mode, max_purchases=max_purchases,
        )
        embed = await self.parent_view.cog.shop_offer_manager_embed(interaction.guild)
        embed.add_field(
            name="Offer saved" if ok else "Offer not saved",
            value=message[:1024],
            inline=False,
        )
        await interaction.response.edit_message(
            embed=embed,
            view=RoleShopOfferManagerView(self.parent_view.cog, self.parent_view.author),
        )


class ShopPublishChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "RoleShopManagerView"):
        super().__init__(
            placeholder="Publish or move shop to a channel", min_values=1, max_values=1,
            channel_types=[discord.ChannelType.text, discord.ChannelType.news], row=2,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        selected = self.values[0]
        channel = interaction.guild.get_channel(selected.id)
        if channel is None:
            message = "That channel is no longer available. Choose another channel."
        else:
            _, message = await self.parent_view.cog.publish_role_shop(interaction.guild, channel)
        embed = await self.parent_view.cog.shop_manager_embed(interaction.guild)
        embed.add_field(name="Publish result", value=message[:1024], inline=False)
        await interaction.edit_original_response(embed=embed, view=self.parent_view)


class RoleShopOfferManagerView(discord.ui.View):
    def __init__(self, cog, author: discord.Member):
        super().__init__(timeout=600)
        self.cog, self.author = cog, author
        self.add_item(ShopOfferRoleSelect(self, add=True))
        self.add_item(ShopOfferRoleSelect(self, add=False))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Open your own RoleTools setup card.", ephemeral=True)
            return False
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("Manage Roles is required.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Back to shop", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=await self.cog.shop_manager_embed(interaction.guild),
            view=RoleShopManagerView(self.cog, self.author),
        )


class RoleShopManagerView(discord.ui.View):
    def __init__(self, cog, author: discord.Member):
        super().__init__(timeout=600)
        self.cog, self.author = cog, author
        self.add_item(ShopPublishChannelSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Open your own RoleTools setup card.", ephemeral=True)
            return False
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("Manage Roles is required.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Manage shop roles", style=discord.ButtonStyle.primary, row=0)
    async def manage_offers(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=await self.cog.shop_offer_manager_embed(interaction.guild),
            view=RoleShopOfferManagerView(self.cog, self.author),
        )

    @discord.ui.button(label="Unpublish", style=discord.ButtonStyle.danger, row=3)
    async def unpublish(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await self.cog.unpublish_role_shop(interaction.guild)
        embed = await self.cog.shop_manager_embed(interaction.guild)
        embed.add_field(name="Last change", value=message[:1024], inline=False)
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success, row=3)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Role shop changes saved.", embed=None, view=None)


class RoleToolsShop(RoleToolsMixin):
    async def role_shop(self, guild: discord.Guild) -> dict:
        data = dict(await self.config.guild(guild).role_shop())
        data.setdefault("role_ids", [])
        data.setdefault("channel_id", None)
        data.setdefault("message_id", None)
        data.setdefault("offers", {})
        return data

    async def save_role_shop(self, guild: discord.Guild, data: dict) -> None:
        await self.config.guild(guild).role_shop.set(data)
        if guild.id in self.settings:
            self.settings[guild.id]["role_shop"] = data

    async def shop_role_ids(self, guild: discord.Guild):
        data = await self.role_shop(guild)
        return [int(role_id) for role_id in data["role_ids"] if guild.get_role(int(role_id))]

    async def update_shop_roles(self, guild: discord.Guild, roles, *, add: bool):
        data = await self.role_shop(guild)
        current = [int(role_id) for role_id in data["role_ids"]]
        advanced = set(await self.restricted_role_ids(guild))
        changed, notes = 0, []
        for role in roles:
            if add and role.id not in advanced:
                notes.append(f"{role.name}: add it to Advanced roles first.")
            elif add and role.id not in current:
                if len(current) >= 25:
                    notes.append("A shop card supports up to 25 offers.")
                    break
                current.append(role.id)
                changed += 1
            elif not add and role.id in current:
                current.remove(role.id)
                changed += 1
        data["role_ids"] = current
        await self.save_role_shop(guild, data)
        if data.get("message_id"):
            await self.sync_role_shop(guild)
        return changed, notes

    async def configure_shop_offer(
        self, guild: discord.Guild, role: discord.Role, cost: int, duration_minutes: int, *,
        purchase_mode: str = "one-time", max_purchases: int = 0,
    ):
        purchase_mode = purchase_mode.replace("_", "-")
        if purchase_mode not in {"one-time", "renewable"}:
            return False, "Purchase mode must be one-time or renewable."
        if cost < 0 or duration_minutes < 0 or max_purchases < 0:
            return False, "Price, duration, and maximum purchases cannot be negative."
        if purchase_mode == "renewable" and not duration_minutes:
            return False, "Renewable offers need a temporary duration."
        if cost >= await bank.get_max_balance(guild):
            return False, "Price must be lower than the maximum Red Bank balance."

        _, notes = await self.update_role_catalog(guild, [role], restricted=True, add=True)
        if role.id not in await self.restricted_role_ids(guild):
            return False, notes[0] if notes else "The bot cannot manage that role."

        group_name, _ = await self.private_group_for_gateway(guild, role.id)
        if group_name:
            ok, message = await self.set_private_group_cost(guild, group_name, cost)
            if not ok:
                return False, message
            ok, message = await self.set_private_group_duration(
                guild, group_name, duration_minutes
            )
            if not ok:
                return False, message
        else:
            cost_setting = self.config.role(role).cost
            duration_setting = self.config.role(role).duration
            if cost:
                await cost_setting.set(cost)
            else:
                await cost_setting.clear()
            if duration_minutes:
                await duration_setting.set(duration_minutes * 60)
            else:
                await duration_setting.clear()

        shop = await self.role_shop(guild)
        offers = dict(shop.get("offers", {}))
        offers[str(role.id)] = {
            "purchase_mode": purchase_mode,
            "max_purchases": int(max_purchases),
        }
        shop["offers"] = offers
        await self.save_role_shop(guild, shop)

        changed, notes = await self.update_shop_roles(guild, [role], add=True)
        if notes:
            return False, "\n".join(notes)
        action = "Added" if changed else "Updated"
        duration = f"{duration_minutes} minute(s)" if duration_minutes else "permanent"
        limit = f" · limit {max_purchases}" if max_purchases else ""
        return True, (
            f"{action} {role.mention} for {cost} credits · {duration} · "
            f"{purchase_mode}{limit}."
        )

    async def private_group_for_gateway(self, guild: discord.Guild, role_id: int):
        for name, data in (await self.private_groups(guild)).items():
            if data.get("gateway_role_id") and int(data["gateway_role_id"]) == role_id:
                return name, data
        return None, None

    async def role_offer_details(self, guild: discord.Guild, role: discord.Role):
        settings = await self.config.role(role).all()
        group_name, group = await self.private_group_for_gateway(guild, role.id)
        required_ids = group.get("required_role_ids", []) if group else settings.get("required", [])
        conflict_ids = group.get("conflict_role_ids", []) if group else settings.get("exclusive_to", [])
        shop = await self.role_shop(guild)
        policy = dict(shop.get("offers", {}).get(str(role.id), {}))
        default_mode = "renewable" if settings.get("duration") else "one-time"
        return {
            "cost": int(settings.get("cost", 0)),
            "duration": settings.get("duration"),
            "required": [guild.get_role(int(role_id)) for role_id in required_ids if guild.get_role(int(role_id))],
            "conflicts": [guild.get_role(int(role_id)) for role_id in conflict_ids if guild.get_role(int(role_id))],
            "require_any": bool(group.get("require_any")) if group else bool(settings.get("require_any")),
            "group_name": group_name,
            "purchase_mode": policy.get("purchase_mode", default_mode),
            "max_purchases": int(policy.get("max_purchases", 0)),
        }

    async def role_shop_embed(self, guild: discord.Guild) -> discord.Embed:
        currency = await bank.get_currency_name(guild)
        lines = []
        for role_id in await self.shop_role_ids(guild):
            role = guild.get_role(role_id)
            details = await self.role_offer_details(guild, role)
            duration = humanize_timedelta(seconds=details["duration"]) if details["duration"] else "Permanent"
            policy = "Renewable" if details["purchase_mode"] == "renewable" else "One-time"
            limit = f" · Limit {details['max_purchases']}" if details["max_purchases"] else ""
            lines.append(
                f"{role.mention} — **{details['cost']} {currency}** · {duration} · {policy}{limit}"
            )
        return discord.Embed(
            title="Role shop",
            description=("Choose a role below to review its price and requirements before buying.\n\n" + "\n".join(lines))[:4096],
            color=discord.Color.blurple(),
        )

    async def shop_confirmation_embed(self, member: discord.Member, role: discord.Role):
        details = await self.role_offer_details(member.guild, role)
        currency = await bank.get_currency_name(member.guild)
        renewing = role in member.roles and details["purchase_mode"] == "renewable"
        embed = discord.Embed(
            title=f"{'Renew' if renewing else 'Buy'} {role.name}?",
            description=(
                "The existing expiration will be extended from its remaining paid time. "
                if renewing else
                "Nothing is charged until you confirm. Eligibility and balance are checked again at purchase time."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(name="Price", value=f"{details['cost']} {currency}")
        embed.add_field(name="Your balance", value=f"{await bank.get_balance(member)} {currency}")
        embed.add_field(
            name="Duration",
            value=humanize_timedelta(seconds=details["duration"]) if details["duration"] else "Permanent",
        )
        policy = "Renewable" if details["purchase_mode"] == "renewable" else "One-time"
        if details["max_purchases"]:
            policy += f" · Maximum {details['max_purchases']} purchase(s)"
        embed.add_field(name="Purchase policy", value=policy, inline=False)
        required = humanize_list([item.mention for item in details["required"]]) or "None"
        if details["required"] and details["require_any"]:
            required = "Any of: " + required
        embed.add_field(name="Required roles", value=required[:1024], inline=False)
        embed.add_field(
            name="Conflicting roles",
            value=(humanize_list([item.mention for item in details["conflicts"]]) or "None")[:1024],
            inline=False,
        )
        return embed

    async def shop_purchase_records(self, member: discord.Member) -> dict:
        return dict(await self.config.member(member).shop_purchases())

    async def record_shop_purchase(self, member: discord.Member, role: discord.Role) -> int:
        records = await self.shop_purchase_records(member)
        record = dict(records.get(str(role.id), {}))
        record["count"] = int(record.get("count", 0)) + 1
        record["last_purchased_at"] = int(discord.utils.utcnow().timestamp())
        records[str(role.id)] = record
        await self.config.member(member).shop_purchases.set(records)
        return record["count"]

    async def shop_offer_eligibility(self, member: discord.Member, role: discord.Role, details: dict):
        member_roles = set(member.roles)
        required = set(details["required"])
        if required:
            allowed = bool(member_roles & required) if details["require_any"] else required <= member_roles
            if not allowed:
                qualifier = "one of" if details["require_any"] else "all of"
                return False, f"You need {qualifier} the required roles before purchasing this offer."
        conflicts = member_roles & set(details["conflicts"])
        if conflicts:
            return False, "A role you already have conflicts with this offer."
        group_name = details.get("group_name")
        if group_name:
            group = (await self.private_groups(member.guild)).get(group_name)
            if group is None:
                return False, "The private access group for this offer is unavailable."
            allowed, message = await self.private_group_access(member, group, joining=True)
            if not allowed:
                return False, message
        return True, ""

    async def purchase_shop_role(self, member: discord.Member, role: discord.Role):
        key = (member.guild.id, member.id)
        lock = self._role_transaction_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if role.id not in await self.shop_role_ids(member.guild):
                return False, "That shop offer is no longer available."
            if role >= member.guild.me.top_role:
                return False, "The bot cannot manage that role."
            details = await self.role_offer_details(member.guild, role)
            records = await self.shop_purchase_records(member)
            purchases = int(records.get(str(role.id), {}).get("count", 0))
            limit = int(details.get("max_purchases", 0))
            if details["purchase_mode"] == "one-time" and purchases:
                return False, "This is a one-time offer and you have already purchased it."
            if limit and purchases >= limit:
                return False, f"You have reached this offer's purchase limit of {limit}."

            eligible, message = await self.shop_offer_eligibility(member, role, details)
            if not eligible:
                return False, message

            if role in member.roles:
                if details["purchase_mode"] != "renewable" or not details.get("duration"):
                    return False, "You already have the requested role."
                cost = int(details["cost"])
                if cost and not await bank.can_spend(member, cost):
                    currency = await bank.get_currency_name(member.guild)
                    return False, f"You do not have enough {currency}. You need {cost} {currency}."
                charged = False
                try:
                    if cost:
                        await bank.withdraw_credits(member, cost)
                        charged = True
                    remove_at = await self.schedule_temporary_role(
                        member, role, int(details["duration"]), extend=True
                    )
                    await self.record_shop_purchase(member, role)
                except Exception:
                    if charged:
                        try:
                            await bank.deposit_credits(member, cost)
                        except Exception:
                            log.critical(
                                "Could not refund %s credits after renewal failure for %s",
                                cost, member.id, exc_info=True,
                            )
                    log.exception("Role shop renewal failed for role %s and member %s", role.id, member.id)
                    return False, "The renewal failed. RoleTools attempted to return your payment."
                renewal_time = discord.utils.format_dt(
                    datetime.fromtimestamp(
                        remove_at, tz=timezone.utc
                    ),
                    style="R",
                )
                return True, f"You renewed {role.mention}. It expires {renewal_time}."

            group_name = details.get("group_name")
            try:
                response = await self._give_roles_unlocked(
                    member,
                    [role],
                    "Role shop purchase",
                    check_private_groups=not bool(group_name),
                )
            except Exception:
                log.exception("Role shop purchase failed for role %s and member %s", role.id, member.id)
                return False, (
                    "Discord could not assign the role. RoleTools attempted to return any payment; "
                    "contact a server manager if your balance looks wrong."
                )
            if response:
                return False, "\n".join(item.reason for item in response)
            await self.record_shop_purchase(member, role)
            return True, f"You purchased {role.mention}."

    async def shop_manager_embed(self, guild: discord.Guild):
        data = await self.role_shop(guild)
        roles = [guild.get_role(role_id) for role_id in await self.shop_role_ids(guild)]
        offer_lines = []
        for role in roles:
            details = await self.role_offer_details(guild, role)
            duration = (
                humanize_timedelta(seconds=details["duration"])
                if details["duration"] else "Permanent"
            )
            policy = "renewable" if details["purchase_mode"] == "renewable" else "one-time"
            limit = f" · limit {details['max_purchases']}" if details["max_purchases"] else ""
            offer_lines.append(
                f"• {role.name} — {details['cost']} credits · {duration} · {policy}{limit}"
            )
        published = (
            f"<#{data['channel_id']}> · message `{data['message_id']}`"
            if data.get("message_id") else "Not published"
        )
        embed = discord.Embed(
            title="Role shop setup",
            description=(
                "Use **Manage shop roles** to choose which roles the shop offers and edit each role's "
                "Red Bank price and duration. The offer list below shows the current values. Existing "
                "prerequisites, conflicts, and private-group access rules are preserved."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Offers",
            value=("\n".join(offer_lines) or "None")[:1024],
            inline=False,
        )
        embed.add_field(name="Published", value=published, inline=False)
        embed.set_footer(text="Use Manage shop roles to add or edit offers. One shop supports up to 25 roles.")
        return embed

    async def shop_offer_manager_embed(self, guild: discord.Guild):
        roles = [guild.get_role(role_id) for role_id in await self.shop_role_ids(guild)]
        lines = []
        for role in roles:
            details = await self.role_offer_details(guild, role)
            duration = (
                humanize_timedelta(seconds=details["duration"])
                if details["duration"] else "Permanent"
            )
            policy = "renewable" if details["purchase_mode"] == "renewable" else "one-time"
            limit = f" · limit {details['max_purchases']}" if details["max_purchases"] else ""
            lines.append(
                f"• {role.name} — {details['cost']} credits · {duration} · {policy}{limit}"
            )
        return discord.Embed(
            title="Manage shop roles",
            description=(
                "Select a role to add or edit its price and duration. After saving, this card refreshes "
                "so another role can be selected immediately.\n\n"
                + ("\n".join(lines) if lines else "No shop offers configured.")
            )[:4096],
            color=discord.Color.blurple(),
        )

    async def publish_role_shop(self, guild: discord.Guild, channel):
        data = await self.role_shop(guild)
        role_ids = await self.shop_role_ids(guild)
        if not role_ids:
            return False, "Add at least one Advanced role before publishing."
        permissions = channel.permissions_for(guild.me)
        missing = [
            name for name in ("view_channel", "send_messages", "embed_links")
            if not getattr(permissions, name, False)
        ]
        if missing:
            return False, "Missing channel permissions: " + ", ".join(name.replace("_", " ") for name in missing)
        old_channel = guild.get_channel(data.get("channel_id")) if data.get("channel_id") else None
        old_message_id = data.get("message_id")
        message = await channel.send(
            embed=await self.role_shop_embed(guild),
            view=RoleShopView(self, guild, role_ids),
        )
        data.update(channel_id=channel.id, message_id=message.id)
        await self.save_role_shop(guild, data)
        self.bot.add_view(RoleShopView(self, guild, role_ids), message_id=message.id)
        if old_channel and old_message_id:
            try:
                await (await old_channel.fetch_message(int(old_message_id))).delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        return True, f"Published the role shop in {channel.mention}."

    async def sync_role_shop(self, guild: discord.Guild):
        data = await self.role_shop(guild)
        channel = guild.get_channel(data.get("channel_id")) if data.get("channel_id") else None
        role_ids = await self.shop_role_ids(guild)
        if channel is None or not data.get("message_id"):
            return False
        try:
            message = await channel.fetch_message(int(data["message_id"]))
            await message.edit(
                embed=await self.role_shop_embed(guild),
                view=RoleShopView(self, guild, role_ids) if role_ids else None,
            )
            return True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False

    async def unpublish_role_shop(self, guild: discord.Guild):
        data = await self.role_shop(guild)
        channel = guild.get_channel(data.get("channel_id")) if data.get("channel_id") else None
        if channel and data.get("message_id"):
            try:
                await (await channel.fetch_message(int(data["message_id"]))).delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        data["channel_id"] = data["message_id"] = None
        await self.save_role_shop(guild, data)
        return "Role shop unpublished. Saved offers were kept."

    async def register_role_shop_views(self):
        for guild in self.bot.guilds:
            data = await self.role_shop(guild)
            role_ids = await self.shop_role_ids(guild)
            if data.get("message_id") and role_ids:
                self.bot.add_view(
                    RoleShopView(self, guild, role_ids), message_id=int(data["message_id"])
                )
