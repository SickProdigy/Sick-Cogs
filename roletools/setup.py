from __future__ import annotations

import re
from typing import List

import discord
from red_commons.logging import getLogger
from redbot.core import commands
from redbot.core.commands import Context

from .abc import RoleToolsMixin
from .picker import (LEGACY_DEFAULT_DESCRIPTIONS, PUBLIC_SELECT_MAX_PAGES, PUBLIC_SELECT_PAGE_SIZE,
                     picker_description, reaction_emoji_key, reaction_emoji_value)

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
            placeholder="Choose the channel for this role menu",
            min_values=1,
            max_values=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = self.values[0]
        ok, message = await self.parent_view.cog.publish_role_menu(
            interaction.guild, self.parent_view.picker_name, channel, self.parent_view.layout
        )
        await interaction.edit_original_response(content=message, embed=None, view=None)


class SetupPublishView(discord.ui.View):
    def __init__(self, cog, author: discord.Member, layout: str, picker_name: str = SETUP_PICKER_NAME):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        self.layout = layout
        self.picker_name = picker_name
        self.add_item(SetupPublishSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Open your own RoleTools setup card.", ephemeral=True)
            return False
        return True


class SetupLayoutView(discord.ui.View):
    def __init__(self, cog, author: discord.Member, picker_name: str = SETUP_PICKER_NAME):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        self.picker_name = picker_name

    async def choose(self, interaction: discord.Interaction, layout: str, explanation: str) -> None:
        await interaction.response.edit_message(
            content=explanation + "\n\nNow choose the channel to publish or move it to.",
            view=SetupPublishView(self.cog, self.author, layout, self.picker_name),
        )

    @discord.ui.button(label="Button Role Menu", style=discord.ButtonStyle.primary)
    async def private_picker(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "private", "One public button opens a private, paged role menu for each member. Best for large catalogs.")

    @discord.ui.button(label="Public dropdowns", style=discord.ButtonStyle.secondary)
    async def dropdown(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "dropdown", "Dropdowns appear directly on the public card, up to 125 roles.")

    @discord.ui.button(label="Single Reaction Card", style=discord.ButtonStyle.secondary)
    async def reactions(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "reactions", "One managed message holds 20 distinct reactions; overflow messages are added automatically.")

    @discord.ui.button(label="Managed Reaction Channel", style=discord.ButtonStyle.secondary)
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


class RemovePublishedMenuView(discord.ui.View):
    def __init__(self, cog, author: discord.Member):
        super().__init__(timeout=120)
        self.cog = cog
        self.author = author

    @discord.ui.button(label="Remove published menu", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        removed, message = await self.cog.unpublish_setup_picker(interaction.guild)
        await interaction.edit_original_response(content=message, embed=None, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Published menu kept.", embed=None, view=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Open your own RoleTools setup card.", ephemeral=True)
            return False
        return True


class SelfRoleAppearanceModal(discord.ui.Modal):
    def __init__(self, cog, guild: discord.Guild, data: dict):
        super().__init__(title="Role menu text")
        self.cog = cog
        self.guild = guild
        saved_description = (data.get("description") or "").strip()
        self.uses_adaptive_default = (
            not saved_description or saved_description in LEGACY_DEFAULT_DESCRIPTIONS
        )
        self.default_description = picker_description(data)
        self.title_input = discord.ui.TextInput(
            label="Menu title", default=data.get("title") or "Choose your roles", max_length=256
        )
        self.description_input = discord.ui.TextInput(
            label="Member instructions",
            default=self.default_description,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.add_item(self.title_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        pickers, data = await self.cog.ensure_setup_picker(self.guild)
        data["title"] = str(self.title_input.value).strip() or "Choose your roles"
        submitted_description = str(self.description_input.value).strip()
        data["description"] = (
            ""
            if self.uses_adaptive_default and submitted_description == self.default_description
            else submitted_description
        )
        pickers[SETUP_PICKER_NAME] = data
        await self.cog.config.guild(self.guild).pickers.set(pickers)
        await self.cog.refresh_setup_picker(self.guild)
        embed = await self.cog.setup_embed(self.guild)
        await interaction.response.edit_message(
            embed=embed, view=RoleToolsSetupView(self.cog, interaction.user)
        )


class SelfRoleLibraryView(discord.ui.View):
    def __init__(self, cog, author: discord.Member):
        super().__init__(timeout=600)
        self.cog, self.author = cog, author

    @discord.ui.button(label="Ordinary self-roles", style=discord.ButtonStyle.primary)
    async def ordinary(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=await self.cog.catalog_embed(interaction.guild, restricted=False),
            view=CatalogEditorView(self.cog, interaction.user, restricted=False), ephemeral=True,
        )

    @discord.ui.button(label="Protected role rules", style=discord.ButtonStyle.secondary)
    async def protected(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=await self.cog.catalog_embed(interaction.guild, restricted=True),
            view=CatalogEditorView(self.cog, interaction.user, restricted=True), ephemeral=True,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Open your own RoleTools setup card.", ephemeral=True)
            return False
        return True


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

    @discord.ui.button(label="Self-role library", style=discord.ButtonStyle.primary, row=0)
    async def basic_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Choose ordinary self-roles, or protected roles that use RoleTools rules such as costs and durations.",
            view=SelfRoleLibraryView(self.cog, interaction.user), ephemeral=True,
        )

    @discord.ui.button(label="Menu text", style=discord.ButtonStyle.secondary, row=0)
    async def appearance(self, interaction: discord.Interaction, button: discord.ui.Button):
        _, data = await self.cog.ensure_setup_picker(interaction.guild)
        await interaction.response.send_modal(
            SelfRoleAppearanceModal(self.cog, interaction.guild, data)
        )

    @discord.ui.button(label="Quick publish all roles", style=discord.ButtonStyle.success, row=1)
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

    @discord.ui.button(label="Remove published menu", style=discord.ButtonStyle.danger, row=1)
    async def remove_published(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Remove the published role menu? Your Basic and Advanced role lists and Menu text settings will be kept.",
            view=RemovePublishedMenuView(self.cog, interaction.user),
            ephemeral=True,
        )


    @discord.ui.button(label="Manage role menus", style=discord.ButtonStyle.primary, row=2)
    async def manage_menus(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.open_named_menu_manager(interaction)


class NamedMenuCreateModal(discord.ui.Modal):
    def __init__(self, cog, author: discord.Member):
        super().__init__(title="Create role menu")
        self.cog = cog
        self.author = author
        self.name_input = discord.ui.TextInput(label="Menu name", placeholder="Games, Ranks, Platforms", max_length=40)
        self.title_input = discord.ui.TextInput(label="Public title", placeholder="Choose your game roles", max_length=256, required=False)
        self.add_item(self.name_input)
        self.add_item(self.title_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name, message = await self.cog.create_named_role_menu(
            interaction.guild, str(self.name_input.value), str(self.title_input.value)
        )
        if name is None:
            await interaction.response.send_message(message, ephemeral=True)
            return
        await interaction.response.send_message(
            message, embed=await self.cog.named_menu_embed(interaction.guild, name),
            view=NamedMenuEditorView(self.cog, self.author, name), ephemeral=True,
        )


class NamedMenuChooseSelect(discord.ui.Select):
    def __init__(self, parent: "NamedMenuManagerView", menus):
        options = [discord.SelectOption(
            label=(data.get("display_name") or name)[:100], value=name,
            description=f"{len(data.get('role_ids', []))} roles · {parent.cog.layout_name(data.get('layout'))}"[:100],
        ) for name, data in menus[:25]]
        super().__init__(placeholder="Choose a saved role menu", options=options, row=0)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        name = self.values[0]
        await interaction.response.send_message(
            embed=await self.parent_view.cog.named_menu_embed(interaction.guild, name),
            view=NamedMenuEditorView(self.parent_view.cog, interaction.user, name), ephemeral=True,
        )


class NamedMenuManagerView(discord.ui.View):
    def __init__(self, cog, author: discord.Member, menus):
        super().__init__(timeout=900)
        self.cog, self.author = cog, author
        if menus:
            self.add_item(NamedMenuChooseSelect(self, menus))

    @discord.ui.button(label="Create role menu", style=discord.ButtonStyle.success, row=1)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(NamedMenuCreateModal(self.cog, self.author))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Open your own RoleTools setup card.", ephemeral=True)
            return False
        return True


class NamedMenuRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "NamedMenuEditorView", *, add: bool):
        super().__init__(placeholder=("Add shared-catalog roles" if add else "Remove roles from this menu"),
                         min_values=1, max_values=25, row=0 if add else 1)
        self.parent_view, self.add = parent, add

    async def callback(self, interaction: discord.Interaction) -> None:
        changed, notes = await self.parent_view.cog.update_named_menu_roles(
            interaction.guild, self.parent_view.name, list(self.values), add=self.add
        )
        embed = await self.parent_view.cog.named_menu_embed(interaction.guild, self.parent_view.name)
        summary = f"{'Added' if self.add else 'Removed'} {changed} role(s)."
        if notes:
            summary += "\n" + "\n".join(notes)
        embed.add_field(name="Last change", value=summary[:1024], inline=False)
        await interaction.response.edit_message(
            embed=embed, view=NamedMenuEditorView(self.parent_view.cog, interaction.user, self.parent_view.name)
        )


class NamedMenuLayoutSelect(discord.ui.Select):
    def __init__(self, parent: "NamedMenuEditorView", current: str):
        labels = {"private": "Button Role Menu", "dropdown": "Public dropdowns",
                  "reactions": "Single Reaction Card", "role_channel": "Managed Reaction Channel"}
        options = [discord.SelectOption(label=label, value=value, default=value == current)
                   for value, label in labels.items()]
        super().__init__(placeholder="Choose the menu layout", options=options, row=2)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.cog.set_named_menu_layout(interaction.guild, self.parent_view.name, self.values[0])
        await interaction.response.edit_message(
            embed=await self.parent_view.cog.named_menu_embed(interaction.guild, self.parent_view.name),
            view=NamedMenuEditorView(self.parent_view.cog, interaction.user, self.parent_view.name),
        )


class NamedMenuTextModal(discord.ui.Modal):
    def __init__(self, cog, guild: discord.Guild, name: str, data: dict):
        super().__init__(title="Role menu text")
        self.cog, self.guild, self.name = cog, guild, name
        self.title_input = discord.ui.TextInput(label="Menu title", default=data.get("title") or "Choose your roles", max_length=256)
        self.description_input = discord.ui.TextInput(label="Member instructions", default=data.get("description") or picker_description(data),
                                                       style=discord.TextStyle.paragraph, max_length=1000, required=False)
        self.add_item(self.title_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        pickers = await self.cog.config.guild(self.guild).pickers()
        data = pickers.get(self.name)
        if data is None:
            await interaction.response.send_message("That saved role menu no longer exists.", ephemeral=True)
            return
        data["title"] = str(self.title_input.value).strip() or "Choose your roles"
        data["description"] = str(self.description_input.value).strip()
        pickers[self.name] = data
        await self.cog.save_role_menus(self.guild, pickers)
        if data.get("message_id"):
            await self.cog.sync_picker(self.guild, self.name, data)
        await interaction.response.edit_message(
            embed=await self.cog.named_menu_embed(self.guild, self.name),
            view=NamedMenuEditorView(self.cog, interaction.user, self.name),
        )


class RoleEmojiModal(discord.ui.Modal):
    def __init__(self, cog, guild: discord.Guild, name: str, role: discord.Role):
        super().__init__(title="Reaction emoji")
        self.cog, self.guild, self.name, self.role = cog, guild, name, role
        self.emoji_input = discord.ui.TextInput(
            label=f"Emoji for {role.name}"[:45], placeholder="🎮 or a server custom emoji", max_length=100
        )
        self.add_item(self.emoji_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ok, message = await self.cog.set_menu_role_emoji(
            self.guild, self.name, self.role, str(self.emoji_input.value)
        )
        await interaction.response.send_message(message, ephemeral=True)


class RoleEmojiSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "RoleEmojiSettingsView"):
        super().__init__(placeholder="Choose a menu role to change its emoji", min_values=1, max_values=1)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        data = (await self.parent_view.cog.config.guild(interaction.guild).pickers()).get(self.parent_view.name, {})
        if role.id not in data.get("role_ids", []):
            await interaction.response.send_message("That role is not part of this menu.", ephemeral=True)
            return
        await interaction.response.send_modal(RoleEmojiModal(self.parent_view.cog, interaction.guild, self.parent_view.name, role))


class RoleEmojiSettingsView(discord.ui.View):
    def __init__(self, cog, author: discord.Member, name: str):
        super().__init__(timeout=600)
        self.cog, self.author, self.name = cog, author, name
        self.add_item(RoleEmojiSelect(self))

    @discord.ui.button(label="Reset automatic emojis", style=discord.ButtonStyle.secondary, row=1)
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.reset_menu_role_emojis(interaction.guild, self.name)
        await interaction.response.send_message("This menu now uses the automatic numbered emoji defaults.", ephemeral=True)


class SharedEmojiModal(discord.ui.Modal):
    def __init__(self, cog, guild: discord.Guild, name: str, current: str):
        super().__init__(title="Managed channel reaction")
        self.cog, self.guild, self.name = cog, guild, name
        self.emoji_input = discord.ui.TextInput(label="Emoji used for every role", default=current or "👍", max_length=100)
        self.add_item(self.emoji_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        _, message = await self.cog.set_menu_shared_emoji(self.guild, self.name, str(self.emoji_input.value))
        await interaction.response.send_message(message, ephemeral=True)


class NamedMenuEditorView(discord.ui.View):
    def __init__(self, cog, author: discord.Member, name: str):
        super().__init__(timeout=900)
        self.cog, self.author, self.name = cog, author, name
        data = cog.settings.get(author.guild.id, {}).get("pickers", {}).get(name, {})
        self.add_item(NamedMenuRoleSelect(self, add=True))
        self.add_item(NamedMenuRoleSelect(self, add=False))
        self.add_item(NamedMenuLayoutSelect(self, data.get("layout", "private")))

    @discord.ui.button(label="Menu text", style=discord.ButtonStyle.secondary, row=3)
    async def text(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = (await self.cog.config.guild(interaction.guild).pickers()).get(self.name)
        if data is None:
            await interaction.response.send_message("That saved role menu no longer exists.", ephemeral=True)
            return
        await interaction.response.send_modal(NamedMenuTextModal(self.cog, interaction.guild, self.name, data))

    @discord.ui.button(label="Publish / Move", style=discord.ButtonStyle.success, row=3)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = (await self.cog.config.guild(interaction.guild).pickers()).get(self.name)
        if data is None:
            await interaction.response.send_message("That saved role menu no longer exists.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Choose the channel to publish or move this saved menu to.",
            view=SetupPublishView(self.cog, interaction.user, data.get("layout", "private"), self.name), ephemeral=True,
        )

    @discord.ui.button(label="Sync", style=discord.ButtonStyle.primary, row=3)
    async def sync(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = (await self.cog.config.guild(interaction.guild).pickers()).get(self.name)
        synced = bool(data and await self.cog.sync_picker(interaction.guild, self.name, data))
        await interaction.response.send_message(
            "Published menu synchronized." if synced else "This menu is not published yet, or synchronization failed.", ephemeral=True
        )

    @discord.ui.button(label="Unpublish", style=discord.ButtonStyle.danger, row=3)
    async def unpublish(self, interaction: discord.Interaction, button: discord.ui.Button):
        _, message = await self.cog.unpublish_role_menu(interaction.guild, self.name)
        await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(label="Use all library roles", style=discord.ButtonStyle.secondary, row=4)
    async def use_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        changed = await self.cog.fill_named_menu_from_library(interaction.guild, self.name)
        await interaction.response.edit_message(
            embed=await self.cog.named_menu_embed(interaction.guild, self.name),
            view=NamedMenuEditorView(self.cog, interaction.user, self.name),
        )
        await interaction.followup.send(f"Added {changed} library role(s) to this menu.", ephemeral=True)

    @discord.ui.button(label="Reaction settings", style=discord.ButtonStyle.secondary, row=4)
    async def reaction_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = (await self.cog.config.guild(interaction.guild).pickers()).get(self.name, {})
        layout = data.get("layout", "private")
        if layout == "reactions":
            await interaction.response.send_message(
                "Automatic numbered emojis are already assigned. Choose a role only when you want to override its emoji.",
                view=RoleEmojiSettingsView(self.cog, interaction.user, self.name), ephemeral=True,
            )
        elif layout == "role_channel":
            await interaction.response.send_modal(
                SharedEmojiModal(self.cog, interaction.guild, self.name, data.get("shared_emoji", "👍"))
            )
        else:
            await interaction.response.send_message("Reaction settings apply only to reaction layouts.", ephemeral=True)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Open your own RoleTools setup card.", ephemeral=True)
            return False
        return True


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


    @staticmethod
    def layout_name(layout: str) -> str:
        return {
            "private": "Button Role Menu", "dropdown": "Public dropdowns",
            "reactions": "Single Reaction Card", "role_channel": "Managed Reaction Channel",
        }.get(layout, "Button Role Menu")

    async def save_role_menus(self, guild: discord.Guild, pickers: dict) -> None:
        await self.config.guild(guild).pickers.set(pickers)
        if guild.id not in self.settings:
            self.settings[guild.id] = await self.config.guild(guild).all()
        self.settings[guild.id]["pickers"] = pickers

    async def named_role_menus(self, guild: discord.Guild):
        pickers = await self.config.guild(guild).pickers()
        return sorted(
            ((name, data) for name, data in pickers.items() if name != SETUP_PICKER_NAME),
            key=lambda item: (item[1].get("display_name") or item[0]).lower(),
        )

    async def create_named_role_menu(self, guild: discord.Guild, display_name: str, title: str):
        display_name = display_name.strip()
        if not display_name:
            return None, "Enter a menu name."
        slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")[:32]
        if not slug:
            return None, "The menu name needs at least one letter or number."
        pickers = await self.config.guild(guild).pickers()
        name = slug
        if name in pickers:
            return None, f"A saved role menu named **{display_name}** already exists."
        pickers[name] = {
            "display_name": display_name[:40], "title": title.strip()[:256] or display_name[:256],
            "description": "", "channel_id": None, "message_id": None, "role_ids": [],
            "sort": "alphabetical", "layout": "private", "reaction_message_ids": [],
            "role_channel_message_ids": [], "role_emojis": {}, "shared_emoji": "👍",
        }
        await self.save_role_menus(guild, pickers)
        return name, f"Created **{display_name}**. Add roles, choose a layout, then publish it."

    async def update_named_menu_roles(self, guild: discord.Guild, name: str, roles, *, add: bool):
        pickers = await self.config.guild(guild).pickers()
        data = pickers.get(name)
        if data is None or name == SETUP_PICKER_NAME:
            return 0, ["That saved role menu no longer exists."]
        allowed = set(await self.combined_catalog_ids(guild))
        current = list(data.get("role_ids", []))
        changed, notes = 0, []
        for role in roles:
            if add and role.id not in allowed:
                notes.append(f"Skipped {role.name}: add it to Basic or Advanced self-roles first.")
            elif add and role.id not in current:
                current.append(role.id)
                changed += 1
            elif not add and role.id in current:
                current.remove(role.id)
                changed += 1
        if data.get("sort", "alphabetical") == "alphabetical":
            current.sort(key=lambda role_id: (guild.get_role(role_id).name.lower() if guild.get_role(role_id) else ""))
        data["role_ids"] = current
        pickers[name] = data
        await self.save_role_menus(guild, pickers)
        if data.get("message_id"):
            await self.sync_picker(guild, name, data)
        return changed, notes

    async def set_named_menu_layout(self, guild: discord.Guild, name: str, layout: str) -> bool:
        if layout not in {"private", "dropdown", "reactions", "role_channel"}:
            return False
        pickers = await self.config.guild(guild).pickers()
        data = pickers.get(name)
        if data is None or name == SETUP_PICKER_NAME:
            return False
        data["layout"] = layout
        pickers[name] = data
        await self.save_role_menus(guild, pickers)
        return True


    async def fill_named_menu_from_library(self, guild: discord.Guild, name: str) -> int:
        pickers = await self.config.guild(guild).pickers()
        data = pickers.get(name)
        if data is None or name == SETUP_PICKER_NAME:
            return 0
        before = set(data.get("role_ids", []))
        data["role_ids"] = await self.combined_catalog_ids(guild)
        pickers[name] = data
        await self.save_role_menus(guild, pickers)
        if data.get("message_id"):
            await self.sync_picker(guild, name, data)
        return len(set(data["role_ids"]) - before)

    async def set_menu_role_emoji(self, guild: discord.Guild, name: str, role: discord.Role, emoji: str):
        pickers = await self.config.guild(guild).pickers()
        data = pickers.get(name)
        emoji = emoji.strip()
        if data is None or role.id not in data.get("role_ids", []):
            return False, "That role is not part of this saved menu."
        if not emoji:
            return False, "Enter one Unicode emoji or one custom server emoji."
        try:
            parsed = reaction_emoji_value(emoji)
            key = reaction_emoji_key(emoji)
            if isinstance(parsed, discord.PartialEmoji) and parsed.id and guild.get_emoji(parsed.id) is None:
                return False, "I cannot access that custom emoji in this server."
        except (TypeError, ValueError):
            return False, "I could not read that emoji."
        overrides = dict(data.get("role_emojis", {}))
        for role_id, current in overrides.items():
            if str(role_id) != str(role.id) and reaction_emoji_key(current) == key:
                return False, "Each role on a reaction card needs a different emoji."
        overrides[str(role.id)] = emoji
        data["role_emojis"] = overrides
        pickers[name] = data
        await self.save_role_menus(guild, pickers)
        synced = bool(data.get("message_id") and await self.sync_picker(guild, name, data))
        return True, f"{role.mention} now uses {emoji}." + (" The published card was synchronized." if synced else "")

    async def reset_menu_role_emojis(self, guild: discord.Guild, name: str) -> None:
        pickers = await self.config.guild(guild).pickers()
        data = pickers.get(name)
        if data is None:
            return
        data["role_emojis"] = {}
        pickers[name] = data
        await self.save_role_menus(guild, pickers)
        if data.get("message_id"):
            await self.sync_picker(guild, name, data)

    async def set_menu_shared_emoji(self, guild: discord.Guild, name: str, emoji: str):
        pickers = await self.config.guild(guild).pickers()
        data = pickers.get(name)
        emoji = emoji.strip()
        if data is None:
            return False, "That saved role menu no longer exists."
        if not emoji:
            return False, "Enter one Unicode emoji or one custom server emoji."
        try:
            parsed = reaction_emoji_value(emoji)
            if isinstance(parsed, discord.PartialEmoji) and parsed.id and guild.get_emoji(parsed.id) is None:
                return False, "I cannot access that custom emoji in this server."
        except (TypeError, ValueError):
            return False, "I could not read that emoji."
        data["shared_emoji"] = emoji
        pickers[name] = data
        await self.save_role_menus(guild, pickers)
        synced = bool(data.get("message_id") and await self.sync_picker(guild, name, data))
        return True, f"Managed role entries now use {emoji}." + (" The published channel was synchronized." if synced else "")

    async def named_menu_embed(self, guild: discord.Guild, name: str) -> discord.Embed:
        data = (await self.config.guild(guild).pickers()).get(name, {})
        roles = [guild.get_role(int(role_id)) for role_id in data.get("role_ids", [])]
        roles = [role for role in roles if role is not None]
        listing = "\n".join(f"• {role.name}" for role in roles[:30]) or "No roles selected yet."
        if len(roles) > 30:
            listing += f"\n…and {len(roles) - 30} more."
        published = f"<#{data.get('channel_id')}>" if data.get("message_id") else "Not published"
        embed = discord.Embed(
            title=data.get("display_name") or "Saved role menu", description=listing,
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Layout", value=self.layout_name(data.get("layout")))
        embed.add_field(name="Destination", value=published)
        embed.set_footer(text="Roles must first exist in the shared Basic or Advanced catalog.")
        return embed

    async def open_named_menu_manager(self, interaction: discord.Interaction) -> None:
        menus = await self.named_role_menus(interaction.guild)
        description = (
            "Create separate menus for games, ranks, platforms, or other groups. "
            "Every menu selects from the same Basic and Advanced self-role catalogs."
        )
        if len(menus) > 25:
            description += " Showing the first 25 menus."
        embed = discord.Embed(title="Manage role menus", description=description, color=discord.Color.blurple())
        embed.add_field(name="Saved menus", value=str(len(menus)))
        await interaction.response.send_message(
            embed=embed, view=NamedMenuManagerView(self, interaction.user, menus), ephemeral=True
        )

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
        title = "Protected role rules" if restricted else "Self-role library"
        explanation = (
            "These library roles use RoleTools rules so costs, requirements, conflicts, and durations cannot be bypassed."
            if restricted else
            "These roles work with Red Admin selfrole and can be reused across any RoleTools menu."
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
                "Keep one reusable self-role library, then publish all roles quickly or build separate menus "
                "for games, ranks, platforms, notifications, and more."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Self-role library", value=str(len(set(basic + restricted))))
        unsafe = []
        for role_id in basic:
            role = guild.get_role(role_id)
            if role is None:
                continue
            settings = await self.config.role(role).all()
            if any(settings.get(key) for key in ADVANCED_KEYS):
                unsafe.append(role.name)
        layout_names = {
            "private": "Button Role Menu",
            "dropdown": "Public dropdowns",
            "reactions": "Single Reaction Card",
            "role_channel": "Managed Reaction Channel",
        }
        embed.add_field(name="Quick all-role menu", value=published, inline=False)
        embed.add_field(name="Published layout", value=layout_names.get(data.get("layout"), "Button Role Menu"))
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

    async def publish_role_menu(
        self, guild: discord.Guild, name: str, channel: discord.TextChannel, layout: str = "private"
    ):
        channel_id = int(getattr(channel, "id", 0) or 0)
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False, "That channel is unavailable or is not a text channel."
        pickers = await self.config.guild(guild).pickers()
        data = pickers.get(name)
        if data is None:
            return False, "That saved role menu no longer exists."
        if name == SETUP_PICKER_NAME:
            data["role_ids"] = await self.combined_catalog_ids(guild)
        if name != SETUP_PICKER_NAME and not data.get("role_ids"):
            return False, "Add at least one shared-catalog role before publishing this menu."
        if layout == "dropdown" and len(data["role_ids"]) > PUBLIC_SELECT_PAGE_SIZE * PUBLIC_SELECT_MAX_PAGES:
            return False, "The public dropdown supports up to 125 roles. Use the Button Role Menu or a reaction layout for this catalog."
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
            pickers[name] = data
            await self.config.guild(guild).pickers.set(pickers)
            if not await self.sync_reaction_picker(guild, name, data):
                return False, f"I could not publish the combined reaction menu in {channel.mention}."
            new_ids = set(data.get("reaction_message_ids", []))
        elif layout == "role_channel":
            if not same_managed_layout:
                data["message_id"] = None
            data["role_channel_message_ids"] = reuse_ids
            data["reaction_message_ids"] = []
            pickers[name] = data
            await self.config.guild(guild).pickers.set(pickers)
            if not await self.sync_role_channel(guild, name, data):
                return False, f"I could not publish the managed role channel in {channel.mention}."
            new_ids = set(data.get("role_channel_message_ids", []))
        else:
            data["reaction_message_ids"] = []
            data["role_channel_message_ids"] = []
            view = self.public_picker_view(guild, name, data)
            try:
                message = await channel.send(embed=self.picker_embed(data), view=view)
            except discord.HTTPException:
                return False, f"I could not publish the self-role menu in {channel.mention}."
            data["message_id"] = message.id
            pickers[name] = data
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
            "private": "Button Role Menu",
            "dropdown": "public dropdowns",
            "reactions": "Single Reaction Card",
            "role_channel": "Managed Reaction Channel",
        }[layout]
        return True, f"Published {layout_name} in {channel.mention}."

    async def unpublish_role_menu(self, guild: discord.Guild, name: str):
        pickers = await self.config.guild(guild).pickers()
        data = pickers.get(name)
        if data is None:
            return False, "That saved role menu no longer exists."
        message_ids = self.managed_message_ids(data)
        channel = guild.get_channel(data.get("channel_id"))
        if not message_ids or channel is None:
            data["channel_id"] = None
            data["message_id"] = None
            data["reaction_message_ids"] = []
            data["role_channel_message_ids"] = []
            pickers[name] = data
            await self.config.guild(guild).pickers.set(pickers)
            return False, "No published role menu was found. Its saved roles and text were left unchanged."

        removed = 0
        for message_id in message_ids:
            try:
                await (await channel.fetch_message(int(message_id))).delete()
                removed += 1
            except discord.NotFound:
                continue
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not remove published RoleTools message %s in guild %s", message_id, guild.id)
                return False, "I could not remove every published menu message. Check my channel permissions and try again."

        if data.get("layout") in {"reactions", "role_channel"}:
            await self._replace_managed_reaction_mappings(guild, data, [])
        data["channel_id"] = None
        data["message_id"] = None
        data["reaction_message_ids"] = []
        data["role_channel_message_ids"] = []
        pickers[name] = data
        await self.config.guild(guild).pickers.set(pickers)
        if guild.id in self.settings:
            self.settings[guild.id]["pickers"] = pickers
        return True, f"Removed {removed} published role-menu message{'s' if removed != 1 else ''}. The saved menu roles and text were kept."

    async def publish_setup_picker(
        self, guild: discord.Guild, channel: discord.TextChannel, layout: str = "private"
    ):
        channel = guild.get_channel(int(getattr(channel, "id", 0) or 0))
        if not isinstance(channel, discord.TextChannel):
            return False, "That channel is unavailable or is not a text channel."
        return await self.publish_role_menu(guild, SETUP_PICKER_NAME, channel, layout)

    async def unpublish_setup_picker(self, guild: discord.Guild):
        await self.ensure_setup_picker(guild)
        return await self.unpublish_role_menu(guild, SETUP_PICKER_NAME)

    @roletools.group(name="menu", invoke_without_command=True)
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_menu(self, ctx: Context) -> None:
        """Create and manage separate role menus from the shared role catalog."""
        menus = await self.named_role_menus(ctx.guild)
        if not menus:
            await ctx.send(f"No saved role menus yet. Use `{ctx.clean_prefix}roletools setup` or `{ctx.clean_prefix}roletools menu create <name> [title]`.")
            return
        await ctx.send("Saved role menus: " + ", ".join(f"`{name}`" for name, _ in menus))

    @roletools_menu.command(name="create")
    async def roletools_menu_create(self, ctx: Context, name: str, *, title: str = "") -> None:
        """Create an unpublished named menu. Roles come from Basic/Advanced self-roles."""
        key, message = await self.create_named_role_menu(ctx.guild, name, title)
        await ctx.send(message + (f" Manager key: `{key}`." if key else ""))

    @roletools_menu.command(name="view")
    async def roletools_menu_view(self, ctx: Context, name: str) -> None:
        """Show one saved menu's roles, layout, and destination."""
        name = name.lower()
        if name not in dict(await self.named_role_menus(ctx.guild)):
            await ctx.send(f"Saved role menu `{name}` does not exist.")
            return
        await ctx.send(embed=await self.named_menu_embed(ctx.guild, name))

    @roletools_menu.command(name="add")
    async def roletools_menu_add(self, ctx: Context, name: str, roles: commands.Greedy[discord.Role]) -> None:
        """Add shared-catalog roles to a saved menu."""
        changed, notes = await self.update_named_menu_roles(ctx.guild, name.lower(), roles, add=True)
        await ctx.send(f"Added {changed} role(s)." + (("\n" + "\n".join(notes)) if notes else ""))

    @roletools_menu.command(name="remove")
    async def roletools_menu_remove(self, ctx: Context, name: str, roles: commands.Greedy[discord.Role]) -> None:
        """Remove roles from a saved menu without deleting the Discord roles."""
        changed, notes = await self.update_named_menu_roles(ctx.guild, name.lower(), roles, add=False)
        await ctx.send(f"Removed {changed} role(s)." + (("\n" + "\n".join(notes)) if notes else ""))

    @roletools_menu.command(name="layout")
    async def roletools_menu_layout(self, ctx: Context, name: str, layout: str) -> None:
        """Set private, dropdown, reactions, or role_channel layout."""
        if not await self.set_named_menu_layout(ctx.guild, name.lower(), layout.lower()):
            await ctx.send("Use a saved menu name and one of: `private`, `dropdown`, `reactions`, `role_channel`.")
            return
        await ctx.send(f"`{name.lower()}` will use {self.layout_name(layout.lower())} when published.")

    @roletools_menu.command(name="publish")
    async def roletools_menu_publish(self, ctx: Context, name: str, channel: discord.TextChannel) -> None:
        """Publish or move a saved menu using its selected layout."""
        name = name.lower()
        data = (await self.config.guild(ctx.guild).pickers()).get(name)
        if data is None or name == SETUP_PICKER_NAME:
            await ctx.send(f"Saved role menu `{name}` does not exist.")
            return
        _, message = await self.publish_role_menu(ctx.guild, name, channel, data.get("layout", "private"))
        await ctx.send(message)

    @roletools_menu.command(name="sync")
    async def roletools_menu_sync(self, ctx: Context, name: str) -> None:
        """Synchronize a saved menu's published Discord messages."""
        name = name.lower()
        data = (await self.config.guild(ctx.guild).pickers()).get(name)
        synced = bool(data and name != SETUP_PICKER_NAME and await self.sync_picker(ctx.guild, name, data))
        await ctx.send("Published menu synchronized." if synced else "That menu is not published, missing, or could not be synchronized.")

    @roletools_menu.command(name="unpublish")
    async def roletools_menu_unpublish(self, ctx: Context, name: str) -> None:
        """Remove published messages but keep the saved menu."""
        _, message = await self.unpublish_role_menu(ctx.guild, name.lower())
        await ctx.send(message)

    @roletools.command(name="setup")
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_setup(self, ctx: Context) -> None:
        """Open the interactive RoleTools self-role setup card."""
        await self.refresh_setup_picker(ctx.guild)
        await ctx.send(embed=await self.setup_embed(ctx.guild), view=RoleToolsSetupView(self, ctx.author))
