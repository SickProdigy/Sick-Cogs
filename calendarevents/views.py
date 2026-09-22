"""Interactive Discord scheduled-event views for CalendarEvents."""

import discord


class OwnedView(discord.ui.View):
    def __init__(self, cog, owner, *, timeout=300):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.owner = owner

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("This calendar menu belongs to someone else.", ephemeral=True)
            return False
        return True


class CreateEventModal(discord.ui.Modal, title="Create Discord event"):
    event_title = discord.ui.TextInput(label="Event title", min_length=1, max_length=100)
    starts_at = discord.ui.TextInput(label="Start (ISO 8601 with offset)", placeholder="2026-10-10T18:00-04:00", max_length=40)
    ends_at = discord.ui.TextInput(label="End (ISO 8601 with offset)", placeholder="2026-10-10T20:00-04:00", max_length=40)
    location = discord.ui.TextInput(label="Location or link", placeholder="Discord / https://...", min_length=1, max_length=100)
    description = discord.ui.TextInput(label="Description", required=False, style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, view):
        super().__init__()
        self.parent_view = view

    async def on_submit(self, interaction):
        start = self.parent_view.cog._time(str(self.starts_at))
        end = self.parent_view.cog._time(str(self.ends_at))
        if start is None or end is None:
            await interaction.response.send_message("Start and end must be ISO times with an explicit timezone offset.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            event = await self.parent_view.cog.create_discord_event(
                interaction.guild,
                interaction.user,
                title=str(self.event_title).strip(),
                start=start,
                end=end,
                location=str(self.location).strip(),
                description=str(self.description).strip(),
            )
        except (PermissionError, ValueError) as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send("Discord could not create that event. Check my Create Events/Manage Events permission.", ephemeral=True)
            return
        await interaction.followup.send(f"Created **[{event.name}]({self.parent_view.cog.event_url(interaction.guild.id, event.id)})**.", ephemeral=True)


class CalendarDashboard(OwnedView):
    @discord.ui.button(label="Create event", style=discord.ButtonStyle.success)
    async def create_event(self, interaction, button):
        if not await self.cog.is_manager(interaction.user):
            await interaction.response.send_message("You need Manage Server or the configured calendar manager role.", ephemeral=True)
            return
        await interaction.response.send_modal(CreateEventModal(self))

    @discord.ui.button(label="Discord events", style=discord.ButtonStyle.primary)
    async def discord_events(self, interaction, button):
        try:
            events = await interaction.guild.fetch_scheduled_events(with_counts=True)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("Discord would not let me fetch this server's scheduled events.", ephemeral=True)
            return
        events = sorted(events, key=lambda event: event.start_time)[:20]
        if not events:
            await interaction.response.send_message("This server has no upcoming Discord events.", ephemeral=True)
            return
        lines = []
        for event in events:
            timestamp = int(event.start_time.timestamp())
            count = getattr(event, "user_count", None)
            interested = f" • {count} interested" if count is not None else ""
            lines.append(f"**[{event.name}]({self.cog.event_url(interaction.guild.id, event.id)})**\n<t:{timestamp}:F> (<t:{timestamp}:R>){interested}")
        embed = discord.Embed(title="Upcoming Discord events", description="\n\n".join(lines), color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Reminder settings", style=discord.ButtonStyle.secondary)
    async def reminder_settings(self, interaction, button):
        if not await self.cog.is_manager(interaction.user):
            await interaction.response.send_message("You need Manage Server or the configured calendar manager role.", ephemeral=True)
            return
        view = ReminderSettings(self.cog, self.owner)
        await interaction.response.send_message(embed=await view.embed(interaction.guild), view=view, ephemeral=True)

    @discord.ui.button(label="Done", style=discord.ButtonStyle.secondary)
    async def done(self, interaction, button):
        await interaction.response.edit_message(view=None)
        self.stop()


class ReminderChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view):
        super().__init__(placeholder="Choose reminder channel", channel_types=[discord.ChannelType.text, discord.ChannelType.news])
        self.parent_view = view

    async def callback(self, interaction):
        channel = self.values[0]
        await self.parent_view.cog.config.guild(interaction.guild).reminder_channel_id.set(channel.id)
        await interaction.response.edit_message(embed=await self.parent_view.embed(interaction.guild), view=self.parent_view)


