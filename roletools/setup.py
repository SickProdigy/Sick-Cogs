from __future__ import annotations

from typing import List

import discord
from red_commons.logging import getLogger
from redbot.core import commands
from redbot.core.commands import Context

from .abc import RoleToolsMixin
from .picker import PUBLIC_SELECT_MAX_PAGES, PUBLIC_SELECT_PAGE_SIZE

roletools = RoleToolsMixin.roletools
log = getLogger("red.Sick-Cogs.RoleTools")
SETUP_PICKER_NAME = "_selfroles"
ADVANCED_KEYS = ("cost", "duration", "required", "exclusive_to", "inclusive_with")


class CatalogRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "CatalogEditorView", *, add: bool):
        action = "Add" if add else "Remove"
        super().__init__(
            placeholder=f"{action} {parent.catalog_name.lower()} roles",
            min_values=1,
            max_values=25,
            row=0 if add else 1,
        )
        self.parent_view = parent
        self.add = add

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        changed, notes = await self.parent_view.cog.update_role_catalog(
            interaction.guild,
            list(self.values),
            restricted=self.parent_view.restricted,
            add=self.add,
        )
        await self.parent_view.cog.refresh_setup_picker(interaction.guild)
        refreshed = CatalogEditorView(
            self.parent_view.cog,
            interaction.user,
            restricted=self.parent_view.restricted,
        )
        embed = await self.parent_view.cog.catalog_embed(
            interaction.guild, restricted=self.parent_view.restricted
        )
        action = "Added" if self.add else "Removed"
        summary = f"{action} {changed} role(s)."
        if notes:
            summary += "\n" + "\n".join(notes)
        embed.add_field(name="Last change", value=summary[:1024], inline=False)
        await interaction.edit_original_response(embed=embed, view=refreshed)


class CatalogEditorView(discord.ui.View):
    def __init__(self, cog, author: discord.Member, *, restricted: bool):
        super().__init__(timeout=600)
        self.cog = cog
        self.author = author
        self.restricted = restricted
        self.catalog_name = "Advanced self-role" if restricted else "Basic Red self-role"
        self.add_item(CatalogRoleSelect(self, add=True))
        self.add_item(CatalogRoleSelect(self, add=False))

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success, row=2)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Role catalog changes saved.", embed=None, view=None
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Open your own RoleTools setup card.", ephemeral=True)
            return False
        return True


class SetupPublishSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "SetupPublishView"):
        super().__init__(
            channel_types=[discord.ChannelType.text],
            placeholder="Choose the public self-role channel",
            min_values=1,
            max_values=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = self.values[0]
        ok, message = await self.parent_view.cog.publish_setup_picker(
            interaction.guild, channel, self.parent_view.layout
        )
        await interaction.edit_original_response(content=message, embed=None, view=None)


class SetupPublishView(discord.ui.View):
    def __init__(self, cog, author: discord.Member, layout: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        self.layout = layout
        self.add_item(SetupPublishSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Open your own RoleTools setup card.", ephemeral=True)
            return False
        return True


class SetupLayoutView(discord.ui.View):
    def __init__(self, cog, author: discord.Member):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author

    async def choose(self, interaction: discord.Interaction, layout: str, explanation: str) -> None:
        await interaction.response.edit_message(
            content=explanation + "\n\nNow choose the channel to publish or move it to.",
            view=SetupPublishView(self.cog, self.author, layout),
        )

    @discord.ui.button(label="Private menu", style=discord.ButtonStyle.primary)
    async def private_picker(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "private", "One public button opens a private, paged role menu for each member. Best for large catalogs.")

    @discord.ui.button(label="Public dropdowns", style=discord.ButtonStyle.secondary)
    async def dropdown(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "dropdown", "Dropdowns appear directly on the public card, up to 125 roles.")

    @discord.ui.button(label="Combined reactions", style=discord.ButtonStyle.secondary)
    async def reactions(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "reactions", "One managed message holds 20 distinct reactions; overflow messages are added automatically.")

    @discord.ui.button(label="Managed role channel", style=discord.ButtonStyle.secondary)
    async def role_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(
            interaction,
            "role_channel",
            "One bot message per role uses 👍. Synchronizing reuses message slots and preserves retained reaction counts.",
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Open your own RoleTools setup card.", ephemeral=True)
            return False
        return True


class SelfRoleAppearanceModal(discord.ui.Modal):
    def __init__(self, cog, guild: discord.Guild, data: dict):
        super().__init__(title="Self-role card appearance")
        self.cog = cog
        self.guild = guild
        self.title_input = discord.ui.TextInput(
            label="Card title", default=data.get("title") or "Choose your roles", max_length=256
        )
        self.description_input = discord.ui.TextInput(
            label="Instructions",
            default=data.get("description") or "Click Choose roles to open your private role list.",
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.add_item(self.title_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        pickers, data = await self.cog.ensure_setup_picker(self.guild)
        data["title"] = str(self.title_input.value).strip() or "Choose your roles"
        data["description"] = str(self.description_input.value).strip()
        pickers[SETUP_PICKER_NAME] = data
        await self.cog.config.guild(self.guild).pickers.set(pickers)
        await self.cog.refresh_setup_picker(self.guild)
        embed = await self.cog.setup_embed(self.guild)
        await interaction.response.edit_message(
            embed=embed, view=RoleToolsSetupView(self.cog, interaction.user)
        )


class RoleToolsSetupView(discord.ui.View):
    def __init__(self, cog, author: discord.Member):
        super().__init__(timeout=900)
        self.cog = cog
        self.author = author

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Open your own RoleTools setup card.", ephemeral=True)
            return False
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("Manage Roles is required.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Basic self-roles", style=discord.ButtonStyle.primary, row=0)
    async def basic_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = await self.cog.catalog_embed(interaction.guild, restricted=False)
        await interaction.response.send_message(
            embed=embed,
            view=CatalogEditorView(self.cog, interaction.user, restricted=False),
            ephemeral=True,
        )

    @discord.ui.button(label="Advanced self-roles", style=discord.ButtonStyle.secondary, row=0)
    async def restricted_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = await self.cog.catalog_embed(interaction.guild, restricted=True)
        await interaction.response.send_message(
            embed=embed,
            view=CatalogEditorView(self.cog, interaction.user, restricted=True),
            ephemeral=True,
        )

    @discord.ui.button(label="Appearance", style=discord.ButtonStyle.secondary, row=0)
    async def appearance(self, interaction: discord.Interaction, button: discord.ui.Button):
        _, data = await self.cog.ensure_setup_picker(interaction.guild)
        await interaction.response.send_modal(
            SelfRoleAppearanceModal(self.cog, interaction.guild, data)
        )

    @discord.ui.button(label="Publish role menu", style=discord.ButtonStyle.success, row=1)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Choose how members should use the public self-role card.",
            view=SetupLayoutView(self.cog, interaction.user),
            ephemeral=True,
        )

    @discord.ui.button(label="Sync published menu", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        synced = await self.cog.refresh_setup_picker(interaction.guild)
        embed = await self.cog.setup_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=RoleToolsSetupView(self.cog, interaction.user))
        await interaction.followup.send(
            "Published role menu synchronized." if synced else "Setup synchronized; publish a role menu when ready.",
            ephemeral=True,
        )


class RoleToolsSetup(RoleToolsMixin):
    async def admin_selfrole_ids(self, guild: discord.Guild) -> List[int]:
        admin = self.bot.get_cog("Admin")
        if admin is None:
            return []
        return [int(role_id) for role_id in await admin.config.guild(guild).selfroles()]

    async def set_admin_selfrole_ids(self, guild: discord.Guild, role_ids: List[int]) -> None:
        admin = self.bot.get_cog("Admin")
        if admin is None:
            raise RuntimeError("Red's Admin cog must be loaded to manage basic self-roles.")
        await admin.config.guild(guild).selfroles.set(list(dict.fromkeys(role_ids)))

    async def restricted_role_ids(self, guild: discord.Guild) -> List[int]:
        return [int(role_id) for role_id in await self.config.guild(guild).restricted_roles()]

    async def ensure_setup_picker(self, guild: discord.Guild):
        pickers = await self.config.guild(guild).pickers()
        data = pickers.get(SETUP_PICKER_NAME, {
            "title": "Choose your roles",
            "description": "Click Choose roles to open your private role list.",
            "channel_id": None,
            "message_id": None,
            "role_ids": [],
            "sort": "alphabetical",
            "layout": "private",
            "reaction_message_ids": [],
            "role_channel_message_ids": [],
        })
        data.setdefault("layout", "private")
        data.setdefault("reaction_message_ids", [])
        data.setdefault("role_channel_message_ids", [])
        return pickers, data

    async def combined_catalog_ids(self, guild: discord.Guild) -> List[int]:
        combined = list(dict.fromkeys(
            await self.admin_selfrole_ids(guild) + await self.restricted_role_ids(guild)
        ))
        combined = [role_id for role_id in combined if guild.get_role(role_id) is not None]
        combined.sort(key=lambda role_id: guild.get_role(role_id).name.lower())
        return combined

    async def refresh_setup_picker(self, guild: discord.Guild) -> bool:
        pickers, data = await self.ensure_setup_picker(guild)
        for role_id in await self.admin_selfrole_ids(guild):
            role = guild.get_role(role_id)
            if role is not None:
                await self.config.role(role).selfassignable.set(True)
                await self.config.role(role).selfremovable.set(True)
        data["role_ids"] = await self.combined_catalog_ids(guild)
        pickers[SETUP_PICKER_NAME] = data
        await self.config.guild(guild).pickers.set(pickers)
        if guild.id in self.settings:
            self.settings[guild.id]["pickers"] = pickers
        if data.get("message_id"):
            return await self.sync_picker(guild, SETUP_PICKER_NAME, data)
        return False

    async def update_role_catalog(
        self, guild: discord.Guild, roles: List[discord.Role], *, restricted: bool, add: bool
    ):
        admin = self.bot.get_cog("Admin")
        if admin is None and not restricted:
            return 0, ["Red's Admin cog is not loaded, so basic self-roles are unavailable."]
        basic = await self.admin_selfrole_ids(guild)
        restricted_ids = await self.restricted_role_ids(guild)
        target = restricted_ids if restricted else basic
        changed = 0
        notes = []
        for role in roles:
            if role.is_default() or role.managed or role >= guild.me.top_role:
                notes.append(f"Skipped {role.name}: the bot cannot manage it.")
                continue
            if add and not restricted:
                settings = await self.config.role(role).all()
                active = [key for key in ADVANCED_KEYS if settings.get(key)]
                if active:
                    notes.append(
                        f"Skipped {role.name}: it has RoleTools-only rules ({', '.join(active)}). "
                        "Add it under Advanced self-roles so !selfrole cannot bypass them."
                    )
                    continue
            if add:
                if role.id not in target:
                    target.append(role.id)
                    changed += 1
                other = restricted_ids if not restricted else basic
                if role.id in other:
                    other.remove(role.id)
                await self.config.role(role).selfassignable.set(True)
                await self.config.role(role).selfremovable.set(True)
            elif role.id in target:
                target.remove(role.id)
                changed += 1
        if admin is not None:
            await self.set_admin_selfrole_ids(guild, basic)
        await self.config.guild(guild).restricted_roles.set(restricted_ids)
        return changed, notes

    async def catalog_embed(self, guild: discord.Guild, *, restricted: bool) -> discord.Embed:
        role_ids = await (self.restricted_role_ids(guild) if restricted else self.admin_selfrole_ids(guild))
        roles = [guild.get_role(role_id) for role_id in role_ids]
        roles = [role for role in roles if role is not None]
        title = "Advanced self-roles" if restricted else "Basic Red self-roles"
        explanation = (
            "These roles use RoleTools-only assignment so costs, requirements, conflicts, and durations cannot be bypassed."
            if restricted else
            "These are Red Admin self-roles. They work with !selfrole and can also appear on RoleTools cards."
        )
        listing = "\n".join(f"• {role.name}" for role in roles[:40]) or "No roles configured."
        if len(roles) > 40:
            listing += f"\n…and {len(roles) - 40} more."
        return discord.Embed(title=title, description=explanation + "\n\n" + listing, color=discord.Color.blurple())

    async def setup_embed(self, guild: discord.Guild) -> discord.Embed:
        basic = await self.admin_selfrole_ids(guild)
        restricted = await self.restricted_role_ids(guild)
        _, data = await self.ensure_setup_picker(guild)
        published = (
            f"<#{data.get('channel_id')}> · message `{data.get('message_id')}`"
            if data.get("message_id") else "Not published"
        )
        embed = discord.Embed(
            title="RoleTools setup",
            description=(
                "Manage one shared self-role catalog without creating internal option names. "
                "Basic roles also work with Red's `selfrole`; advanced self-roles stay inside RoleTools."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Basic self-roles", value=str(len(basic)))
        embed.add_field(name="Advanced self-roles", value=str(len(restricted)))
        unsafe = []
        for role_id in basic:
            role = guild.get_role(role_id)
            if role is None:
                continue
            settings = await self.config.role(role).all()
            if any(settings.get(key) for key in ADVANCED_KEYS):
                unsafe.append(role.name)
        layout_names = {
            "private": "Private menu",
            "dropdown": "Public dropdowns",
            "reactions": "Combined reaction menu",
            "role_channel": "Managed role channel",
        }
        embed.add_field(name="Public card", value=published, inline=False)
        embed.add_field(name="Published layout", value=layout_names.get(data.get("layout"), "Private menu"))
        if unsafe:
            embed.add_field(
                name="Needs attention",
                value=("These basic roles have advanced RoleTools rules that `selfrole` can bypass: "
                       + ", ".join(unsafe[:10])),
                inline=False,
            )
        embed.set_footer(text="Select roles in batches of up to 25; reopen a selector for additional roles.")
        return embed

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: Context) -> None:
        if ctx.guild is None or ctx.command is None:
            return
        if ctx.command.qualified_name in {
            "selfroleset add", "selfroleset remove", "selfroleset clear"
        }:
            await self.refresh_setup_picker(ctx.guild)

    async def publish_setup_picker(
        self, guild: discord.Guild, channel: discord.TextChannel, layout: str = "private"
    ):
        channel_id = int(getattr(channel, "id", 0) or 0)
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False, "That channel is unavailable or is not a text channel."
        pickers, data = await self.ensure_setup_picker(guild)
        data["role_ids"] = await self.combined_catalog_ids(guild)
        if layout == "dropdown" and len(data["role_ids"]) > PUBLIC_SELECT_PAGE_SIZE * PUBLIC_SELECT_MAX_PAGES:
            return False, "The public dropdown supports up to 125 roles. Use the private menu or a reaction layout for this catalog."
        if layout == "role_channel" and not data["role_ids"]:
            return False, "Add at least one Basic or Advanced self-role before publishing a managed role channel."
        permissions = channel.permissions_for(guild.me)
        required = {"view_channel", "send_messages"}
        if layout != "role_channel":
            required.add("embed_links")
        if layout in {"reactions", "role_channel"}:
            required.update({"add_reactions", "read_message_history"})
        if layout in {"reactions", "role_channel"}:
            required.add("manage_messages")
        missing = [permission.replace("_", " ") for permission in sorted(required) if not getattr(permissions, permission, False)]
        if missing:
            return False, f"I need these permissions in {channel.mention}: {', '.join(missing)}."

        old_layout = data.get("layout", "private")
        old_channel = guild.get_channel(data.get("channel_id"))
        old_ids = list(data.get("reaction_message_ids", []))
        if not old_ids:
            old_ids = list(data.get("role_channel_message_ids", []))
        if not old_ids and data.get("message_id"):
            old_ids = [data["message_id"]]
        same_managed_layout = (
            old_layout == layout
            and old_channel is not None
            and old_channel.id == channel.id
            and layout in {"reactions", "role_channel"}
        )
        if old_layout in {"reactions", "role_channel"} and not same_managed_layout:
            await self._replace_managed_reaction_mappings(guild, data, [])
        reuse_ids = old_ids if same_managed_layout else []

        data["layout"] = layout
        data["channel_id"] = channel.id
        if layout == "reactions":
            if not same_managed_layout:
                data["message_id"] = None
            data["reaction_message_ids"] = reuse_ids
            data["role_channel_message_ids"] = []
            pickers[SETUP_PICKER_NAME] = data
            await self.config.guild(guild).pickers.set(pickers)
            if not await self.sync_reaction_picker(guild, SETUP_PICKER_NAME, data):
                return False, f"I could not publish the combined reaction menu in {channel.mention}."
            new_ids = set(data.get("reaction_message_ids", []))
        elif layout == "role_channel":
            if not same_managed_layout:
                data["message_id"] = None
            data["role_channel_message_ids"] = reuse_ids
            data["reaction_message_ids"] = []
            pickers[SETUP_PICKER_NAME] = data
            await self.config.guild(guild).pickers.set(pickers)
            if not await self.sync_role_channel(guild, SETUP_PICKER_NAME, data):
                return False, f"I could not publish the managed role channel in {channel.mention}."
            new_ids = set(data.get("role_channel_message_ids", []))
        else:
            data["reaction_message_ids"] = []
            data["role_channel_message_ids"] = []
            view = self.public_picker_view(guild, SETUP_PICKER_NAME, data)
            try:
                message = await channel.send(embed=self.picker_embed(data), view=view)
            except discord.HTTPException:
                return False, f"I could not publish the self-role menu in {channel.mention}."
            data["message_id"] = message.id
            pickers[SETUP_PICKER_NAME] = data
            await self.config.guild(guild).pickers.set(pickers)
            self.picker_views.append(view)
            new_ids = {message.id}

        if old_channel is not None:
            for old_message_id in old_ids:
                if old_message_id in new_ids and old_channel.id == channel.id:
                    continue
                try:
                    await (await old_channel.fetch_message(old_message_id)).delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    log.warning("Could not remove an earlier self-role menu message in guild %s", guild.id)
        if guild.id in self.settings:
            self.settings[guild.id]["pickers"] = await self.config.guild(guild).pickers()
        layout_name = {
            "private": "private role menu",
            "dropdown": "public dropdowns",
            "reactions": "combined reaction menu",
            "role_channel": "managed role channel",
        }[layout]
        return True, f"Published {layout_name} in {channel.mention}."

    @roletools.command(name="setup")
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_setup(self, ctx: Context) -> None:
        """Open the interactive RoleTools self-role setup card."""
        await self.refresh_setup_picker(ctx.guild)
        await ctx.send(embed=await self.setup_embed(ctx.guild), view=RoleToolsSetupView(self, ctx.author))
