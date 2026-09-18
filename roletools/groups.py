from __future__ import annotations

import re
from typing import List, Optional, Tuple

import discord
from redbot.core import commands
from redbot.core.commands import Context
from redbot.core.utils.chat_formatting import humanize_list, humanize_timedelta

from .abc import RoleToolsMixin

roletools = RoleToolsMixin.roletools
GROUP_LAYOUTS = {"private", "dropdown", "reactions", "role_channel"}


class RoleToolsGroups(RoleToolsMixin):
    """Reusable gated role groups backed by the named-menu publisher."""

    async def private_groups(self, guild: discord.Guild) -> dict:
        return dict(await self.config.guild(guild).private_groups())

    async def save_private_groups(self, guild: discord.Guild, groups: dict) -> None:
        await self.config.guild(guild).private_groups.set(groups)
        if guild.id in self.settings:
            self.settings[guild.id]["private_groups"] = groups

    @staticmethod
    def private_group_slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")[:32]

    async def create_private_group(self, guild: discord.Guild, display_name: str):
        display_name = display_name.strip()
        slug = self.private_group_slug(display_name)
        if not slug:
            return None, "Enter a group name containing a letter or number."
        groups = await self.private_groups(guild)
        if slug in groups:
            return None, f"A private group named **{display_name}** already exists."
        menu_name, message = await self.create_named_role_menu(
            guild, display_name, f"{display_name} roles"
        )
        if menu_name is None:
            return None, message
        groups[slug] = {
            "display_name": display_name[:40], "description": "", "menu_name": menu_name,
            "role_ids": [], "required_role_ids": [], "require_any": False,
            "conflict_role_ids": [], "gateway_role_id": None, "entry_cost": 0,
            "duration": None, "archived": False,
        }
        await self.save_private_groups(guild, groups)
        return slug, f"Created private group **{display_name}** with role menu `{menu_name}`."

    async def private_group_role_ids(self, guild: discord.Guild, data: dict) -> List[int]:
        menu = (await self.config.guild(guild).pickers()).get(data.get("menu_name"), {})
        return [int(role_id) for role_id in menu.get("role_ids", data.get("role_ids", []))]

    async def update_private_group_roles(
        self, guild: discord.Guild, name: str, roles: List[discord.Role], *, add: bool
    ):
        groups = await self.private_groups(guild)
        data = groups.get(name)
        if data is None:
            return 0, ["That private group does not exist."]
        gateway_id = data.get("gateway_role_id")
        roles = [role for role in roles if role.id != gateway_id]
        notes = []
        if add:
            _, catalog_notes = await self.update_role_catalog(guild, roles, restricted=True, add=True)
            notes.extend(catalog_notes)
        changed, menu_notes = await self.update_named_menu_roles(
            guild, data["menu_name"], roles, add=add
        )
        notes.extend(menu_notes)
        menu = (await self.config.guild(guild).pickers()).get(data["menu_name"], {})
        data["role_ids"] = list(menu.get("role_ids", []))
        groups[name] = data
        await self.save_private_groups(guild, groups)
        return changed, notes

    async def set_private_group_gateway(
        self, guild: discord.Guild, name: str, role: Optional[discord.Role]
    ) -> Tuple[bool, str]:
        groups = await self.private_groups(guild)
        data = groups.get(name)
        if data is None:
            return False, "That private group does not exist."
        old_gateway_id = data.get("gateway_role_id")
        if role is not None:
            if role.id in await self.private_group_role_ids(guild, data):
                return False, "The gateway role cannot also be one of the group's selectable roles."
            for other_name, other in groups.items():
                if other_name != name and other.get("gateway_role_id") == role.id:
                    return False, "That role is already the gateway for another private group."
        if old_gateway_id and (role is None or int(old_gateway_id) != role.id):
            old_setting = self.config.role_from_id(int(old_gateway_id))
            await old_setting.cost.set(int(data.get("gateway_previous_cost", 0)))
            previous_duration = data.get("gateway_previous_duration")
            if previous_duration:
                await old_setting.duration.set(int(previous_duration))
            else:
                await old_setting.duration.clear()
            data.pop("gateway_previous_cost", None)
            data.pop("gateway_previous_duration", None)
        if role is not None:
            if not old_gateway_id or int(old_gateway_id) != role.id:
                data["gateway_previous_cost"] = int(await self.config.role(role).cost())
                data["gateway_previous_duration"] = await self.config.role(role).duration()
            await self.update_role_catalog(guild, [role], restricted=True, add=True)
            await self.config.role(role).cost.set(int(data.get("entry_cost", 0)))
            duration = data.get("duration")
            if duration:
                await self.config.role(role).duration.set(int(duration))
            else:
                await self.config.role(role).duration.clear()
        data["gateway_role_id"] = role.id if role else None
        groups[name] = data
        await self.save_private_groups(guild, groups)
        return True, (f"Gateway set to {role.mention}." if role else "Gateway role cleared.")

    async def set_private_group_cost(self, guild: discord.Guild, name: str, amount: int):
        if amount < 0:
            return False, "Cost cannot be negative."
        groups = await self.private_groups(guild)
        data = groups.get(name)
        if data is None:
            return False, "That private group does not exist."
        if amount and not data.get("gateway_role_id"):
            return False, "Set a gateway role before adding an entry cost."
        data["entry_cost"] = amount
        groups[name] = data
        await self.save_private_groups(guild, groups)
        if data.get("gateway_role_id"):
            await self.config.role_from_id(int(data["gateway_role_id"])).cost.set(amount)
        return True, f"Entry cost set to {amount}."

    async def set_private_group_duration(self, guild: discord.Guild, name: str, minutes: int):
        if minutes < 0:
            return False, "Duration cannot be negative."
        groups = await self.private_groups(guild)
        data = groups.get(name)
        if data is None:
            return False, "That private group does not exist."
        if minutes and not data.get("gateway_role_id"):
            return False, "Set a gateway role before adding temporary access."
        seconds = minutes * 60 if minutes else None
        data["duration"] = seconds
        groups[name] = data
        await self.save_private_groups(guild, groups)
        if data.get("gateway_role_id"):
            setting = self.config.role_from_id(int(data["gateway_role_id"])).duration
            if seconds:
                await setting.set(seconds)
            else:
                await setting.clear()
        return True, (f"Access lasts {humanize_timedelta(seconds=seconds)}." if seconds else "Temporary access disabled.")

    async def set_private_group_relation(
        self, guild: discord.Guild, name: str, roles: List[discord.Role], *, relation: str, add: bool
    ):
        groups = await self.private_groups(guild)
        data = groups.get(name)
        if data is None:
            return 0, "That private group does not exist."
        key = "required_role_ids" if relation == "required" else "conflict_role_ids"
        current = [int(role_id) for role_id in data.get(key, [])]
        changed = 0
        for role in roles:
            if add and role.id not in current:
                current.append(role.id); changed += 1
            elif not add and role.id in current:
                current.remove(role.id); changed += 1
        data[key] = current
        groups[name] = data
        await self.save_private_groups(guild, groups)
        return changed, f"Updated {relation} roles."

    async def private_group_access(self, member: discord.Member, data: dict, *, joining=False):
        if data.get("archived"):
            return False, "This private group is archived."
        member_ids = {role.id for role in member.roles}
        required = {int(role_id) for role_id in data.get("required_role_ids", [])}
        if required:
            allowed = bool(member_ids & required) if data.get("require_any") else required <= member_ids
            if not allowed:
                qualifier = "one of" if data.get("require_any") else "all of"
                return False, f"You need {qualifier} this group's required roles."
        conflicts = member_ids & {int(role_id) for role_id in data.get("conflict_role_ids", [])}
        if conflicts:
            return False, "A role you already have conflicts with this private group."
        gateway_id = data.get("gateway_role_id")
        if gateway_id and not joining and int(gateway_id) not in member_ids:
            return False, "Join this private group before selecting its roles."
        return True, ""

    async def set_private_group_description(self, guild: discord.Guild, name: str, description: str):
        groups = await self.private_groups(guild)
        data = groups.get(name)
        if data is None:
            return False, "That private group does not exist."
        data["description"] = description.strip()[:1000]
        groups[name] = data
        await self.save_private_groups(guild, groups)
        return True, "Private-group description updated."

    async def set_private_group_archived(self, guild: discord.Guild, name: str, archived: bool):
        groups = await self.private_groups(guild)
        data = groups.get(name)
        if data is None:
            return False, "That private group does not exist."
        changed, message = await self.set_named_menu_archived(guild, data["menu_name"], archived)
        if not changed:
            return False, message
        data["archived"] = archived
        groups[name] = data
        await self.save_private_groups(guild, groups)
        return True, "Private group archived." if archived else "Private group restored."

    async def delete_private_group(self, guild: discord.Guild, name: str):
        groups = await self.private_groups(guild)
        data = groups.get(name)
        if data is None:
            return False, "That private group does not exist."
        deleted, message = await self.delete_named_role_menu(
            guild, data["menu_name"], allow_private_group=True
        )
        if not deleted:
            return False, message
        if data.get("gateway_role_id"):
            await self.set_private_group_gateway(guild, name, None)
            groups = await self.private_groups(guild)
        groups.pop(name)
        await self.save_private_groups(guild, groups)
        return True, "Deleted the private-group configuration. Discord roles were not deleted."

    async def publish_private_group(
        self, guild: discord.Guild, name: str, channel: discord.TextChannel, layout: str
    ):
        groups = await self.private_groups(guild)
        data = groups.get(name)
        if data is None:
            return False, "That private group does not exist."
        if data.get("archived"):
            return False, "Restore this private group before publishing it."
        if layout not in GROUP_LAYOUTS:
            return False, "Use one of: private, dropdown, reactions, role_channel."
        await self.set_named_menu_layout(guild, data["menu_name"], layout)
        return await self.publish_role_menu(guild, data["menu_name"], channel, layout)

    async def private_group_role_access(self, member: discord.Member, role: discord.Role):
        groups = []
        for data in (await self.private_groups(member.guild)).values():
            if role.id in await self.private_group_role_ids(member.guild, data):
                groups.append(data)
        if not groups:
            return True, ""
        failures = []
        for data in groups:
            allowed, message = await self.private_group_access(member, data)
            if allowed:
                return True, ""
            failures.append(message)
        return False, failures[0] if failures else "This role belongs to a private group."

    async def private_group_embed(self, guild: discord.Guild, name: str) -> discord.Embed:
        data = (await self.private_groups(guild)).get(name)
        if data is None:
            return discord.Embed(title="Private group unavailable")
        role_names = [guild.get_role(role_id) for role_id in await self.private_group_role_ids(guild, data)]
        required = [guild.get_role(int(role_id)) for role_id in data.get("required_role_ids", [])]
        conflicts = [guild.get_role(int(role_id)) for role_id in data.get("conflict_role_ids", [])]
        gateway = guild.get_role(int(data["gateway_role_id"])) if data.get("gateway_role_id") else None
        embed = discord.Embed(
            title=data.get("display_name") or name,
            description=data.get("description") or "A reusable gated role group.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Stable ID", value=f"`{name}`")
        embed.add_field(name="Role menu", value=f"`{data.get('menu_name')}`")
        role_text = humanize_list([r.mention for r in role_names if r]) or "None"
        required_text = humanize_list([r.mention for r in required if r]) or "None"
        conflict_text = humanize_list([r.mention for r in conflicts if r]) or "None"
        embed.add_field(name="Selectable roles", value=role_text[:1024], inline=False)
        embed.add_field(name="Required", value=required_text[:1024])
        embed.add_field(name="Requirement mode", value="Any" if data.get("require_any") else "All")
        embed.add_field(name="Conflicts", value=conflict_text[:1024], inline=False)
        embed.add_field(name="Gateway", value=gateway.mention if gateway else "None")
        embed.add_field(name="Entry cost", value=str(data.get("entry_cost", 0)))
        duration = data.get("duration")
        embed.add_field(name="Duration", value=humanize_timedelta(seconds=duration) if duration else "Permanent")
        return embed

    async def join_private_group(self, member: discord.Member, name: str):
        data = (await self.private_groups(member.guild)).get(name)
        if data is None or data.get("archived"):
            return False, "That private group is unavailable."
        gateway = member.guild.get_role(int(data["gateway_role_id"])) if data.get("gateway_role_id") else None
        if gateway is None:
            return False, "This group does not use a joinable gateway role."
        if gateway in member.roles:
            return True, "You already have access to this private group."
        allowed, message = await self.private_group_access(member, data, joining=True)
        if not allowed:
            return False, message
        response = await self.give_roles(member, [gateway], "Private role-group access")
        if response:
            return False, "".join(item.reason for item in response)
        return True, f"You joined **{data.get('display_name') or name}**."

    async def leave_private_group(self, member: discord.Member, name: str):
        data = (await self.private_groups(member.guild)).get(name)
        if data is None:
            return False, "That private group does not exist."
        roles = [member.guild.get_role(role_id) for role_id in await self.private_group_role_ids(member.guild, data)]
        gateway = member.guild.get_role(int(data["gateway_role_id"])) if data.get("gateway_role_id") else None
        remove = [role for role in roles if role and role in member.roles]
        if gateway and gateway in member.roles:
            remove.append(gateway)
        if not remove:
            return True, "You do not currently hold roles from this group."
        response = await self.remove_roles(member, remove, "Left private role group")
        if response:
            return False, "".join(item.reason for item in response)
        return True, f"You left **{data.get('display_name') or name}**."

    @roletools.group(name="group", aliases=["groups", "privategroups"], invoke_without_command=True)
    async def roletools_group(self, ctx: Context) -> None:
        """View or manage private gated role groups."""
        groups = await self.private_groups(ctx.guild)
        if not groups:
            await ctx.send(f"No private groups are configured. Managers can use `{ctx.clean_prefix}roletools group create <name>`.")
            return
        await ctx.send("Private groups: " + humanize_list([f"`{name}`" for name in sorted(groups)]))

    @roletools_group.command(name="join")
    async def roletools_group_join(self, ctx: Context, name: str) -> None:
        """Join a private group after its requirements and entry cost are checked."""
        _, message = await self.join_private_group(ctx.author, name.lower())
        await ctx.send(message)

    @roletools_group.command(name="leave")
    async def roletools_group_leave(self, ctx: Context, name: str) -> None:
        """Leave a private group and remove its gateway/selectable roles."""
        _, message = await self.leave_private_group(ctx.author, name.lower())
        await ctx.send(message)

    @roletools_group.command(name="view")
    async def roletools_group_view(self, ctx: Context, name: str) -> None:
        """Show a private group's access rules and roles."""
        await ctx.send(embed=await self.private_group_embed(ctx.guild, name.lower()))

    @roletools_group.command(name="create")
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_group_create(self, ctx: Context, *, name: str) -> None:
        """Create a private group and its unpublished named menu."""
        key, message = await self.create_private_group(ctx.guild, name)
        await ctx.send(message + (f" Stable ID: `{key}`." if key else ""))

    @roletools_group.group(name="roles", invoke_without_command=True)
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_group_roles(self, ctx: Context) -> None:
        """Add or remove private-group selectable roles."""
        await ctx.send_help()

    @roletools_group_roles.command(name="add")
    async def roletools_group_roles_add(self, ctx: Context, name: str, roles: commands.Greedy[discord.Role]) -> None:
        changed, notes = await self.update_private_group_roles(ctx.guild, name.lower(), list(roles), add=True)
        await ctx.send(f"Added {changed} role(s)." + (("\n" + "\n".join(notes)) if notes else ""))

    @roletools_group_roles.command(name="remove")
    async def roletools_group_roles_remove(self, ctx: Context, name: str, roles: commands.Greedy[discord.Role]) -> None:
        changed, notes = await self.update_private_group_roles(ctx.guild, name.lower(), list(roles), add=False)
        await ctx.send(f"Removed {changed} role(s)." + (("\n" + "\n".join(notes)) if notes else ""))

    @roletools_group.command(name="gateway")
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_group_gateway(self, ctx: Context, name: str, role: Optional[discord.Role] = None) -> None:
        """Set the group's gateway role; omit the role to clear it."""
        _, message = await self.set_private_group_gateway(ctx.guild, name.lower(), role)
        await ctx.send(message)

    @roletools_group.command(name="cost")
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_group_cost(self, ctx: Context, name: str, amount: int) -> None:
        """Set the one-time gateway entry cost; use 0 for free."""
        _, message = await self.set_private_group_cost(ctx.guild, name.lower(), amount)
        await ctx.send(message)

    @roletools_group.command(name="duration")
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_group_duration(self, ctx: Context, name: str, minutes: int) -> None:
        """Set gateway access in minutes; use 0 for permanent."""
        _, message = await self.set_private_group_duration(ctx.guild, name.lower(), minutes)
        await ctx.send(message)

    @roletools_group.group(name="required", invoke_without_command=True)
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_group_required(self, ctx: Context) -> None:
        await ctx.send_help()

    @roletools_group_required.command(name="add")
    async def roletools_group_required_add(self, ctx: Context, name: str, roles: commands.Greedy[discord.Role]) -> None:
        changed, message = await self.set_private_group_relation(ctx.guild, name.lower(), list(roles), relation="required", add=True)
        await ctx.send(f"Changed {changed} role(s). {message}")

    @roletools_group_required.command(name="remove")
    async def roletools_group_required_remove(self, ctx: Context, name: str, roles: commands.Greedy[discord.Role]) -> None:
        changed, message = await self.set_private_group_relation(ctx.guild, name.lower(), list(roles), relation="required", add=False)
        await ctx.send(f"Changed {changed} role(s). {message}")

    @roletools_group_required.command(name="mode")
    async def roletools_group_required_mode(self, ctx: Context, name: str, mode: str) -> None:
        groups = await self.private_groups(ctx.guild); data = groups.get(name.lower())
        if data is None or mode.lower() not in {"any", "all"}:
            await ctx.send("Use an existing group and `any` or `all`."); return
        data["require_any"] = mode.lower() == "any"; groups[name.lower()] = data
        await self.save_private_groups(ctx.guild, groups)
        await ctx.send(f"Required-role mode set to **{mode.lower()}**.")

    @roletools_group.group(name="conflicts", invoke_without_command=True)
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_group_conflicts(self, ctx: Context) -> None:
        await ctx.send_help()

    @roletools_group_conflicts.command(name="add")
    async def roletools_group_conflicts_add(self, ctx: Context, name: str, roles: commands.Greedy[discord.Role]) -> None:
        changed, message = await self.set_private_group_relation(ctx.guild, name.lower(), list(roles), relation="conflict", add=True)
        await ctx.send(f"Changed {changed} role(s). {message}")

    @roletools_group_conflicts.command(name="remove")
    async def roletools_group_conflicts_remove(self, ctx: Context, name: str, roles: commands.Greedy[discord.Role]) -> None:
        changed, message = await self.set_private_group_relation(ctx.guild, name.lower(), list(roles), relation="conflict", add=False)
        await ctx.send(f"Changed {changed} role(s). {message}")

    @roletools_group.command(name="description", aliases=["describe"])
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_group_description(self, ctx: Context, name: str, *, description: str = "") -> None:
        """Set the manager/member description for a private group."""
        _, message = await self.set_private_group_description(ctx.guild, name.lower(), description)
        await ctx.send(message)

    @roletools_group.command(name="archive")
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_group_archive(self, ctx: Context, name: str) -> None:
        """Archive an unpublished private group."""
        _, message = await self.set_private_group_archived(ctx.guild, name.lower(), True)
        await ctx.send(message)

    @roletools_group.command(name="restore")
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_group_restore(self, ctx: Context, name: str) -> None:
        """Restore an archived private group."""
        _, message = await self.set_private_group_archived(ctx.guild, name.lower(), False)
        await ctx.send(message)

    @roletools_group.command(name="delete")
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_group_delete(self, ctx: Context, name: str, confirmation: str = "") -> None:
        """Delete an unpublished private group; append `confirm`."""
        if confirmation.lower() != "confirm":
            await ctx.send(f"This deletes private group `{name.lower()}` and its saved menu. Re-run with `confirm`.")
            return
        _, message = await self.delete_private_group(ctx.guild, name.lower())
        await ctx.send(message)

    @roletools_group.command(name="publish")
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_group_publish(self, ctx: Context, name: str, channel: discord.TextChannel, layout: str = "private") -> None:
        """Publish a group through private, dropdown, reactions, or role_channel layout."""
        _, message = await self.publish_private_group(
            ctx.guild, name.lower(), channel, layout.lower()
        )
        await ctx.send(message)
