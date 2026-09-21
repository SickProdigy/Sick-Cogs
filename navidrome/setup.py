from __future__ import annotations

from typing import Dict

import discord

from .client import NavidromeError


async def owner_check(interaction: discord.Interaction, author: discord.abc.User) -> bool:
    if interaction.user.id != author.id:
        await interaction.response.send_message(
            "Only the administrator who opened this setup panel can use it.", ephemeral=True
        )
        return False
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Manage Server permission is required.", ephemeral=True)
        return False
    return True


class IntervalModal(discord.ui.Modal):
    def __init__(self, cog, author: discord.abc.User, current: int):
        super().__init__(title="Navidrome polling interval")
        self.cog, self.author = cog, author
        self.minutes = discord.ui.TextInput(
            label="Minutes (15-1440)", default=str(current), min_length=1, max_length=4
        )
        self.add_item(self.minutes)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            minutes = int(str(self.minutes.value).strip())
        except ValueError:
            await interaction.response.send_message("Enter a whole number of minutes.", ephemeral=True)
            return
        if not 15 <= minutes <= 1440:
            await interaction.response.send_message(
                "The interval must be between 15 and 1440 minutes.", ephemeral=True
            )
            return
        await self.cog.config.guild(interaction.guild).interval_minutes.set(minutes)
        await self.cog._set_next_check(interaction.guild, minutes)
        await interaction.response.edit_message(
            content=f"Polling interval set to {minutes} minutes.",
            embed=await self.cog.setup_embed(interaction.guild),
            view=await NavidromeSetupView.create(self.cog, interaction.user, interaction.guild),
        )


class ConnectionSelect(discord.ui.Select):
    def __init__(self, parent: "NavidromeSetupView", profiles: Dict[str, dict], selected: str | None):
        options = [
            discord.SelectOption(
                label=name[:100],
                value=name,
                description=str(profile.get("base_url") or "Approved connection")[:100],
                default=name == selected,
            )
            for name, profile in sorted(profiles.items())[:25]
        ]
        super().__init__(placeholder="Choose an approved Navidrome connection", options=options, row=0)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        name = self.values[0]
        try:
            await (await self.parent_view.cog._client(name)).ping()
        except NavidromeError as exc:
            await interaction.followup.send(f"Connection test failed: {exc}", ephemeral=True)
            return
        group = self.parent_view.cog.config.guild(interaction.guild)
        await group.connection.set(name)
        await group.announcement_enabled.set(False)
        await group.announced_album_ids.set([])
        await interaction.edit_original_response(
            content=f"Selected `{name}`. Announcements remain disabled.",
            embed=await self.parent_view.cog.setup_embed(interaction.guild),
            view=await NavidromeSetupView.create(
                self.parent_view.cog, interaction.user, interaction.guild
            ),
        )


class AnnouncementChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "NavidromeSetupView"):
        super().__init__(
            placeholder="Choose the recently-added album channel",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
            row=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        resolved = await self.parent_view.cog._channel(interaction.guild, int(channel.id))
        if not resolved:
            await interaction.response.send_message(
                "I cannot send messages in that channel.", ephemeral=True
            )
            return
        await self.parent_view.cog.config.guild(interaction.guild).announcement_channel_id.set(
            int(channel.id)
        )
        await interaction.response.edit_message(
            content=f"Announcement channel set to <#{channel.id}>.",
            embed=await self.parent_view.cog.setup_embed(interaction.guild),
            view=await NavidromeSetupView.create(
                self.parent_view.cog, interaction.user, interaction.guild
            ),
        )


class NavidromeSetupView(discord.ui.View):
    def __init__(self, cog, author: discord.abc.User):
        super().__init__(timeout=600)
        self.cog, self.author = cog, author

    @classmethod
    async def create(cls, cog, author: discord.abc.User, guild: discord.Guild):
        view = cls(cog, author)
        profiles = await cog.config.connections()
        settings = await cog.config.guild(guild).all()
        if profiles:
            view.add_item(ConnectionSelect(view, profiles, settings.get("connection")))
        view.add_item(AnnouncementChannelSelect(view))
        toggle = next(item for item in view.children if getattr(item, "custom_id", None) == "navidrome:toggle")
        enabled = bool(settings.get("announcement_enabled"))
        toggle.label = "Disable announcements" if enabled else "Enable announcements"
        toggle.style = discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success
        return view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_check(interaction, self.author)

    @discord.ui.button(label="Set interval", style=discord.ButtonStyle.secondary, row=2)
    async def interval(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = await self.cog.config.guild(interaction.guild).interval_minutes()
        await interaction.response.send_modal(IntervalModal(self.cog, interaction.user, current))

    @discord.ui.button(
        label="Test connection", style=discord.ButtonStyle.primary, row=2,
        custom_id="navidrome:test",
    )
    async def test_connection(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        name = await self.cog.config.guild(interaction.guild).connection()
        if not name:
            await interaction.followup.send("Choose a connection first.", ephemeral=True)
            return
        try:
            await (await self.cog._client(name)).ping()
        except NavidromeError as exc:
            await interaction.followup.send(f"Connection test failed: {exc}", ephemeral=True)
            return
        await interaction.followup.send(f"Connection `{name}` is responding.", ephemeral=True)

    @discord.ui.button(label="Preview album", style=discord.ButtonStyle.secondary, row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        ok, message = await self.cog.preview_announcement(interaction.guild)
        await interaction.followup.send(message, ephemeral=not ok)

    @discord.ui.button(
        label="Enable announcements", style=discord.ButtonStyle.success, row=3,
        custom_id="navidrome:toggle",
    )
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        settings = await self.cog.config.guild(interaction.guild).all()
        if settings.get("announcement_enabled"):
            await self.cog.config.guild(interaction.guild).announcement_enabled.set(False)
            message = "Recently added album announcements are disabled."
        else:
            ok, message = await self.cog.enable_announcements(interaction.guild)
            if not ok:
                await interaction.followup.send(message, ephemeral=True)
                return
        await interaction.edit_original_response(
            content=message,
            embed=await self.cog.setup_embed(interaction.guild),
            view=await NavidromeSetupView.create(self.cog, interaction.user, interaction.guild),
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.secondary, row=3)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Navidrome setup closed.",
            embed=await self.cog.setup_embed(interaction.guild),
            view=None,
        )
