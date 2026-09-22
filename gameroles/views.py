"""Interactive setup and assignment views for GameRoles."""

from typing import Dict

import discord


class OwnedView(discord.ui.View):
    def __init__(self, owner: discord.Member, *, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.owner = owner

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message("This menu belongs to someone else.", ephemeral=True)
        return False


class ProfileModal(discord.ui.Modal, title="Create game-role profile"):
    name = discord.ui.TextInput(label="Profile name", placeholder="rust", min_length=1, max_length=32)

    def __init__(self, view):
        super().__init__()
        self.parent_view = view

    async def on_submit(self, interaction: discord.Interaction):
        key = self.parent_view.cog.normalize_game(str(self.name))
        if key is None:
            await interaction.response.send_message("Use only letters, numbers, hyphens, or underscores.", ephemeral=True)
            return
        async with self.parent_view.cog.config.guild(interaction.guild).games() as games:
            games.setdefault(key, {"manager_roles": [], "assignable_roles": []})
        await self.parent_view.rebuild(interaction, selected=key)


class ProfileSelect(discord.ui.Select):
    def __init__(self, view, games: Dict):
        options = [discord.SelectOption(label=key, value=key) for key in sorted(games)[:25]]
        super().__init__(placeholder="Choose a profile to manage", options=options)
        self.parent_view = view

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.rebuild(interaction, selected=self.values[0])


class ProfileRoleSelect(discord.ui.RoleSelect):
    def __init__(self, view, field: str, add: bool):
        noun = "manager" if field == "manager_roles" else "assignable"
        action = "Add" if add else "Remove"
        super().__init__(placeholder=f"{action} {noun} role(s)", min_values=1, max_values=10)
        self.parent_view = view
        self.field = field
        self.add = add

    async def callback(self, interaction: discord.Interaction):
        selected = self.parent_view.selected
        if selected is None:
            await interaction.response.send_message("Choose a profile first.", ephemeral=True)
            return
        roles = list(self.values)
        if self.field == "assignable_roles":
            invalid = [role for role in roles if not self.parent_view.cog._role_is_manageable(interaction.guild, role)]
            if invalid:
                await interaction.response.send_message("I cannot manage one or more selected roles. Check role position and integrations.", ephemeral=True)
                return
        else:
            roles = [role for role in roles if role != interaction.guild.default_role and not role.managed]
        async with self.parent_view.cog.config.guild(interaction.guild).games() as games:
            profile = games.setdefault(selected, {"manager_roles": [], "assignable_roles": []})
            ids = profile.setdefault(self.field, [])
            for role in roles:
                if self.add and role.id not in ids:
                    ids.append(role.id)
                elif not self.add and role.id in ids:
                    ids.remove(role.id)
        await self.parent_view.rebuild(interaction, selected=selected)


class SetupDashboard(OwnedView):
    def __init__(self, cog, owner):
        super().__init__(owner)
        self.cog = cog
        self.selected = None

    async def embed(self, guild):
        games = await self.cog.config.guild(guild).games()
        embed = discord.Embed(title="GameRoles setup", description="Create a profile, then choose its manager and assignable roles.", color=discord.Color.blurple())
        if not games:
            embed.add_field(name="Profiles", value="None yet", inline=False)
        else:
            lines = []
            for key in sorted(games):
                profile = self.cog._profile(games, key)
                marker = "▶ " if key == self.selected else ""
                lines.append(f"{marker}**{key}** — {len(profile.get('manager_roles', []))} manager, {len(profile.get('assignable_roles', []))} assignable")
            embed.add_field(name="Profiles", value="\n".join(lines), inline=False)
        if self.selected and self.selected in games:
            profile = self.cog._profile(games, self.selected)
            managers = [guild.get_role(i) for i in profile.get("manager_roles", [])]
            allowed = [guild.get_role(i) for i in profile.get("assignable_roles", [])]
            embed.add_field(name=f"{self.selected} managers", value=", ".join(r.mention for r in managers if r) or "None", inline=False)
            embed.add_field(name=f"{self.selected} assignable roles", value=", ".join(r.mention for r in allowed if r) or "None", inline=False)
        return embed

    async def rebuild(self, interaction, selected=None):
        self.selected = selected
        self.clear_items()
        games = await self.cog.config.guild(interaction.guild).games()
        if games:
            self.add_item(ProfileSelect(self, games))
        if self.selected:
            self.add_item(ProfileRoleSelect(self, "manager_roles", True))
            self.add_item(ProfileRoleSelect(self, "assignable_roles", True))
            self.add_item(RemoveRolesButton(self, "manager_roles"))
            self.add_item(RemoveRolesButton(self, "assignable_roles"))
            self.add_item(DeleteProfileButton(self))
            self.add_item(BackButton(self))
        else:
            self.add_item(CreateProfileButton(self))
        self.add_item(DoneButton(self))
        await interaction.response.edit_message(embed=await self.embed(interaction.guild), view=self)

    async def prepare(self, guild):
        games = await self.cog.config.guild(guild).games()
        if games:
            self.add_item(ProfileSelect(self, games))
        self.add_item(CreateProfileButton(self))
        self.add_item(DoneButton(self))


class BackButton(discord.ui.Button):
    def __init__(self, view):
        super().__init__(label="Back", style=discord.ButtonStyle.secondary)
        self.parent_view = view

    async def callback(self, interaction):
        await self.parent_view.rebuild(interaction)


class DoneButton(discord.ui.Button):
    def __init__(self, view):
        super().__init__(label="Done", style=discord.ButtonStyle.success)
        self.parent_view = view

    async def callback(self, interaction):
        self.parent_view.selected = None
        embed = await self.parent_view.embed(interaction.guild)
        embed.description = "Setup saved. Run the setup command again whenever you need to make changes."
        await interaction.response.edit_message(embed=embed, view=None)
        self.parent_view.stop()


class CreateProfileButton(discord.ui.Button):
    def __init__(self, view):
        super().__init__(label="Create profile", style=discord.ButtonStyle.success)
        self.parent_view = view

    async def callback(self, interaction):
        await interaction.response.send_modal(ProfileModal(self.parent_view))


class RemoveConfiguredSelect(discord.ui.Select):
    def __init__(self, view, roles):
        options = [discord.SelectOption(label=role.name, value=str(role.id)) for role in roles[:25]]
        super().__init__(placeholder="Choose role(s) to remove", min_values=1, max_values=len(options), options=options)
        self.parent_view = view

    async def callback(self, interaction):
        async with self.parent_view.setup.cog.config.guild(interaction.guild).games() as games:
            profile = games.get(self.parent_view.setup.selected, {})
            ids = profile.get(self.parent_view.field, [])
            for value in self.values:
                role_id = int(value)
                if role_id in ids:
                    ids.remove(role_id)
        await interaction.response.edit_message(content="Selected roles removed.", view=None)


class RemoveRolesView(OwnedView):
    def __init__(self, setup, field, roles):
        super().__init__(setup.owner)
        self.setup, self.field = setup, field
        self.add_item(RemoveConfiguredSelect(self, roles))


class RemoveRolesButton(discord.ui.Button):
    def __init__(self, view, field):
        noun = "managers" if field == "manager_roles" else "assignable roles"
        super().__init__(label=f"Remove {noun}", style=discord.ButtonStyle.secondary)
        self.parent_view, self.field = view, field

    async def callback(self, interaction):
        games = await self.parent_view.cog.config.guild(interaction.guild).games()
        profile = games.get(self.parent_view.selected, {})
        roles = [interaction.guild.get_role(i) for i in profile.get(self.field, [])]
        roles = [role for role in roles if role]
        if not roles:
            await interaction.response.send_message("There are no configured roles to remove.", ephemeral=True)
            return
        await interaction.response.send_message("Choose the roles to remove:", view=RemoveRolesView(self.parent_view, self.field, roles), ephemeral=True)


class DeleteProfileButton(discord.ui.Button):
    def __init__(self, view):
        super().__init__(label="Delete profile", style=discord.ButtonStyle.danger)
        self.parent_view = view

    async def callback(self, interaction):
        async with self.parent_view.cog.config.guild(interaction.guild).games() as games:
            games.pop(self.parent_view.selected, None)
        await self.parent_view.rebuild(interaction)


class AssignmentProfileSelect(discord.ui.Select):
    def __init__(self, view, games):
        super().__init__(placeholder="Choose a game profile", options=[discord.SelectOption(label=k, value=k) for k in sorted(games)[:25]])
        self.parent_view = view

    async def callback(self, interaction):
        self.parent_view.selected_profile = self.values[0]
        self.parent_view.selected_role = None
        self.parent_view.refresh_items()
        await interaction.response.edit_message(embed=self.parent_view.embed(), view=self.parent_view)


class TargetSelect(discord.ui.UserSelect):
    def __init__(self, view):
        super().__init__(placeholder="Choose a member")
        self.parent_view = view

    async def callback(self, interaction):
        self.parent_view.target = self.values[0]
        await interaction.response.edit_message(embed=self.parent_view.embed(), view=self.parent_view)


class AllowedRoleSelect(discord.ui.Select):
    def __init__(self, view, profile):
        roles = [view.owner.guild.get_role(i) for i in profile.get("assignable_roles", [])]
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in roles if r][:25]
        super().__init__(placeholder="Choose an approved role", options=options)
        self.parent_view = view

    async def callback(self, interaction):
        self.parent_view.selected_role = interaction.guild.get_role(int(self.values[0]))
        await interaction.response.edit_message(embed=self.parent_view.embed(), view=self.parent_view)


