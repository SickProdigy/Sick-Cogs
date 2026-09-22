"""Guild-owned Google Calendar management."""

import io
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import discord
from redbot.core import Config, commands
from discord.ext import tasks
from .google import GoogleCalendarClient
from .models import CalendarEvent


class CalendarEvents(commands.Cog):
    """Manage native Discord events and optional shared Google calendars."""
    __author__ = ["SickProdigy"]
    __version__ = "0.3.0"
    CONFIG_IDENTIFIER = 9329894641121951861707075415179419308323035696195774470464762724187261

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=self.CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(
            calendar_id=None,
            channel_id=None,
            manager_role_id=None,
            timezone="UTC",
            ics_url=None,
            event_owners={},
            reminder_channel_id=None,
            reminder_role_id=None,
            reminder_offsets=[1440, 720, 360, 60],
            reminder_state={},
        )
        self.reminder_loop.start()

    def cog_unload(self):
        self.reminder_loop.cancel()

    async def red_delete_data_for_user(self, *, requester, user_id):
        for guild_id, data in (await self.config.all_guilds()).items():
            owners = data.get("event_owners", {})
            clean = {key: owner for key, owner in owners.items() if owner != user_id}
            if clean != owners:
                await self.config.guild_from_id(guild_id).event_owners.set(clean)

    async def is_manager(self, member):
        role_id = await self.config.guild(member.guild).manager_role_id()
        return member.guild_permissions.manage_guild or (
            role_id is not None and any(role.id == role_id for role in member.roles)
        )

    async def _manager(self, ctx):
        allowed = await self.is_manager(ctx.author)
        if not allowed:
            await ctx.send("You need **Manage Server** or the configured calendar manager role.")
        return allowed

    async def _client(self):
        tokens = await self.bot.get_shared_api_tokens("googlecalendar")
        if not tokens.get("client_email") or not tokens.get("private_key"):
            raise RuntimeError("The bot owner has not configured the Google Calendar service account.")
        return GoogleCalendarClient(tokens["client_email"], tokens["private_key"])

    async def _calendar_id(self, ctx):
        calendar_id = await self.config.guild(ctx.guild).calendar_id()
        if not calendar_id:
            await ctx.send(f"This server has no connected calendar. Run `{ctx.clean_prefix}calendarset serviceaccount` "
                           f"and `{ctx.clean_prefix}calendarset calendar <calendar ID>`.")
        return calendar_id

    async def _error(self, ctx, error):
        if isinstance(error, PermissionError):
            message = "Google denied access. Share the calendar with the service account and grant **Make changes to events**."
        elif isinstance(error, LookupError):
            message = "Google could not find that calendar or event. Check its ID."
        else:
            message = f"Google Calendar could not complete that request: {error}"
        await ctx.send(message)

    @staticmethod
    def _time(value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else None

    @staticmethod
    def _display(raw):
        value = raw.get("dateTime") or raw.get("date")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return f"<t:{int(parsed.timestamp())}:F>" if parsed.tzinfo else value
        except (AttributeError, ValueError):
            return value or "Unknown"

    @staticmethod
    def normalize_offsets(values):
        converted = [int(value) for value in values]
        if any(value < 0 or value > 40320 for value in converted):
            raise ValueError("Reminder offsets must be from 0 to 40320 minutes.")
        offsets = sorted(set(converted), reverse=True)
        if not offsets or len(offsets) > 8:
            raise ValueError("Choose between 1 and 8 reminder offsets from 0 to 40320 minutes.")
        return offsets

    @staticmethod
    def due_offset(start_time, offsets, sent, now=None):
        now = now or datetime.now(timezone.utc)
        remaining = (start_time.astimezone(timezone.utc) - now).total_seconds() / 60
        if remaining < -2:
            return None
        due = sorted((offset for offset in offsets if remaining <= offset and str(offset) not in sent))
        return due[0] if due else None

    @staticmethod
    def event_url(guild_id, event_id):
        return f"https://discord.com/events/{guild_id}/{event_id}"

    async def create_discord_event(self, guild, member, *, title, start, end, location, description=""):
        if not await self.is_manager(member):
            raise PermissionError("You need Manage Server or the configured calendar manager role.")
        title, location, description = title.strip(), location.strip(), description.strip()
        if not title or not location:
            raise ValueError("The event needs both a title and a location or link.")
        if start <= datetime.now(timezone.utc):
            raise ValueError("The event start must be in the future.")
        if end <= start:
            raise ValueError("The event end must be after its start.")
        return await guild.create_scheduled_event(
            name=title[:100],
            description=description[:1000] or "Created with CalendarEvents.",
            start_time=start,
            end_time=end,
            entity_type=discord.EntityType.external,
            privacy_level=discord.PrivacyLevel.guild_only,
            location=location[:100],
            reason=f"CalendarEvents: created by {member} ({member.id})",
        )

    async def _send_event_reminder(self, guild, event, channel, role, offset):
        start = event.start_time.astimezone(timezone.utc)
        title = "Event starting now" if offset == 0 else "Upcoming server event"
        embed = discord.Embed(title=title, description=f"**[{event.name}]({self.event_url(guild.id, event.id)})**", color=discord.Color.blurple())
        embed.add_field(name="Starts", value=f"<t:{int(start.timestamp())}:F> (<t:{int(start.timestamp())}:R>)", inline=False)
        location = getattr(event, "location", None)
        if location:
            embed.add_field(name="Location", value=location, inline=False)
        if getattr(event, "description", None):
            embed.add_field(name="Details", value=event.description[:1024], inline=False)
        content = role.mention if role else None
        await channel.send(content=content, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False))

    @tasks.loop(minutes=1)
    async def reminder_loop(self):
        now = datetime.now(timezone.utc)
        for guild in list(self.bot.guilds):
            data = await self.config.guild(guild).all()
            channel = guild.get_channel(data.get("reminder_channel_id"))
            if channel is None:
                continue
            role = guild.get_role(data.get("reminder_role_id")) if data.get("reminder_role_id") else None
            offsets = data.get("reminder_offsets") or []
            state = data.get("reminder_state") or {}
            try:
                events = await guild.fetch_scheduled_events(with_counts=False)
            except (discord.Forbidden, discord.HTTPException):
                continue
            active_keys = set()
            changed = False
            for event in events:
                if event.status != discord.EventStatus.scheduled:
                    continue
                start_key = str(int(event.start_time.timestamp()))
                event_key = f"{event.id}:{start_key}"
                active_keys.add(event_key)
                sent = set(state.get(event_key, []))
                offset = self.due_offset(event.start_time, offsets, sent, now)
                if offset is None:
                    continue
                try:
                    await self._send_event_reminder(guild, event, channel, role, offset)
                except (discord.Forbidden, discord.HTTPException):
                    continue
                sent.update(str(value) for value in offsets if value >= offset)
                state[event_key] = sorted(sent, key=int, reverse=True)
                changed = True
            stale = [key for key in state if key not in active_keys]
            for key in stale:
                state.pop(key, None)
                changed = True
            if changed:
                await self.config.guild(guild).reminder_state.set(state)

    @reminder_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_red_ready()

    @commands.group(name="calendar", aliases=["cal", "calendarevents"], invoke_without_command=True)
    @commands.guild_only()
    async def calendar(self, ctx):
        """View the server calendar and common commands."""
        embed = discord.Embed(title="Server Calendar",
            description="Events live in this server's Google Calendar and can be shared with other apps.",
            color=discord.Color.blurple())
        embed.add_field(name="Calendar", value="Connected" if await self.config.guild(ctx.guild).calendar_id() else "Not connected")
        embed.add_field(name="Timezone", value=await self.config.guild(ctx.guild).timezone())
        embed.add_field(name="Commands", inline=False, value=(
            f"`{ctx.clean_prefix}calendar list` - upcoming events\n"
            f"`{ctx.clean_prefix}calendar add <start> <end> <title>` - create an event\n"
            f"`{ctx.clean_prefix}calendar show <event ID>` - details and ICS export"))
        embed.set_footer(text=f"Server managers: {ctx.clean_prefix}calendarset")
        from .views import CalendarDashboard
        view = CalendarDashboard(self, ctx.author)
        await ctx.send(embed=embed, view=view)

    @calendar.command(name="add", aliases=["create"])
    async def calendar_add(self, ctx, starts_at: str, ends_at: str, *, title: str):
        """Create an event using ISO times with offsets."""
        if not await self._manager(ctx):
            return
        calendar_id = await self._calendar_id(ctx)
        start, end, title = self._time(starts_at), self._time(ends_at), title.strip()
        if not calendar_id:
            return
        if start is None or end is None or end <= start:
            await ctx.send("Use ordered ISO times with offsets, e.g. 2026-09-20T18:00-04:00 2026-09-20T20:00-04:00.")
            return
        if not 1 <= len(title) <= 200:
            await ctx.send("Event titles must contain 1 to 200 characters.")
            return
        zone = await self.config.guild(ctx.guild).timezone()
        payload = {"summary": title,
            "start": {"dateTime": start.isoformat(), "timeZone": zone},
            "end": {"dateTime": end.isoformat(), "timeZone": zone},
            "description": f"Created from Discord by {ctx.author} ({ctx.author.id})."}
        try:
            client = await self._client()
            event = await client.request("POST", client.calendar_path(calendar_id, "/events"), payload=payload)
        except (LookupError, PermissionError, RuntimeError, ValueError) as error:
            await self._error(ctx, error)
            return
        async with self.config.guild(ctx.guild).event_owners() as owners:
            owners[event["id"]] = ctx.author.id
        embed = discord.Embed(title=title, url=event.get("htmlLink"), color=discord.Color.green(),
                              description=f"Created for {self._display(event['start'])}.")
        embed.set_footer(text=f"Event ID: {event['id']}")
        await ctx.send(embed=embed)
        channel_id = await self.config.guild(ctx.guild).channel_id()
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        if channel and channel.id != ctx.channel.id:
            await channel.send(embed=embed)

    @calendar.command(name="list")
    async def calendar_list(self, ctx):
        """List the next 20 events."""
        calendar_id = await self._calendar_id(ctx)
        if not calendar_id:
            return
        try:
            client = await self._client()
            data = await client.request("GET", client.calendar_path(calendar_id, "/events"), params={
                "singleEvents": "true", "orderBy": "startTime",
                "timeMin": datetime.now(timezone.utc).isoformat(), "maxResults": "20"})
        except (LookupError, PermissionError, RuntimeError, ValueError) as error:
            await self._error(ctx, error)
            return
        events = data.get("items", [])
        if not events:
            await ctx.send("This server's calendar has no upcoming events.")
            return
        lines = [f"**{e.get('summary', 'Untitled event')}** - {self._display(e.get('start', {}))}\n`{e['id']}`" for e in events]
        await ctx.send(embed=discord.Embed(title="Upcoming server events",
                                           description="\n".join(lines), color=discord.Color.blurple()))

    @calendar.command(name="show", aliases=["export"])
    async def calendar_show(self, ctx, event_id: str):
        """Show an event and attach a portable ICS copy."""
        calendar_id = await self._calendar_id(ctx)
        if not calendar_id:
            return
        try:
            client = await self._client()
            event = await client.request("GET", client.calendar_path(calendar_id, f"/events/{event_id}"))
            start, end = event.get("start", {}), event.get("end", {})
            if "dateTime" not in start or "dateTime" not in end:
                await ctx.send("All-day events appear in the list, but ICS export is not supported yet.")
                return
            owners = await self.config.guild(ctx.guild).event_owners()
            model = CalendarEvent(event["id"], ctx.guild.id, owners.get(event["id"], self.bot.user.id),
                event.get("summary", "Untitled event"),
                datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00")),
                datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00")),
                event.get("description", ""), event.get("location", ""), event.get("htmlLink", ""))
        except (LookupError, PermissionError, RuntimeError, ValueError) as error:
            await self._error(ctx, error)
            return
        embed = discord.Embed(title=model.title, url=event.get("htmlLink"), color=discord.Color.blurple())
        embed.add_field(name="Starts", value=self._display(start), inline=False)
        embed.add_field(name="Ends", value=self._display(end), inline=False)
        embed.set_footer(text=f"Event ID: {model.event_id} - Portable ICS attached")
        await ctx.send(embed=embed, file=discord.File(io.BytesIO(model.ics().encode()),
                                                     filename=f"{model.event_id}.ics"))

    @calendar.command(name="remove", aliases=["delete", "cancel"])
    async def calendar_remove(self, ctx, event_id: str):
        """Delete an event from Google Calendar."""
        if not await self._manager(ctx):
            return
        calendar_id = await self._calendar_id(ctx)
        if not calendar_id:
            return
        try:
            client = await self._client()
            await client.request("DELETE", client.calendar_path(calendar_id, f"/events/{event_id}"))
        except (LookupError, PermissionError, RuntimeError, ValueError) as error:
            await self._error(ctx, error)
            return
        async with self.config.guild(ctx.guild).event_owners() as owners:
            owners.pop(event_id, None)
        await ctx.send("The event was removed from the shared Google Calendar.")

    @commands.group(name="calendarset", aliases=["calset"], invoke_without_command=True)
    @commands.guild_only()
    async def calendarset(self, ctx):
        """Configure the server calendar."""
        if await self._manager(ctx):
            await ctx.invoke(self.calendarset_info)

    @calendarset.command(name="info", aliases=["view", "settings"])
    async def calendarset_info(self, ctx):
        """Show safe server calendar settings."""
        if not await self._manager(ctx):
            return
        data = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(data["channel_id"]) if data["channel_id"] else None
        role = ctx.guild.get_role(data["manager_role_id"]) if data["manager_role_id"] else None
        embed = discord.Embed(title="Server Calendar Settings", color=discord.Color.blurple())
        embed.add_field(name="Google Calendar", value="Connected" if data["calendar_id"] else "Not connected")
        embed.add_field(name="Announcement channel", value=channel.mention if channel else "Not set")
        embed.add_field(name="Manager role", value=role.mention if role else "Manage Server only")
        embed.add_field(name="Timezone", value=data["timezone"])
        embed.add_field(name="ICS subscription", value="Configured" if data["ics_url"] else "Not set")
        reminder_channel = ctx.guild.get_channel(data.get("reminder_channel_id"))
        reminder_role = ctx.guild.get_role(data.get("reminder_role_id"))
        embed.add_field(name="Reminder channel", value=reminder_channel.mention if reminder_channel else "Disabled")
        embed.add_field(name="Reminder role", value=reminder_role.mention if reminder_role else "No mention")
        embed.add_field(name="Reminder offsets", value=", ".join(f"{value}m" for value in data.get("reminder_offsets", [])) or "None", inline=False)
        await ctx.send(embed=embed)

    @calendarset.command(name="serviceaccount")
    async def calendarset_serviceaccount(self, ctx):
        """Show the service-account email."""
        if not await self._manager(ctx):
            return
        email = (await self.bot.get_shared_api_tokens("googlecalendar")).get("client_email")
        await ctx.send(f"Share the calendar with `{email}` and grant **Make changes to events**."
                       if email else "The bot owner has not configured a Google Calendar service account.")

    @calendarset.command(name="calendar")
    async def calendarset_calendar(self, ctx, *, calendar_id: str):
        """Verify and connect a Google Calendar."""
        if not await self._manager(ctx):
            return
        calendar_id = calendar_id.strip()
        try:
            client = await self._client()
            await client.request("GET", client.calendar_path(calendar_id))
        except (LookupError, PermissionError, RuntimeError, ValueError) as error:
            await self._error(ctx, error)
            return
        await self.config.guild(ctx.guild).calendar_id.set(calendar_id)
        await ctx.send("Google Calendar connected. Events created here will be stored there.")

    @calendarset.command(name="channel")
    async def calendarset_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the announcement channel; omit to clear."""
        if await self._manager(ctx):
            await self.config.guild(ctx.guild).channel_id.set(channel.id if channel else None)
            await ctx.send(f"Announcements will go to {channel.mention}." if channel else "Announcement channel cleared.")

    @calendarset.command(name="managerrole")
    async def calendarset_managerrole(self, ctx, role: discord.Role = None):
        """Set the manager role; omit to clear."""
        if await self._manager(ctx):
            await self.config.guild(ctx.guild).manager_role_id.set(role.id if role else None)
            await ctx.send(f"{role.mention} can manage events." if role else "Only Manage Server can manage events.")

    @calendarset.command(name="timezone")
    async def calendarset_timezone(self, ctx, *, timezone_name: str):
        """Set an IANA timezone such as America/New_York."""
        if not await self._manager(ctx):
            return
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            await ctx.send("That is not a recognized IANA timezone, such as America/New_York.")
            return
        await self.config.guild(ctx.guild).timezone.set(timezone_name)
        await ctx.send(f"Calendar timezone set to **{timezone_name}**.")

    @calendarset.command(name="ics")
    async def calendarset_ics(self, ctx, *, subscription_url: str = None):
        """Store an ICS subscription URL; omit to clear."""
        if not await self._manager(ctx):
            return
        if subscription_url and not subscription_url.startswith("https://"):
            await ctx.send("The subscription URL must use HTTPS.")
            return
        await self.config.guild(ctx.guild).ics_url.set(subscription_url)
        await ctx.send("ICS subscription URL saved." if subscription_url else "ICS subscription URL cleared.")


    @calendarset.command(name="reminderchannel")
    async def calendarset_reminderchannel(self, ctx, channel: discord.TextChannel = None):
        """Set the repeated reminder channel; omit to disable reminders."""
        if not await self._manager(ctx):
            return
        await self.config.guild(ctx.guild).reminder_channel_id.set(channel.id if channel else None)
        await ctx.send(f"Event reminders will post in {channel.mention}." if channel else "Event reminders disabled.")

    @calendarset.command(name="reminderrole")
    async def calendarset_reminderrole(self, ctx, role: discord.Role = None):
        """Set an optional role mention for event reminders; omit to clear."""
        if not await self._manager(ctx):
            return
        await self.config.guild(ctx.guild).reminder_role_id.set(role.id if role else None)
        await ctx.send(f"Event reminders will mention {role.mention}." if role else "Reminder role cleared.")

    @calendarset.command(name="reminders")
    async def calendarset_reminders(self, ctx, *minutes: int):
        """Set 1–8 reminder offsets in minutes, such as 1440 720 360 60."""
        if not await self._manager(ctx):
            return
        try:
            offsets = self.normalize_offsets(minutes)
        except (TypeError, ValueError) as error:
            await ctx.send(str(error))
            return
        await self.config.guild(ctx.guild).reminder_offsets.set(offsets)
        await self.config.guild(ctx.guild).reminder_state.clear()
        await ctx.send("Reminder offsets saved: " + ", ".join(f"{value} minutes" for value in offsets))
