"""Interactive views for the Incidents staff center."""

import discord
from redbot.core import modlog


class StaffView(discord.ui.View):
    def __init__(self, cog, owner, *, timeout=300):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.owner = owner

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("This staff menu belongs to someone else.", ephemeral=True)
            return False
        if not await self.cog._has_access(interaction.user):
            await interaction.response.send_message("You no longer have access to the Incident Center.", ephemeral=True)
            return False
        return True


class IncidentHome(StaffView):
    def __init__(self, cog, owner, cases):
        super().__init__(cog, owner)
        self.cases = cog.sorted_cases(cases)

    def embed(self):
        embed = discord.Embed(title="Incident Center", description="Browse and summarize this server's existing Red ModLog cases.", color=discord.Color.orange())
        embed.add_field(name="Recorded cases", value=str(len(self.cases)))
        if self.cases:
            latest = self.cases[0]
            embed.add_field(name="Latest", value=self.cog.case_line(latest), inline=False)
        embed.set_footer(text="Staff only • Source: Red ModLog • No duplicate case database")
        return embed

    @discord.ui.button(label="Recent", style=discord.ButtonStyle.primary)
    async def recent(self, interaction, button):
        if not self.cases:
            await interaction.response.send_message("No ModLog cases are recorded.", ephemeral=True)
            return
        view = CaseBrowser(self.cog, self.owner, self.cases)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="Summary", style=discord.ButtonStyle.secondary)
    async def summary(self, interaction, button):
        await interaction.response.edit_message(embed=self.cog.summary_embed(self.cases, 7), view=SummaryView(self.cog, self.owner, self.cases))

    @discord.ui.button(label="Search user", style=discord.ButtonStyle.secondary)
    async def search_user(self, interaction, button):
        await interaction.response.send_modal(UserSearchModal(self))

    @discord.ui.button(label="Search case", style=discord.ButtonStyle.secondary)
    async def search_case(self, interaction, button):
        await interaction.response.send_modal(CaseSearchModal(self))

    @discord.ui.button(label="Filter", style=discord.ButtonStyle.secondary)
    async def filter_cases(self, interaction, button):
        await interaction.response.send_modal(FilterModal(self))

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success)
    async def done(self, interaction, button):
        embed = self.embed()
        embed.description = "Incident Center closed. Run the command again whenever you need it."
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()


class CaseBrowser(StaffView):
    def __init__(self, cog, owner, cases, *, index=0):
        super().__init__(cog, owner)
        self.cases = cog.sorted_cases(cases)
        self.index = min(max(index, 0), max(len(self.cases) - 1, 0))
        self._sync_buttons()

    def _sync_buttons(self):
        self.previous.disabled = self.index <= 0
        self.next.disabled = self.index >= len(self.cases) - 1

    def embed(self):
        return self.cog.case_embed(self.cases[self.index], position=self.index, total=len(self.cases))

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction, button):
        self.index -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction, button):
        self.index += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="User history", style=discord.ButtonStyle.primary)
    async def user_history(self, interaction, button):
        subject = getattr(self.cases[self.index], "user", None)
        user_id = subject if isinstance(subject, int) else getattr(subject, "id", None)
        if user_id is None:
            await interaction.response.send_message("This case has no searchable user ID.", ephemeral=True)
            return
        cases = await self.cog.user_cases(interaction.guild, user_id)
        if not cases:
            await interaction.response.send_message("No user history was found.", ephemeral=True)
            return
        view = CaseBrowser(self.cog, self.owner, cases)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        cases = await self.cog.all_cases(interaction.guild)
        view = IncidentHome(self.cog, self.owner, cases)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success)
    async def done(self, interaction, button):
        await interaction.response.edit_message(embed=self.embed(), view=None)
        self.stop()


class SummaryView(StaffView):
    def __init__(self, cog, owner, cases):
        super().__init__(cog, owner)
        self.cases = cases

    @discord.ui.button(label="Change period", style=discord.ButtonStyle.primary)
    async def period(self, interaction, button):
        await interaction.response.send_modal(SummaryPeriodModal(self))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        view = IncidentHome(self.cog, self.owner, self.cases)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success)
    async def done(self, interaction, button):
        await interaction.response.edit_message(view=None)
        self.stop()


