from __future__ import annotations

import math
import re
from typing import List, Optional

import discord
from red_commons.logging import getLogger
from redbot.core import commands
from redbot.core.commands import Context
from redbot.core.utils.chat_formatting import humanize_list

from .abc import RoleToolsMixin
from .converter import RoleHierarchyConverter

roletools = RoleToolsMixin.roletools
log = getLogger("red.Sick-Cogs.RoleTools")

PICKER_PAGE_SIZE = 25
PICKER_NAME = re.compile(r"^[a-z0-9_-]{1,40}$")


def picker_pages(role_ids: List[int], page_size: int = PICKER_PAGE_SIZE) -> List[List[int]]:
    """Split picker role IDs into stable Discord-sized pages."""
    return [role_ids[start : start + page_size] for start in range(0, len(role_ids), page_size)]


def picker_page_index(current: int, page_count: int) -> int:
    if page_count < 1:
        return 0
    return current % page_count


class PickerRoleSelect(discord.ui.Select):
    def __init__(self, parent: "PickerMemberView", roles: List[discord.Role]):
        self.parent_view = parent
        owned = {role.id for role in parent.member.roles}
        options = []
        for role in roles:
            marker = "✓ " if role.id in owned else ""
            options.append(
                discord.SelectOption(
                    label=f"{marker}{role.name}"[:100],
                    value=str(role.id),
                    description=("Select to remove" if role.id in owned else "Select to add"),
                )
            )
        super().__init__(
            placeholder="Choose roles to add or remove",
            min_values=1,
            max_values=max(1, len(options)),
            options=options,
            custom_id=f"RTPickerRoles:{parent.guild_id}:{parent.picker_name}:{parent.page}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        cog = self.parent_view.cog
        added = []
        removed = []
        errors = []
        for value in self.values:
            role = interaction.guild.get_role(int(value))
            if role is None:
                errors.append("A selected role was deleted.")
                continue
            if role in interaction.user.roles:
                if not await cog.config.role(role).selfremovable():
                    errors.append(f"{role.name} is not self-removable.")
                    continue
                response = await cog.remove_roles(interaction.user, [role], "Role picker")
                if response:
                    errors.extend(item.reason for item in response)
                    continue
                removed.append(role)
                await cog.notify_role_change(interaction.user, role, "removed")
            else:
                if not await cog.config.role(role).selfassignable():
                    errors.append(f"{role.name} is not self-assignable.")
                    continue
                if getattr(interaction.user, "pending", False):
                    errors.append("Finish the server membership screening before choosing roles.")
                    continue
                wait_time = await cog.check_guild_verification(interaction.user, interaction.guild)
                if wait_time:
                    errors.append("You must spend more time in this server before choosing roles.")
                    continue
                response = await cog.give_roles(interaction.user, [role], "Role picker")
                if response:
                    errors.extend(item.reason for item in response)
                    continue
                added.append(role)
                await cog.notify_role_change(interaction.user, role, "received")

        result = []
        if added:
            result.append("Added: " + humanize_list([role.mention for role in added]))
        if removed:
            result.append("Removed: " + humanize_list([role.mention for role in removed]))
        result.extend(errors)
        refreshed = await PickerMemberView.create(
            cog, interaction.guild, interaction.user, self.parent_view.picker_name, self.parent_view.page
        )
        refreshed.embed.add_field(
            name="Last change",
            value=("\n".join(result) or "No roles changed.")[:1024],
            inline=False,
        )
        await interaction.message.edit(embed=refreshed.embed, view=refreshed)


class PickerPageButton(discord.ui.Button):
    def __init__(self, *, direction: int):
        emoji = "◀️" if direction < 0 else "▶️"
        super().__init__(style=discord.ButtonStyle.secondary, emoji=emoji)
        self.direction = direction

    async def callback(self, interaction: discord.Interaction) -> None:
        parent: PickerMemberView = self.view
        updated = await PickerMemberView.create(
            parent.cog,
            interaction.guild,
            interaction.user,
            parent.picker_name,
            parent.page + self.direction,
        )
        await interaction.response.edit_message(embed=updated.embed, view=updated)


class PickerMemberView(discord.ui.View):
    def __init__(self, cog, guild_id: int, member: discord.Member, picker_name: str, page: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.member = member
        self.picker_name = picker_name
        self.page = page
        self.embed = discord.Embed(title="Role picker")

    @classmethod
    async def create(cls, cog, guild: discord.Guild, member: discord.Member, picker_name: str, page: int = 0):
        pickers = await cog.config.guild(guild).pickers()
        data = pickers.get(picker_name)
        view = cls(cog, guild.id, member, picker_name, page)
        if data is None:
            view.embed = discord.Embed(title="Role picker unavailable", description="This picker was removed.")
            return view
        roles = [guild.get_role(int(role_id)) for role_id in data.get("role_ids", [])]
        roles = [role for role in roles if role is not None]
        pages = picker_pages([role.id for role in roles])
        if not pages:
            view.embed = discord.Embed(
                title=data.get("title") or "Choose your roles",
                description="No roles are available in this picker yet.",
                color=discord.Color.blurple(),
            )
            return view
        view.page = picker_page_index(page, len(pages))
        page_roles = [guild.get_role(role_id) for role_id in pages[view.page]]
        page_roles = [role for role in page_roles if role is not None]
        view.embed = discord.Embed(
            title=data.get("title") or "Choose your roles",
            description=(
                "Select any displayed role to toggle it. A ✓ means you already have it.\n"
                f"Page {view.page + 1} of {len(pages)} · {len(roles)} roles"
            ),
            color=discord.Color.blurple(),
        )
        view.add_item(PickerRoleSelect(view, page_roles))
        if len(pages) > 1:
            view.add_item(PickerPageButton(direction=-1))
            view.add_item(PickerPageButton(direction=1))
        return view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("Open your own private role picker.", ephemeral=True)
            return False
        return True


class PickerLaunchButton(discord.ui.Button):
    def __init__(self, guild_id: int, picker_name: str):
        super().__init__(
            label="Choose roles",
            style=discord.ButtonStyle.primary,
            custom_id=f"RTPickerOpen:{guild_id}:{picker_name}",
        )
        self.guild_id = guild_id
        self.picker_name = picker_name

    async def callback(self, interaction: discord.Interaction) -> None:
        view: PickerLaunchView = self.view
        member_view = await PickerMemberView.create(
            view.cog, interaction.guild, interaction.user, self.picker_name
        )
        await interaction.response.send_message(embed=member_view.embed, view=member_view, ephemeral=True)


class PickerLaunchView(discord.ui.View):
    def __init__(self, cog, guild_id: int, picker_name: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(PickerLaunchButton(guild_id, picker_name))


class RoleToolsPicker(RoleToolsMixin):
    """High-level scalable self-role picker commands."""

    @staticmethod
    def picker_embed(data: dict) -> discord.Embed:
        count = len(data.get("role_ids", []))
        pages = max(1, math.ceil(count / PICKER_PAGE_SIZE))
        return discord.Embed(
            title=data.get("title") or "Choose your roles",
            description=(data.get("description") or
                "Click **Choose roles** to open your private role list. "
                "Selecting a role adds it; selecting one you have removes it."
            ),
            color=discord.Color.blurple(),
        ).set_footer(text=f"{count} roles · {pages} page{'s' if pages != 1 else ''}")

    async def sync_picker(self, guild: discord.Guild, name: str, data: dict) -> bool:
        channel = guild.get_channel(data.get("channel_id"))
        if channel is None or not data.get("message_id"):
            return False
        try:
            message = await channel.fetch_message(data["message_id"])
            await message.edit(
                embed=self.picker_embed(data),
                view=PickerLaunchView(self, guild.id, name),
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log.exception("Could not synchronize RoleTools picker %s in guild %s", name, guild.id)
            return False
        return True

    async def register_picker_views(self) -> None:
        for guild_id, data in (await self.config.all_guilds()).items():
            for name, picker in data.get("pickers", {}).items():
                message_id = picker.get("message_id")
                if message_id:
                    view = PickerLaunchView(self, int(guild_id), name)
                    self.bot.add_view(view, message_id=int(message_id))
                    self.picker_views.append(view)

    @roletools.group(name="picker", invoke_without_command=True, hidden=True)
    @commands.admin_or_permissions(manage_roles=True)
    async def picker(self, ctx: Context) -> None:
        """Create and maintain scalable self-role picker cards."""
        names = sorted((await self.config.guild(ctx.guild).pickers()).keys())
        if not names:
            await ctx.send(
                f"No role pickers exist. Create one with `{ctx.clean_prefix}roletools picker create games #roles`."
            )
            return
        await ctx.send(
            "Role pickers: " + humanize_list([f"`{name}`" for name in names]) +
            f"\nUse `{ctx.clean_prefix}help roletools picker` for management commands."
        )

    @picker.command(name="create")
    async def picker_create(
        self, ctx: Context, name: str, channel: discord.TextChannel, *, title: Optional[str] = None
    ) -> None:
        """Create and publish a managed role picker card.

        `<name>` is a short manager-facing name. `<channel>` is where the card is sent.
        `[title]` is the public card title.
        """
        name = name.lower()
        if not PICKER_NAME.fullmatch(name):
            await ctx.send("Picker names may use 1–40 lowercase letters, numbers, hyphens, or underscores.")
            return
        pickers = await self.config.guild(ctx.guild).pickers()
        if name in pickers:
            await ctx.send(f"A picker named `{name}` already exists.")
            return
        data = {"title": (title or "Choose your roles")[:256], "channel_id": channel.id, "message_id": None, "role_ids": [], "sort": "alphabetical"}
        launch_view = PickerLaunchView(self, ctx.guild.id, name)
        try:
            message = await channel.send(embed=self.picker_embed(data), view=launch_view)
        except discord.HTTPException:
            await ctx.send(f"I could not publish a picker in {channel.mention}.")
            return
        data["message_id"] = message.id
        pickers[name] = data
        await self.config.guild(ctx.guild).pickers.set(pickers)
        if ctx.guild.id in self.settings:
            self.settings[ctx.guild.id]["pickers"] = pickers
        self.picker_views.append(launch_view)
        await ctx.send(
            f"Created `{name}` in {channel.mention}. Add roles with "
            f"`{ctx.clean_prefix}roletools picker add {name} @Role ...`."
        )

    @picker.command(name="add")
    async def picker_add(
        self, ctx: Context, name: str, roles: commands.Greedy[RoleHierarchyConverter]
    ) -> None:
        """Add one or more roles to a picker without rebuilding it."""
        name = name.lower()
        pickers = await self.config.guild(ctx.guild).pickers()
        if name not in pickers:
            await ctx.send(f"Picker `{name}` does not exist.")
            return
        roles = list(dict.fromkeys(roles))
        if not roles:
            await ctx.send("Provide at least one role to add.")
            return
        current = list(pickers[name].get("role_ids", []))
        current.extend(role.id for role in roles if role.id not in current)
        if pickers[name].get("sort") == "alphabetical":
            current.sort(key=lambda role_id: (ctx.guild.get_role(role_id).name.lower() if ctx.guild.get_role(role_id) else ""))
        pickers[name]["role_ids"] = current
        await self.config.guild(ctx.guild).pickers.set(pickers)
        await self.confirm_selfassignable(ctx, roles)
        synced = await self.sync_picker(ctx.guild, name, pickers[name])
        await ctx.send(f"Added {len(roles)} role(s) to `{name}`. Card sync: {'complete' if synced else 'failed' }.")

    @picker.command(name="remove")
    async def picker_remove(
        self, ctx: Context, name: str, roles: commands.Greedy[discord.Role]
    ) -> None:
        """Remove one or more roles from a picker without deleting the Discord roles."""
        name = name.lower()
        pickers = await self.config.guild(ctx.guild).pickers()
        if name not in pickers:
            await ctx.send(f"Picker `{name}` does not exist.")
            return
        remove_ids = {role.id for role in roles}
        before = len(pickers[name].get("role_ids", []))
        pickers[name]["role_ids"] = [role_id for role_id in pickers[name].get("role_ids", []) if role_id not in remove_ids]
        await self.config.guild(ctx.guild).pickers.set(pickers)
        synced = await self.sync_picker(ctx.guild, name, pickers[name])
        await ctx.send(f"Removed {before - len(pickers[name]['role_ids'])} role(s) from `{name}`. Card sync: {'complete' if synced else 'failed'}.")

    @picker.command(name="replace")
    async def picker_replace(self, ctx: Context, name: str, old_role: discord.Role, new_role: RoleHierarchyConverter) -> None:
        """Replace a picker role in place while retaining its position."""
        name = name.lower()
        pickers = await self.config.guild(ctx.guild).pickers()
        if name not in pickers or old_role.id not in pickers[name].get("role_ids", []):
            await ctx.send(f"{old_role.mention} is not in picker `{name}`.")
            return
        role_ids = pickers[name]["role_ids"]
        role_ids[role_ids.index(old_role.id)] = new_role.id
        pickers[name]["role_ids"] = list(dict.fromkeys(role_ids))
        await self.config.guild(ctx.guild).pickers.set(pickers)
        await self.confirm_selfassignable(ctx, [new_role])
        synced = await self.sync_picker(ctx.guild, name, pickers[name])
        await ctx.send(f"Replaced the role in `{name}`. Card sync: {'complete' if synced else 'failed'}.")

    @picker.command(name="move")
    async def picker_move(self, ctx: Context, name: str, role: discord.Role, position: int) -> None:
        """Move a picker role to a one-based position and switch to manual ordering."""
        name = name.lower()
        pickers = await self.config.guild(ctx.guild).pickers()
        if name not in pickers or role.id not in pickers[name].get("role_ids", []):
            await ctx.send(f"{role.mention} is not in picker `{name}`.")
            return
        role_ids = pickers[name]["role_ids"]
        role_ids.remove(role.id)
        role_ids.insert(max(0, min(position - 1, len(role_ids))), role.id)
        pickers[name]["sort"] = "manual"
        await self.config.guild(ctx.guild).pickers.set(pickers)
        synced = await self.sync_picker(ctx.guild, name, pickers[name])
        await ctx.send(f"Moved {role.mention} to position {role_ids.index(role.id) + 1}. Card sync: {'complete' if synced else 'failed'}.")

    @picker.command(name="sort")
    async def picker_sort(self, ctx: Context, name: str, order: str = "alphabetical") -> None:
        """Sort a picker alphabetically or retain manual order."""
        name = name.lower()
        order = order.lower()
        pickers = await self.config.guild(ctx.guild).pickers()
        if name not in pickers:
            await ctx.send(f"Picker `{name}` does not exist.")
            return
        if order not in {"alphabetical", "manual"}:
            await ctx.send("Order must be `alphabetical` or `manual`.")
            return
        if order == "alphabetical":
            pickers[name]["role_ids"].sort(key=lambda role_id: (ctx.guild.get_role(role_id).name.lower() if ctx.guild.get_role(role_id) else ""))
        pickers[name]["sort"] = order
        await self.config.guild(ctx.guild).pickers.set(pickers)
        synced = await self.sync_picker(ctx.guild, name, pickers[name])
        await ctx.send(f"Picker `{name}` now uses {order} order. Card sync: {'complete' if synced else 'failed'}.")

    @picker.command(name="view")
    async def picker_view(self, ctx: Context, name: str) -> None:
        """Show a picker's saved roles and published message."""
        name = name.lower()
        data = (await self.config.guild(ctx.guild).pickers()).get(name)
        if data is None:
            await ctx.send(f"Picker `{name}` does not exist.")
            return
        roles = [ctx.guild.get_role(role_id) for role_id in data.get("role_ids", [])]
        missing = sum(role is None for role in roles)
        names = [role.name for role in roles if role is not None]
        pages = max(1, math.ceil(len(names) / PICKER_PAGE_SIZE))
        description = humanize_list(names) if names else "No roles configured."
        if len(description) > 3500:
            description = description[:3497] + "..."
        embed = discord.Embed(title=f"Role picker: {name}", description=description, color=discord.Color.blurple())
        embed.add_field(name="Published", value=f"<#{data.get('channel_id')}> · message `{data.get('message_id')}`", inline=False)
        embed.set_footer(text=f"{len(names)} active · {missing} missing · {pages} member pages · {data.get('sort', 'manual')} order")
        await ctx.send(embed=embed)

    @picker.command(name="delete")
    async def picker_delete(self, ctx: Context, name: str) -> None:
        """Delete a picker configuration and remove its published card."""
        name = name.lower()
        pickers = await self.config.guild(ctx.guild).pickers()
        data = pickers.get(name)
        if data is None:
            await ctx.send(f"Picker `{name}` does not exist.")
            return
        channel = ctx.guild.get_channel(data.get("channel_id"))
        if channel is not None and data.get("message_id"):
            try:
                message = await channel.fetch_message(data["message_id"])
                await message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.warning("Could not remove published picker %s in guild %s", name, ctx.guild.id)
        del pickers[name]
        await self.config.guild(ctx.guild).pickers.set(pickers)
        if ctx.guild.id in self.settings:
            self.settings[ctx.guild.id]["pickers"] = pickers
        await ctx.send(f"Deleted picker `{name}` and its saved configuration.")

    @picker.command(name="sync")
    async def picker_sync(self, ctx: Context, name: str) -> None:
        """Refresh a published picker card from its saved configuration."""
        name = name.lower()
        data = (await self.config.guild(ctx.guild).pickers()).get(name)
        if data is None:
            await ctx.send(f"Picker `{name}` does not exist.")
            return
        await ctx.send("Picker synchronized." if await self.sync_picker(ctx.guild, name, data) else "Picker synchronization failed; check the saved channel and message.")
