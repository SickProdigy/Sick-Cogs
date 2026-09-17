"""Server events with portable calendar exports."""

import io
import uuid
from datetime import datetime, timezone

import discord
from redbot.core import Config, commands

from .models import CalendarEvent


class CalendarEvents(commands.Cog):
    """Create server events and export them without calendar-account access."""

    __author__ = ["SickProdigy"]
    __version__ = "0.1.0"
    CONFIG_IDENTIFIER = 9329894641121951861707075415179419308323035696195774470464762724187261

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=self.CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(events={})

    async def red_delete_data_for_user(self, *, requester, user_id):
        for guild_id, settings in (await self.config.all_guilds()).items():
            events = settings.get("events", {})
            filtered = {
                event_id: raw for event_id, raw in events.items()
                if not isinstance(raw, dict) or raw.get("creator_id") != user_id
            }
            if len(filtered) != len(events):
                await self.config.guild_from_id(guild_id).events.set(filtered)

    @staticmethod
    def _parse_time(value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None

    @staticmethod
    def _event_from_raw(raw):
        try:
            return CalendarEvent.from_raw(raw)
        except ValueError:
            return None

    @commands.group(name="calendar", aliases=["cal"], invoke_without_command=True)
    @commands.guild_only()
    async def calendar(self, ctx):
        """Create portable server events; use help calendar for commands."""
        await ctx.send_help()

    @calendar.command(name="add")
    async def calendar_add(self, ctx, starts_at: str, ends_at: str, *, title: str):
        """Add an event using ISO 8601 times, such as 2026-09-20T18:00Z."""
        start, end = self._parse_time(starts_at), self._parse_time(ends_at)
        if start is None or end is None or end <= start:
            await ctx.send("Use ordered UTC or offset times, for example 2026-09-20T18:00Z 2026-09-20T20:00Z.")
            return
        if not title.strip() or len(title.strip()) > 200:
            await ctx.send("Event titles must contain 1 to 200 characters.")
            return
        event = CalendarEvent(uuid.uuid4().hex[:8], ctx.guild.id, ctx.author.id, title.strip(), start, end)
        async with self.config.guild(ctx.guild).events() as events:
            events[event.event_id] = event.to_raw()
        await ctx.send(f"Added **{event.title}** with ID {event.event_id}. Use calendar show {event.event_id} for export links.")

    @calendar.command(name="list")
    async def calendar_list(self, ctx):
        """List this server's upcoming events."""
        events = [self._event_from_raw(raw) for raw in (await self.config.guild(ctx.guild).events()).values()]
        upcoming = sorted(
            (event for event in events if event and event.ends_at >= datetime.now(timezone.utc)),
            key=lambda event: event.starts_at,
        )
        if not upcoming:
            await ctx.send("This server has no upcoming calendar events.")
            return
        lines = [
            f"{event.event_id} • **{event.title}** — <t:{int(event.starts_at.timestamp())}:F>"
            for event in upcoming[:20]
        ]
        embed = discord.Embed(title="Upcoming server events", description="\n".join(lines), color=discord.Color.blurple())
        if len(upcoming) > 20:
            embed.set_footer(text=f"Showing 20 of {len(upcoming)} upcoming events.")
        await ctx.send(embed=embed)

    @calendar.command(name="show", aliases=["export"])
    async def calendar_show(self, ctx, event_id: str):
        """Show an event's Google link and attach its portable ICS file."""
        raw = (await self.config.guild(ctx.guild).events()).get(event_id.lower())
        event = self._event_from_raw(raw)
        if event is None:
            await ctx.send("That event ID was not found.")
            return
        embed = discord.Embed(title=event.title, color=discord.Color.blurple())
        embed.add_field(name="Starts", value=f"<t:{int(event.starts_at.timestamp())}:F>", inline=False)
        embed.add_field(name="Ends", value=f"<t:{int(event.ends_at.timestamp())}:F>", inline=False)
        embed.add_field(name="Add to Google Calendar", value=f"[Open template]({event.google_url()})", inline=False)
        embed.set_footer(text=f"Event ID: {event.event_id} • Portable ICS attached")
        attachment = discord.File(io.BytesIO(event.ics().encode("utf-8")), filename=f"{event.event_id}.ics")
        await ctx.send(embed=embed, file=attachment)

    @calendar.command(name="remove", aliases=["delete"])
    async def calendar_remove(self, ctx, event_id: str):
        """Remove your event; server managers may remove any event."""
        event_id = event_id.lower()
        async with self.config.guild(ctx.guild).events() as events:
            event = self._event_from_raw(events.get(event_id))
            if event is None:
                await ctx.send("That event ID was not found.")
                return
            if event.creator_id != ctx.author.id and not ctx.author.guild_permissions.manage_guild:
                await ctx.send("Only the event creator or a server manager can remove that event.")
                return
            del events[event_id]
        await ctx.send(f"Removed **{event.title}**.")