class AssignmentDashboard(OwnedView):
    def __init__(self, cog, owner, games):
        super().__init__(owner)
        self.cog, self.games = cog, games
        self.selected_profile = None
        self.target = None
        self.selected_role = None
        self.refresh_items()

    def refresh_items(self):
        self.clear_items()
        self.add_item(AssignmentProfileSelect(self, self.games))
        if self.selected_profile:
            self.add_item(TargetSelect(self))
            profile = self.games[self.selected_profile]
            if profile.get("assignable_roles"):
                self.add_item(AllowedRoleSelect(self, profile))
            self.add_item(AssignmentAction(self, True))
            self.add_item(AssignmentAction(self, False))

    def embed(self):
        embed = discord.Embed(title="Manage game roles", description="Choose a profile, member, and approved role.", color=discord.Color.blurple())
        embed.add_field(name="Profile", value=self.selected_profile or "Not selected")
        embed.add_field(name="Member", value=self.target.mention if self.target else "Not selected")
        embed.add_field(name="Role", value=self.selected_role.mention if self.selected_role else "Not selected")
        return embed


class AssignmentAction(discord.ui.Button):
    def __init__(self, view, add):
        super().__init__(label="Add role" if add else "Remove role", style=discord.ButtonStyle.success if add else discord.ButtonStyle.danger)
        self.parent_view, self.add = view, add

    async def callback(self, interaction):
        view = self.parent_view
        if not view.target or not view.selected_role or not view.selected_profile:
            await interaction.response.send_message("Choose a profile, member, and role first.", ephemeral=True)
            return
        role = view.selected_role
        games = await view.cog.config.guild(interaction.guild).games()
        if view.cog._authorized_profile(interaction.user, games, role, view.selected_profile) is None:
            await interaction.response.send_message("You no longer have permission to manage that profile or role.", ephemeral=True)
            return
        if not view.cog._role_is_manageable(interaction.guild, role):
            await interaction.response.send_message("I cannot manage that role. Check my permission and role position.", ephemeral=True)
            return
        try:
            if self.add:
                await view.target.add_roles(role, reason=f"GameRoles {view.selected_profile}: assigned by {interaction.user} ({interaction.user.id})")
            else:
                await view.target.remove_roles(role, reason=f"GameRoles {view.selected_profile}: removed by {interaction.user} ({interaction.user.id})")
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("Discord could not complete that role change.", ephemeral=True)
            return
        await interaction.response.send_message(f"{role.mention} was {'added to' if self.add else 'removed from'} {view.target.mention}.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
