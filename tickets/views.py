"""Persistent launcher, ticket controls, and administrator setup UI."""

import discord


class TicketError(RuntimeError):
    pass


class OpenTicketModal(discord.ui.Modal, title="Open a support ticket"):
    topic = discord.ui.TextInput(
        label="Topic", placeholder="Account, technical help, purchase...", max_length=50
    )
    subject = discord.ui.TextInput(label="Subject", max_length=100)
    description = discord.ui.TextInput(
        label="How can staff help?",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=1800,
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel, number = await self.cog.create_ticket(
                interaction,
                topic=str(self.topic).strip(),
                subject=str(self.subject).strip(),
                description=str(self.description).strip(),
            )
        except TicketError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        await interaction.followup.send(
            f"Ticket #{number} is ready: {channel.mention}", ephemeral=True
        )


class LauncherView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Open ticket",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="tickets:open",
    )
    async def open_ticket(self, interaction, button):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Tickets can only be opened from a server.", ephemeral=True
            )
            return
        await interaction.response.send_modal(OpenTicketModal(self.cog))


class TicketControls(discord.ui.View):
    def __init__(self, cog, channel_id, record):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = int(channel_id)
        suffix = str(self.channel_id)
        self.claim.custom_id = f"tickets:{suffix}:claim"
        self.status.custom_id = f"tickets:{suffix}:status"
        self.close.custom_id = f"tickets:{suffix}:close"
        claimed = int(record.get("claimed_by_id", 0) or 0)
        closed = record.get("status") == "closed"
        self.claim.label = "Unclaim" if claimed else "Claim"
        self.claim.style = discord.ButtonStyle.secondary if claimed else discord.ButtonStyle.success
        self.claim.disabled = closed
        self.status.label = self.cog.status_label(record.get("status", "open"))
        self.status.disabled = closed
        self.close.label = "Reopen" if closed else "Close"
        self.close.style = discord.ButtonStyle.success if closed else discord.ButtonStyle.danger

    async def interaction_check(self, interaction):
        if interaction.guild is None or interaction.channel_id != self.channel_id:
            await interaction.response.send_message("This ticket control is no longer valid.", ephemeral=True)
            return False
        record = await self.cog.get_ticket(interaction.guild, self.channel_id)
        if record is None:
            await interaction.response.send_message("This ticket is no longer registered.", ephemeral=True)
            return False
        if interaction.user.id != record.get("owner_id") and not await self.cog.is_staff(interaction.user):
            await interaction.response.send_message("You do not have access to this ticket control.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success)
    async def claim(self, interaction, button):
        if not await self.cog.is_staff(interaction.user):
            await interaction.response.send_message("Only support staff can claim tickets.", ephemeral=True)
            return
        try:
            record = await self.cog.toggle_claim(interaction.guild, self.channel_id, interaction.user)
        except TicketError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        embed = self.cog.updated_control_embed(interaction.message, record)
        await interaction.response.edit_message(
            embed=embed,
            view=TicketControls(self.cog, self.channel_id, record),
        )

    @discord.ui.button(label="Open", style=discord.ButtonStyle.secondary)
    async def status(self, interaction, button):
        if not await self.cog.is_staff(interaction.user):
            await interaction.response.send_message("Only support staff can change ticket status.", ephemeral=True)
            return
        record = await self.cog.get_ticket(interaction.guild, self.channel_id)
        await interaction.response.send_message(
            "Choose the current ticket status.",
            view=StatusView(self.cog, interaction.user, self.channel_id, record),
            ephemeral=True,
        )

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close(self, interaction, button):
        record = await self.cog.get_ticket(interaction.guild, self.channel_id)
        reopening = record.get("status") == "closed"
        await interaction.response.send_message(
            "Reopen this ticket and restore requester replies?"
            if reopening
            else "Close this ticket and prevent further requester replies?",
            view=CloseConfirmation(
                self.cog,
                interaction.user,
                self.channel_id,
                reopening=reopening,
            ),
            ephemeral=True,
        )


class OwnedEphemeralView(discord.ui.View):
    def __init__(self, cog, owner, channel_id, *, timeout=120):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.owner_id = owner.id
        self.channel_id = int(channel_id)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This confirmation belongs to someone else.", ephemeral=True)
            return False
        return True


class TicketStatusSelect(discord.ui.Select):
    def __init__(self, parent, current):
        options = [
            discord.SelectOption(label="Open", value="open", emoji="🟢"),
            discord.SelectOption(label="Waiting on member", value="waiting_member", emoji="👤"),
            discord.SelectOption(label="Waiting on staff", value="waiting_staff", emoji="🛡️"),
        ]
        for option in options:
            option.default = option.value == current
        super().__init__(placeholder="Choose ticket status", options=options)
        self.parent_view = parent

    async def callback(self, interaction):
        try:
            record = await self.parent_view.cog.set_ticket_status(
                interaction.guild,
                self.parent_view.channel_id,
                self.values[0],
                interaction.user,
            )
        except TicketError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self.parent_view.cog.refresh_control_message(
            interaction.guild, self.parent_view.channel_id, record
        )
        await interaction.response.edit_message(
            content=f"Ticket status changed to {self.parent_view.cog.status_label(record['status'])}.",
            view=None,
        )
        self.parent_view.stop()


class StatusView(OwnedEphemeralView):
    def __init__(self, cog, owner, channel_id, record):
        super().__init__(cog, owner, channel_id)
        self.add_item(TicketStatusSelect(self, record.get("status", "open")))


class CloseConfirmation(OwnedEphemeralView):
    def __init__(self, cog, owner, channel_id, *, reopening):
        super().__init__(cog, owner, channel_id)
        self.reopening = reopening
        self.confirm.label = "Reopen ticket" if reopening else "Close ticket"
        self.confirm.style = discord.ButtonStyle.success if reopening else discord.ButtonStyle.danger

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        try:
            record = await self.cog.set_closed(
                interaction.guild,
                self.channel_id,
                interaction.user,
                closed=not self.reopening,
            )
        except TicketError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self.cog.refresh_control_message(interaction.guild, self.channel_id, record)
        await interaction.response.edit_message(
            content="Ticket reopened." if self.reopening else "Ticket closed.",
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="No changes made.", view=None)
        self.stop()


class SetupOwnedView(discord.ui.View):
    def __init__(self, cog, owner):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner.id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This setup panel belongs to someone else.", ephemeral=True)
            return False
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Manage Server permission is required.", ephemeral=True)
            return False
        return True


class LauncherChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent):
        super().__init__(
            placeholder="Choose public support launcher channel",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            row=0,
        )
        self.parent_view = parent

    async def callback(self, interaction):
        await self.parent_view.cog.config.guild(interaction.guild).launcher_channel_id.set(
            self.values[0].id
        )
        await interaction.response.edit_message(
            embed=await self.parent_view.cog.settings_embed(interaction.guild),
            view=self.parent_view,
        )


