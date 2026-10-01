"""Private support ticket channels for Red-DiscordBot."""

import asyncio
import time
from collections import defaultdict

import discord
from redbot.core import Config, commands

from .models import OPEN_STATUSES, is_open, new_ticket_record, normalized_record, safe_display, ticket_channel_name
from .views import LauncherView, SetupView, TicketControls, TicketError

CONFIG_ID = 7422161104
MAX_TRACKED_TICKETS = 500
MAX_ACTIVE_TICKETS = 100

GUILD_DEFAULTS = {
    "launcher_channel_id": 0, "launcher_message_id": 0, "launcher_message_channel_id": 0, "category_id": 0,
    "staff_role_ids": [], "log_channel_id": 0, "max_open_per_user": 1,
    "creation_cooldown": 300, "enabled_modes": ["text"],
    "launcher_title": "Tickets",
    "launcher_message": "To create a ticket, click a button below.",
    "welcome_message": "Thanks for contacting support. Please describe what you need help with below. A support team member will be with you shortly.",
    "next_ticket_number": 1, "tickets": {},
}


class Tickets(commands.Cog):
    """Private, server-owned support ticket channels."""

    __author__ = "SickProdigy"
    __version__ = "0.2.1"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_ID, force_registration=True)
        self.config.register_guild(**GUILD_DEFAULTS)
        self.config.register_member(last_created_at=0)
        self._locks = defaultdict(asyncio.Lock)

    async def cog_load(self):
        self.bot.add_view(LauncherView(self))
        for data in (await self.config.all_guilds()).values():
            for key, value in data.get("tickets", {}).items():
                record = normalized_record(value)
                if record and record["status"] != "deleted":
                    try:
                        self.bot.add_view(
                            TicketControls(self, int(key), record),
                            message_id=record["control_message_id"] or None,
                        )
                    except (TypeError, ValueError):
                        continue

    @staticmethod
    def status_label(status):
        return {
            "open": "Open", "waiting_member": "Waiting on member",
            "waiting_staff": "Waiting on staff", "closed": "Closed", "deleted": "Deleted",
        }.get(status, "Open")

    async def is_staff(self, member):
        if not isinstance(member, discord.Member):
            return False
        if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
            return True
        role_ids = set(await self.config.guild(member.guild).staff_role_ids())
        return any(role.id in role_ids for role in member.roles)

    @staticmethod
    def _get_destination(guild, channel_id):
        if hasattr(guild, "get_channel_or_thread"):
            return guild.get_channel_or_thread(int(channel_id))
        return guild.get_channel(int(channel_id))

    async def get_ticket(self, guild, channel_id):
        value = (await self.config.guild(guild).tickets()).get(str(int(channel_id)))
        return normalized_record(value)

    async def _save_ticket(self, guild, record):
        key = str(record["channel_id"])
        async with self.config.guild(guild).tickets() as tickets:
            tickets[key] = record
            if len(tickets) > MAX_TRACKED_TICKETS:
                candidates = sorted(
                    ((str(k), normalized_record(v)) for k, v in tickets.items()),
                    key=lambda pair: (pair[1] or {}).get("updated_at", 0),
                )
                for old_key, old in candidates:
                    if len(tickets) <= MAX_TRACKED_TICKETS:
                        break
                    if old is None or old["status"] == "deleted":
                        tickets.pop(old_key, None)

    async def settings_embed(self, guild):
        data = await self.config.guild(guild).all()
        launcher = guild.get_channel(data["launcher_channel_id"])
        category = guild.get_channel(data["category_id"])
        log = guild.get_channel(data["log_channel_id"])
        roles = [guild.get_role(value) for value in data["staff_role_ids"]]
        roles = [role for role in roles if role]
        embed = discord.Embed(
            title="Tickets setup",
            description="Configure private text, voice, or boosted-server thread tickets, then publish the launcher.",
            color=await self.bot.get_embed_color(launcher or guild.me),
        )
        embed.add_field(name="Launcher", value=launcher.mention if launcher else "Not set")
        embed.add_field(name="Category", value=category.name if category else "Not set")
        embed.add_field(name="Staff roles", value=", ".join(r.mention for r in roles) or "Not set", inline=False)
        embed.add_field(name="Staff log", value=log.mention if log else "Disabled")
        embed.add_field(name="Open limit", value=str(data["max_open_per_user"]))
        embed.add_field(name="Cooldown", value=f"{data['creation_cooldown']} seconds")
        embed.add_field(name="Ticket modes", value=", ".join(data["enabled_modes"]))
        return embed

    async def _configured_parts(self, guild):
        data = await self.config.guild(guild).all()
        launcher = guild.get_channel(data["launcher_channel_id"])
        category = guild.get_channel(data["category_id"])
        roles = [guild.get_role(value) for value in data["staff_role_ids"]]
        roles = [role for role in roles if role and role != guild.default_role and not role.managed]
        if not isinstance(launcher, discord.TextChannel):
            raise TicketError("Choose a valid launcher text channel first.")
        if data["category_id"] and not isinstance(category, discord.CategoryChannel):
            raise TicketError("The configured ticket category no longer exists.")
        if not roles:
            raise TicketError("Choose at least one valid support staff role first.")
        launcher_perms = launcher.permissions_for(guild.me)
        if not (
            launcher_perms.view_channel
            and launcher_perms.send_messages
            and launcher_perms.embed_links
        ):
            raise TicketError(
                "I need View Channel, Send Messages, and Embed Links in the launcher channel."
            )
        return data, launcher, category, roles

    async def _validate_mode(self, guild, mode, launcher, category, roles):
        if mode not in {"text", "voice", "thread"}:
            raise TicketError("That ticket mode is not supported.")
        data = await self.config.guild(guild).all()
        if mode not in data["enabled_modes"]:
            raise TicketError("That ticket type is not enabled on this server.")
        if mode in {"text", "voice"}:
            permissions = (
                category.permissions_for(guild.me)
                if category
                else guild.me.guild_permissions
            )
            if not permissions.manage_channels:
                raise TicketError("I need Manage Channels where tickets will be created.")
            return
        if guild.premium_tier < 2:
            raise TicketError("Private thread tickets require a Level 2 boosted server.")
        bot_permissions = launcher.permissions_for(guild.me)
        if not (
            bot_permissions.create_private_threads
            and bot_permissions.manage_threads
            and bot_permissions.send_messages_in_threads
        ):
            raise TicketError(
                "I need Create Private Threads, Manage Threads, and Send Messages in Threads "
                "in the launcher channel."
            )
        missing = [
            role.mention
            for role in roles
            if not (
                launcher.permissions_for(role).manage_threads
                and launcher.permissions_for(role).send_messages_in_threads
            )
        ]
        if missing:
            raise TicketError(
                "Support roles need Manage Threads and Send Messages in Threads in the "
                f"launcher channel: {', '.join(missing)}"
            )

    async def publish_launcher(self, guild):
        data, launcher, category, roles = await self._configured_parts(guild)
        enabled_modes = data["enabled_modes"]
        for mode in enabled_modes:
            await self._validate_mode(guild, mode, launcher, category, roles)
        embed = discord.Embed(
            title=safe_display(data["launcher_title"], 100),
            description=safe_display(data["launcher_message"], 1000),
            color=await self.bot.get_embed_color(launcher),
        )
        old_channel = guild.get_channel(data["launcher_message_channel_id"])
        view = LauncherView(self, enabled_modes)
        if data["launcher_message_id"] and isinstance(old_channel, discord.TextChannel):
            try:
                old_message = await old_channel.fetch_message(data["launcher_message_id"])
                if old_channel == launcher:
                    await old_message.edit(embed=embed, view=view)
                    return old_message
                await old_message.edit(
                    content=f"The ticket launcher moved to {launcher.mention}.",
                    embed=None,
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.NotFound, discord.Forbidden):
                pass
        message = await launcher.send(
            embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none()
        )
        await self.config.guild(guild).launcher_message_id.set(message.id)
        await self.config.guild(guild).launcher_message_channel_id.set(launcher.id)
        return message

    @staticmethod
    def _ticket_overwrites(guild, owner, roles, *, voice=False):
        common = {
            "view_channel": True,
            "read_message_history": True,
            "send_messages": True,
            "embed_links": True,
            "attach_files": True,
        }
        if voice:
            common.update(connect=True, speak=True)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False, connect=False if voice else None
            ),
            owner: discord.PermissionOverwrite(**common),
            guild.me: discord.PermissionOverwrite(
                **common, manage_channels=True, manage_messages=True
            ),
        }
        for role in roles:
            overwrites[role] = discord.PermissionOverwrite(
                **common, manage_messages=True
            )
        return overwrites

    async def _create_destination(self, guild, owner, number, mode, launcher, category, roles):
        reason = f"Support {mode} ticket #{number} opened by user {owner.id}"
        if mode == "text":
            return await guild.create_text_channel(
                ticket_channel_name(number),
                category=category,
                overwrites=self._ticket_overwrites(guild, owner, roles),
                reason=reason,
            )
        if mode == "voice":
            return await guild.create_voice_channel(
                ticket_channel_name(number),
                category=category,
                overwrites=self._ticket_overwrites(guild, owner, roles, voice=True),
                reason=reason,
            )
        thread = await launcher.create_thread(
            name=ticket_channel_name(number),
            type=discord.ChannelType.private_thread,
            invitable=False,
            auto_archive_duration=1440,
            reason=reason,
        )
        await thread.add_user(owner)
        return thread

    @staticmethod
    def verify_ticket_permissions(destination, owner, roles, mode):
        if mode == "thread":
            return (
                isinstance(destination, discord.Thread)
                and destination.type == discord.ChannelType.private_thread
                and not destination.invitable
            )
        everyone = destination.permissions_for(destination.guild.default_role)
        requester = destination.permissions_for(owner)
        bot = destination.permissions_for(destination.guild.me)
        valid = (
            not everyone.view_channel
            and requester.view_channel
            and requester.send_messages
            and requester.read_message_history
            and bot.view_channel
            and bot.send_messages
            and bot.manage_channels
            and all(
                (permissions := destination.permissions_for(role)).view_channel
                and permissions.send_messages
                and permissions.read_message_history
                for role in roles
            )
        )
        if mode == "voice":
            valid = (
                valid
                and not everyone.connect
                and requester.connect
                and bot.connect
                and all(destination.permissions_for(role).connect for role in roles)
            )
        return valid

    async def create_ticket(self, interaction, *, mode):
        guild, owner = interaction.guild, interaction.user
        if guild is None or not isinstance(owner, discord.Member):
            raise TicketError("Tickets can only be opened from a server.")
        async with self._locks[guild.id]:
            data, launcher, category, roles = await self._configured_parts(guild)
            await self._validate_mode(guild, mode, launcher, category, roles)
            records = [
                record
                for value in data["tickets"].values()
                if (record := normalized_record(value))
            ]
            active = [record for record in records if is_open(record)]
            own = [record for record in active if record["owner_id"] == owner.id]
            if len(own) >= data["max_open_per_user"]:
                mentions = ", ".join(
                    destination.mention
                    for record in own
                    if (destination := self._get_destination(guild, record["channel_id"]))
                )
                suffix = f" {mentions}" if mentions else ""
                raise TicketError(
                    f"You already have the maximum number of open tickets.{suffix}"
                )
            if len(active) >= MAX_ACTIVE_TICKETS:
                raise TicketError(
                    "This server has reached its active ticket limit. Please contact staff."
                )
            now = int(time.time())
            last_created = await self.config.member(owner).last_created_at()
            remaining = data["creation_cooldown"] - (now - last_created)
            if remaining > 0:
                raise TicketError(
                    f"Please wait {remaining} more seconds before opening another ticket."
                )

            number = max(1, int(data["next_ticket_number"]))
            destination = None
            try:
                destination = await self._create_destination(
                    guild, owner, number, mode, launcher, category, roles
                )
                if not self.verify_ticket_permissions(destination, owner, roles, mode):
                    raise TicketError(
                        "The private ticket permission check failed; no ticket was opened."
                    )
                embed = discord.Embed(
                    title=f"Support ticket #{number}",
                    description=safe_display(data["welcome_message"], 1800),
                    color=await self.bot.get_embed_color(destination),
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Requester", value=owner.mention)
                embed.add_field(name="Type", value=mode.replace("_", " ").title())
                embed.add_field(name="Status", value="Open")
                embed.add_field(name="Claimed by", value="Nobody")
                embed.set_footer(text="Use the controls below to manage this ticket.")
                record = new_ticket_record(
                    number, destination.id, owner.id, mode=mode
                )
                control = await destination.send(
                    content=owner.mention,
                    embed=embed,
                    view=TicketControls(self, destination.id, record),
                    allowed_mentions=discord.AllowedMentions(
                        users=[owner], roles=False, everyone=False
                    ),
                )
                record["control_message_id"] = control.id
                await self._save_ticket(guild, record)
                await self.config.guild(guild).next_ticket_number.set(number + 1)
                await self.config.member(owner).last_created_at.set(now)
            except TicketError:
                if destination:
                    try:
                        await destination.delete(
                            reason="Incomplete ticket failed privacy verification"
                        )
                    except discord.HTTPException:
                        pass
                raise
            except discord.HTTPException as error:
                if destination:
                    try:
                        await destination.delete(reason="Incomplete ticket creation failed")
                    except discord.HTTPException:
                        pass
                raise TicketError(f"Discord could not create the ticket: {error}") from error
        await self.log_event(guild, f"opened ({mode})", record, owner)
        return destination, number

    def updated_control_embed(self, message, record):
        embed = discord.Embed.from_dict(message.embeds[0].to_dict()) if message.embeds else discord.Embed(title=f"Ticket #{record['number']}")
        values = {
            "Status": self.status_label(record["status"]),
            "Claimed by": f"<@{record['claimed_by_id']}>" if record["claimed_by_id"] else "Nobody",
        }
        found = set()
        for index, field in enumerate(embed.fields):
            if field.name in values:
                embed.set_field_at(index, name=field.name, value=values[field.name], inline=field.inline)
                found.add(field.name)
        for name, value in values.items():
            if name not in found:
                embed.add_field(name=name, value=value)
        return embed

    async def refresh_control_message(self, guild, channel_id, record=None):
        record = record or await self.get_ticket(guild, channel_id)
        channel = self._get_destination(guild, channel_id)
        if not record or not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.Thread)) or not record["control_message_id"]:
            return
        try:
            message = await channel.fetch_message(record["control_message_id"])
            await message.edit(embed=self.updated_control_embed(message, record), view=TicketControls(self, channel_id, record))
        except (discord.NotFound, discord.Forbidden):
            pass

    async def toggle_claim(self, guild, channel_id, actor):
        async with self._locks[guild.id]:
            record = await self.get_ticket(guild, channel_id)
            if not record or record["status"] not in OPEN_STATUSES:
                raise TicketError("Only an open ticket can be claimed.")
            claimed = record["claimed_by_id"]
            if claimed and claimed != actor.id:
                raise TicketError(f"This ticket is already claimed by <@{claimed}>.")
            record["claimed_by_id"] = 0 if claimed else actor.id
            record["updated_at"] = int(time.time())
            await self._save_ticket(guild, record)
        await self.log_event(guild, "unclaimed" if claimed else "claimed", record, actor)
        return record

    async def set_ticket_status(self, guild, channel_id, status, actor):
        if status not in OPEN_STATUSES:
            raise TicketError("That is not a valid open ticket status.")
        async with self._locks[guild.id]:
            record = await self.get_ticket(guild, channel_id)
            if not record or record["status"] in {"closed", "deleted"}:
                raise TicketError("Closed or deleted tickets cannot change status.")
            record["status"], record["updated_at"] = status, int(time.time())
            await self._save_ticket(guild, record)
        await self.log_event(guild, f"status: {self.status_label(status)}", record, actor)
        return record

    async def set_closed(self, guild, channel_id, actor, *, closed):
        async with self._locks[guild.id]:
            record = await self.get_ticket(guild, channel_id)
            if not record or record["status"] == "deleted":
                raise TicketError("This ticket is no longer registered.")
            staff = await self.is_staff(actor)
            if actor.id != record["owner_id"] and not staff:
                raise TicketError("Only the requester or support staff can close this ticket.")
            if not closed and not staff:
                raise TicketError("Only support staff can reopen a closed ticket.")
            if closed == (record["status"] == "closed"):
                return record
            channel = self._get_destination(guild, channel_id)
            owner = guild.get_member(record["owner_id"])
            if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.Thread)):
                raise TicketError("The ticket destination no longer exists.")
            try:
                if isinstance(channel, discord.Thread):
                    await channel.edit(
                        archived=closed,
                        locked=closed,
                        reason=f"Ticket changed by user {actor.id}",
                    )
                elif owner:
                    overwrite = channel.overwrites_for(owner)
                    overwrite.send_messages = not closed
                    if isinstance(channel, discord.VoiceChannel):
                        overwrite.connect = not closed
                    await channel.set_permissions(
                        owner,
                        overwrite=overwrite,
                        reason=f"Ticket changed by user {actor.id}",
                    )
            except discord.HTTPException as error:
                raise TicketError("I could not update the ticket permissions.") from error
            now = int(time.time())
            record.update(
                status="closed" if closed else "open", updated_at=now,
                closed_at=now if closed else 0, closed_by_id=actor.id if closed else 0,
            )
            await self._save_ticket(guild, record)
        await self.log_event(guild, "closed" if closed else "reopened", record, actor)
        return record

    async def log_event(self, guild, action, record, actor=None):
        channel = guild.get_channel(await self.config.guild(guild).log_channel_id())
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(
                f"**Ticket #{record['number']}** {safe_display(action, 80)} · <#{record['channel_id']}> · {f'<@{actor.id}>' if actor else 'System'}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            return
        record = await self.get_ticket(channel.guild, channel.id)
        if record:
            record.update(status="deleted", updated_at=int(time.time()), control_message_id=0)
            await self._save_ticket(channel.guild, record)
            await self.log_event(channel.guild, "channel deleted", record)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread):
        record = await self.get_ticket(thread.guild, thread.id)
        if record:
            record.update(
                status="deleted",
                updated_at=int(time.time()),
                control_message_id=0,
            )
            await self._save_ticket(thread.guild, record)
            await self.log_event(thread.guild, "thread deleted", record)

    @commands.hybrid_group(name="tickets", invoke_without_command=True)
    @commands.guild_only()
    async def tickets(self, ctx):
        """View your open tickets."""
        records = [
            r for value in (await self.config.guild(ctx.guild).tickets()).values()
            if (r := normalized_record(value)) and is_open(r) and r["owner_id"] == ctx.author.id
        ]
        links = [f"Ticket #{r['number']}: {c.mention}" for r in records if (c := self._get_destination(ctx.guild, r["channel_id"]))]
        await ctx.send("\n".join(links) if links else "You do not have an open ticket.")

    @tickets.command(name="open")
    async def tickets_open(self, ctx):
        """Show a temporary ticket launcher."""
        modes = await self.config.guild(ctx.guild).enabled_modes()
        await ctx.send(
            "Choose a private support ticket type.",
            view=LauncherView(self, modes),
            delete_after=300,
        )

    @tickets.command(name="queue")
    async def tickets_queue(self, ctx):
        """Show the active support queue (staff only)."""
        if not await self.is_staff(ctx.author):
            raise commands.CheckFailure("Only support staff can view the ticket queue.")
        records = [
            r for value in (await self.config.guild(ctx.guild).tickets()).values()
            if (r := normalized_record(value)) and is_open(r)
        ]
        records.sort(key=lambda item: item["created_at"])
        lines = [
            f"#{r['number']} · {c.mention} · {self.status_label(r['status'])}"
            for r in records[:25] if (c := self._get_destination(ctx.guild, r["channel_id"]))
        ]
        extra = max(0, len(records) - len(lines))
        await ctx.send(("\n".join(lines) + (f"\n…and {extra} more." if extra else "")) if lines else "The ticket queue is empty.")

    @commands.group(name="ticketsset", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def ticketsset(self, ctx):
        """Configure the private ticket system."""
        await ctx.send(embed=await self.settings_embed(ctx.guild), view=SetupView(self, ctx.author))

    @ticketsset.command(name="channel")
    async def set_channel(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).launcher_channel_id.set(channel.id)
        await ctx.tick()

    @ticketsset.command(name="category")
    async def set_category(self, ctx, category: discord.CategoryChannel):
        await self.config.guild(ctx.guild).category_id.set(category.id)
        await ctx.tick()

    @ticketsset.command(name="clearcategory")
    async def clear_category(self, ctx):
        """Create text and voice tickets at the top of the channel list."""
        await self.config.guild(ctx.guild).category_id.set(0)
        await ctx.tick()

    @ticketsset.command(name="staff")
    async def set_staff(self, ctx, role: discord.Role):
        """Toggle a support staff role."""
        if role == ctx.guild.default_role or role.managed:
            await ctx.send("Choose an ordinary server role.")
            return
        async with self.config.guild(ctx.guild).staff_role_ids() as values:
            if role.id in values:
                values.remove(role.id)
                action = "removed from"
            else:
                values.append(role.id)
                action = "added to"
        await ctx.send(f"{role.mention} {action} support staff roles.")

    @ticketsset.command(name="log")
    async def set_log(self, ctx, channel: discord.TextChannel = None):
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id if channel else 0)
        await ctx.tick()

    @ticketsset.command(name="limit")
    async def set_limit(self, ctx, maximum: commands.Range[int, 1, 5]):
        await self.config.guild(ctx.guild).max_open_per_user.set(maximum)
        await ctx.tick()

    @ticketsset.command(name="cooldown")
    async def set_cooldown(self, ctx, seconds: commands.Range[int, 30, 3600]):
        await self.config.guild(ctx.guild).creation_cooldown.set(seconds)
        await ctx.tick()

    @ticketsset.command(name="modes")
    async def set_modes(self, ctx, *, modes: str):
        """Enable text, voice, and/or private-thread ticket modes."""
        requested = {
            value.strip().lower()
            for value in modes.replace(",", " ").split()
            if value.strip()
        }
        aliases = {"private_thread": "thread", "private-thread": "thread"}
        requested = {aliases.get(value, value) for value in requested}
        invalid = requested - {"text", "voice", "thread"}
        if invalid or not requested:
            await ctx.send("Choose one or more of: text, voice, thread.")
            return
        if "thread" in requested and ctx.guild.premium_tier < 2:
            await ctx.send("Private thread tickets require a Level 2 boosted server.")
            return
        await self.config.guild(ctx.guild).enabled_modes.set(
            [mode for mode in ("text", "voice", "thread") if mode in requested]
        )
        await ctx.send(
            "Enabled ticket modes: "
            + ", ".join(mode for mode in ("text", "voice", "thread") if mode in requested)
            + ". Republish the launcher to apply the change."
        )

    @ticketsset.command(name="publish")
    async def set_publish(self, ctx):
        try:
            message = await self.publish_launcher(ctx.guild)
        except TicketError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Ticket launcher is ready: {message.jump_url}")

    async def red_delete_data_for_user(self, *, requester, user_id):
        for guild_id, data in (await self.config.all_guilds()).items():
            await self.config.member_from_ids(guild_id, user_id).clear()
            changed = False
            tickets = data.get("tickets", {})
            for key, value in tickets.items():
                record = normalized_record(value)
                if not record:
                    continue
                for field in ("owner_id", "claimed_by_id", "closed_by_id"):
                    if record[field] == user_id:
                        record[field], changed = 0, True
                tickets[key] = record
            if changed:
                await self.config.guild_from_id(guild_id).tickets.set(tickets)