class ReminderRoleSelect(discord.ui.RoleSelect):
    def __init__(self, view):
        super().__init__(placeholder="Choose optional reminder role")
        self.parent_view = view

    async def callback(self, interaction):
        role = self.values[0]
        if role == interaction.guild.default_role:
            await interaction.response.send_message("Choose an opt-in server role rather than @everyone.", ephemeral=True)
            return
        await self.parent_view.cog.config.guild(interaction.guild).reminder_role_id.set(role.id)
        await interaction.response.edit_message(embed=await self.parent_view.embed(interaction.guild), view=self.parent_view)


class ReminderOffsetsModal(discord.ui.Modal, title="Reminder schedule"):
    offsets = discord.ui.TextInput(label="Minutes before event", placeholder="1440 720 360 60", min_length=1, max_length=80)

    def __init__(self, view):
        super().__init__()
        self.parent_view = view

    async def on_submit(self, interaction):
        try:
            values = [int(value) for value in str(self.offsets).replace(",", " ").split()]
            offsets = self.parent_view.cog.normalize_offsets(values)
        except ValueError as error:
            await interaction.response.send_message(str(error) or "Enter reminder offsets as minutes separated by spaces.", ephemeral=True)
            return
        config = self.parent_view.cog.config.guild(interaction.guild)
        await config.reminder_offsets.set(offsets)
        await config.reminder_state.clear()
        await interaction.response.edit_message(embed=await self.parent_view.embed(interaction.guild), view=self.parent_view)


class ReminderSettings(OwnedView):
    def __init__(self, cog, owner):
        super().__init__(cog, owner)
        self.add_item(ReminderChannelSelect(self))
        self.add_item(ReminderRoleSelect(self))

    async def embed(self, guild):
        data = await self.cog.config.guild(guild).all()
        channel = guild.get_channel(data.get("reminder_channel_id"))
        role = guild.get_role(data.get("reminder_role_id"))
        embed = discord.Embed(title="Event reminder setup", description="Post several bounded reminders so members have multiple chances to notice an event.", color=discord.Color.blurple())
        embed.add_field(name="Channel", value=channel.mention if channel else "Disabled")
        embed.add_field(name="Role mention", value=role.mention if role else "None")
        embed.add_field(name="Offsets", value=", ".join(f"{value} minutes" for value in data.get("reminder_offsets", [])) or "None", inline=False)
        embed.set_footer(text="Maximum 8 reminders • 0 means event start • Maximum offset 4 weeks")
        return embed

    @discord.ui.button(label="Set times", style=discord.ButtonStyle.primary, row=2)
    async def set_times(self, interaction, button):
        await interaction.response.send_modal(ReminderOffsetsModal(self))

    @discord.ui.button(label="Clear role", style=discord.ButtonStyle.secondary, row=2)
    async def clear_role(self, interaction, button):
        await self.cog.config.guild(interaction.guild).reminder_role_id.clear()
        await interaction.response.edit_message(embed=await self.embed(interaction.guild), view=self)

    @discord.ui.button(label="Disable reminders", style=discord.ButtonStyle.danger, row=2)
    async def disable(self, interaction, button):
        await self.cog.config.guild(interaction.guild).reminder_channel_id.clear()
        await interaction.response.edit_message(embed=await self.embed(interaction.guild), view=self)

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success, row=2)
    async def done(self, interaction, button):
        embed = await self.embed(interaction.guild)
        embed.description = "Reminder setup saved."
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()
