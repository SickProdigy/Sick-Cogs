from __future__ import annotations

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
        if role is None or role.id not in await self.cog.shop_role_ids(interaction.guild):
            await interaction.response.send_message("That shop offer is no longer available.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=await self.cog.shop_confirmation_embed(interaction.user, role),
            view=ShopConfirmView(self.cog, interaction.user, role), ephemeral=True,
        )


class RoleShopView(discord.ui.View):
    def __init__(self, cog, guild: discord.Guild, role_ids):
        super().__init__(timeout=None)
        self.add_item(ShopRoleSelect(cog, guild, role_ids))


class ShopConfirmView(discord.ui.View):
    def __init__(self, cog, author: discord.Member, role: discord.Role):
        super().__init__(timeout=120)
        self.cog, self.author, self.role = cog, author, role
        self.finished = False

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
    def __init__(self, parent: "RoleShopOfferManagerView", role: discord.Role, *, cost, duration, group_name):
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
        self.add_item(self.cost_input)
        self.add_item(self.duration_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            cost = int(str(self.cost_input.value).strip() or "0")
            duration_minutes = int(str(self.duration_input.value).strip() or "0")
        except ValueError:
            await interaction.response.send_message(
                "Price and duration must be whole numbers.", ephemeral=True
            )
            return
        ok, message = await self.parent_view.cog.configure_shop_offer(
            interaction.guild, self.role, cost, duration_minutes
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
        self, guild: discord.Guild, role: discord.Role, cost: int, duration_minutes: int
    ):
        if cost < 0 or duration_minutes < 0:
            return False, "Price and duration cannot be negative."
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

        changed, notes = await self.update_shop_roles(guild, [role], add=True)
        if notes:
            return False, "\n".join(notes)
        action = "Added" if changed else "Updated"
        duration = f"{duration_minutes} minute(s)" if duration_minutes else "permanent"
        return True, f"{action} {role.mention} for {cost} credits · {duration}."

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
        return {
            "cost": int(settings.get("cost", 0)),
            "duration": settings.get("duration"),
            "required": [guild.get_role(int(role_id)) for role_id in required_ids if guild.get_role(int(role_id))],
            "conflicts": [guild.get_role(int(role_id)) for role_id in conflict_ids if guild.get_role(int(role_id))],
            "require_any": bool(group.get("require_any")) if group else bool(settings.get("require_any")),
            "group_name": group_name,
        }

    async def role_shop_embed(self, guild: discord.Guild) -> discord.Embed:
        currency = await bank.get_currency_name(guild)
        lines = []
        for role_id in await self.shop_role_ids(guild):
            role = guild.get_role(role_id)
            details = await self.role_offer_details(guild, role)
            duration = humanize_timedelta(seconds=details["duration"]) if details["duration"] else "Permanent"
            lines.append(f"{role.mention} — **{details['cost']} {currency}** · {duration}")
        return discord.Embed(
            title="Role shop",
            description=("Choose a role below to review its price and requirements before buying.\n\n" + "\n".join(lines))[:4096],
            color=discord.Color.blurple(),
        )

    async def shop_confirmation_embed(self, member: discord.Member, role: discord.Role):
        details = await self.role_offer_details(member.guild, role)
        currency = await bank.get_currency_name(member.guild)
        embed = discord.Embed(
            title=f"Buy {role.name}?",
            description="Nothing is charged until you confirm. Eligibility and balance are checked again at purchase time.",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Price", value=f"{details['cost']} {currency}")
        embed.add_field(name="Your balance", value=f"{await bank.get_balance(member)} {currency}")
        embed.add_field(
            name="Duration",
            value=humanize_timedelta(seconds=details["duration"]) if details["duration"] else "Permanent",
        )
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

    async def purchase_shop_role(self, member: discord.Member, role: discord.Role):
        if role.id not in await self.shop_role_ids(member.guild):
            return False, "That shop offer is no longer available."
        group_name, _ = await self.private_group_for_gateway(member.guild, role.id)
        try:
            if group_name:
                ok, message = await self.join_private_group(member, group_name)
                if not ok:
                    return False, message
                return True, f"You purchased {role.mention}."
            response = await self.give_roles(member, [role], "Role shop purchase")
        except Exception:
            log.exception("Role shop purchase failed for role %s and member %s", role.id, member.id)
            return False, ("Discord could not assign the role. RoleTools attempted to return any payment; "
                           "contact a server manager if your balance looks wrong.")
        if response:
            return False, "\n".join(item.reason for item in response)
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
            offer_lines.append(f"• {role.name} — {details['cost']} credits · {duration}")
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
            lines.append(f"• {role.name} — {details['cost']} credits · {duration}")
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