class TicketCategorySelect(discord.ui.ChannelSelect):
    def __init__(self, parent):
        super().__init__(
            placeholder="Choose private ticket category",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.category],
            row=1,
        )
        self.parent_view = parent

    async def callback(self, interaction):
        await self.parent_view.cog.config.guild(interaction.guild).category_id.set(
            self.values[0].id
        )
        await interaction.response.edit_message(
            embed=await self.parent_view.cog.settings_embed(interaction.guild),
            view=self.parent_view,
        )


class StaffRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent):
        super().__init__(
            placeholder="Replace support staff roles",
            min_values=1,
            max_values=10,
            row=2,
        )
        self.parent_view = parent

    async def callback(self, interaction):
        roles = [
            role for role in self.values
            if role != interaction.guild.default_role and not role.managed
        ]
        if not roles:
            await interaction.response.send_message(
                "Choose at least one ordinary staff role.", ephemeral=True
            )
            return
        await self.parent_view.cog.config.guild(interaction.guild).staff_role_ids.set(
            [role.id for role in roles]
        )
        await interaction.response.edit_message(
            embed=await self.parent_view.cog.settings_embed(interaction.guild),
            view=self.parent_view,
        )


class LogChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent):
        super().__init__(
            placeholder="Choose optional staff log channel",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            row=3,
        )
        self.parent_view = parent

    async def callback(self, interaction):
        await self.parent_view.cog.config.guild(interaction.guild).log_channel_id.set(
            self.values[0].id
        )
        await interaction.response.edit_message(
            embed=await self.parent_view.cog.settings_embed(interaction.guild),
            view=self.parent_view,
        )


class SetupView(SetupOwnedView):
    def __init__(self, cog, owner):
        super().__init__(cog, owner)
        self.add_item(LauncherChannelSelect(self))
        self.add_item(TicketCategorySelect(self))
        self.add_item(StaffRoleSelect(self))
        self.add_item(LogChannelSelect(self))

    @discord.ui.button(label="Publish launcher", style=discord.ButtonStyle.success, row=4)
    async def publish(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message = await self.cog.publish_launcher(interaction.guild)
        except TicketError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        await interaction.followup.send(
            f"Ticket launcher is ready: {message.jump_url}", ephemeral=True
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=4)
    async def refresh(self, interaction, button):
        await interaction.response.edit_message(
            embed=await self.cog.settings_embed(interaction.guild),
            view=self,
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.secondary, row=4)
    async def done(self, interaction, button):
        await interaction.response.edit_message(view=None)
        self.stop()