class SummaryPeriodModal(discord.ui.Modal, title="Summary period"):
    days = discord.ui.TextInput(label="Days", default="30", min_length=1, max_length=3)

    def __init__(self, view):
        super().__init__()
        self.parent_view = view

    async def on_submit(self, interaction):
        try:
            days = max(1, min(int(str(self.days)), 365))
        except ValueError:
            await interaction.response.send_message("Days must be a number from 1 to 365.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=self.parent_view.cog.summary_embed(self.parent_view.cases, days), view=self.parent_view)


class UserSearchModal(discord.ui.Modal, title="Search incident user"):
    query = discord.ui.TextInput(label="Member mention or Discord user ID", min_length=1, max_length=30)

    def __init__(self, home):
        super().__init__()
        self.home = home

    async def on_submit(self, interaction):
        user_id = self.home.cog.parse_user_id(str(self.query))
        if user_id is None:
            await interaction.response.send_message("Enter a member mention or numeric Discord user ID.", ephemeral=True)
            return
        cases = await self.home.cog.user_cases(interaction.guild, user_id)
        if not cases:
            await interaction.response.send_message(f"No ModLog cases were found for `{user_id}`.", ephemeral=True)
            return
        view = CaseBrowser(self.home.cog, self.home.owner, cases)
        await interaction.response.edit_message(embed=view.embed(), view=view)


class CaseSearchModal(discord.ui.Modal, title="Search incident case"):
    case_number = discord.ui.TextInput(label="Red ModLog case number", min_length=1, max_length=10)

    def __init__(self, home):
        super().__init__()
        self.home = home

    async def on_submit(self, interaction):
        try:
            number = int(str(self.case_number))
            case = await modlog.get_case(number, interaction.guild, self.home.cog.bot)
        except (ValueError, RuntimeError):
            await interaction.response.send_message("That case number does not exist in this server.", ephemeral=True)
            return
        view = CaseBrowser(self.home.cog, self.home.owner, [case])
        await interaction.response.edit_message(embed=view.embed(), view=view)


class FilterModal(discord.ui.Modal, title="Filter incidents"):
    action = discord.ui.TextInput(label="Action type", placeholder="ban, kick, warning, mute...", required=False, max_length=40)
    days = discord.ui.TextInput(label="Days to include", default="30", min_length=1, max_length=3)

    def __init__(self, home):
        super().__init__()
        self.home = home

    async def on_submit(self, interaction):
        try:
            days = max(1, min(int(str(self.days)), 365))
        except ValueError:
            await interaction.response.send_message("Days must be a number from 1 to 365.", ephemeral=True)
            return
        action = str(self.action).strip() or None
        cases = self.home.cog.filter_cases(self.home.cases, action=action, days=days)
        if not cases:
            await interaction.response.send_message("No cases match those filters.", ephemeral=True)
            return
        view = CaseBrowser(self.home.cog, self.home.owner, cases)
        await interaction.response.edit_message(embed=view.embed(), view=view)


class AccessRoleSelect(discord.ui.RoleSelect):
    def __init__(self, view, add):
        super().__init__(placeholder=("Add" if add else "Remove") + " access role(s)", min_values=1, max_values=10)
        self.parent_view = view
        self.add = add

    async def callback(self, interaction):
        roles = [role for role in self.values if role != interaction.guild.default_role and not role.managed]
        async with self.parent_view.cog.config.guild(interaction.guild).access_roles() as role_ids:
            for role in roles:
                if self.add and role.id not in role_ids:
                    role_ids.append(role.id)
                elif not self.add and role.id in role_ids:
                    role_ids.remove(role.id)
        await interaction.response.edit_message(embed=await self.parent_view.embed(interaction.guild), view=self.parent_view)


class IncidentSetup(StaffView):
    def __init__(self, cog, owner):
        super().__init__(cog, owner)
        self.add_item(AccessRoleSelect(self, True))
        self.add_item(AccessRoleSelect(self, False))

    async def embed(self, guild):
        role_ids = await self.cog.config.guild(guild).access_roles()
        roles = [guild.get_role(role_id) for role_id in role_ids]
        embed = discord.Embed(title="Incident Center setup", description="Moderators and members with Manage Messages, Manage Server, or Administrator already have access.", color=discord.Color.orange())
        embed.add_field(name="Additional access roles", value=", ".join(role.mention for role in roles if role) or "None", inline=False)
        return embed

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success, row=2)
    async def done(self, interaction, button):
        embed = await self.embed(interaction.guild)
        embed.description = "Setup saved. Run the setup command again to make changes."
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()
