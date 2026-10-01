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
    "creation_cooldown": 300, "next_ticket_number": 1, "tickets": {},
}


class Tickets(commands.Cog):
    """Private, server-owned support ticket channels."""

    __author__ = "SickProdigy"
    __version__ = "0.1.0"

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
            description="Configure private text-channel tickets, then publish the launcher.",
            color=await self.bot.get_embed_color(launcher or guild.me),
        )
        embed.add_field(name="Launcher", value=launcher.mention if launcher else "Not set")
        embed.add_field(name="Category", value=category.name if category else "Not set")
        embed.add_field(name="Staff roles", value=", ".join(r.mention for r in roles) or "Not set", inline=False)
        embed.add_field(name="Staff log", value=log.mention if log else "Disabled")
        embed.add_field(name="Open limit", value=str(data["max_open_per_user"]))
        embed.add_field(name="Cooldown", value=f"{data['creation_cooldown']} seconds")
        return embed

    async def _configured_parts(self, guild):
        data = await self.config.guild(guild).all()
        launcher = guild.get_channel(data["launcher_channel_id"])
        category = guild.get_channel(data["category_id"])
        roles = [guild.get_role(value) for value in data["staff_role_ids"]]
        roles = [r for r in roles if r and r != guild.default_role and not r.managed]
        if not isinstance(launcher, discord.TextChannel):
            raise TicketError("Choose a valid launcher text channel first.")
        if not isinstance(category, discord.CategoryChannel):
            raise TicketError("Choose a valid ticket category first.")
        if not roles:
            raise TicketError("Choose at least one valid support staff role first.")
        me = guild.me
        category_perms = category.permissions_for(me)
        launcher_perms = launcher.permissions_for(me)
        if not (category_perms.view_channel and category_perms.manage_channels):
            raise TicketError("I need View Channel and Manage Channels in the ticket category.")
        if not (launcher_perms.view_channel and launcher_perms.send_messages and launcher_perms.embed_links):
            raise TicketError("I need View Channel, Send Messages, and Embed Links in the launcher channel.")
        return data, launcher, category, roles

    async def publish_launcher(self, guild):
        data, launcher, _, _ = await self._configured_parts(guild)
        embed = discord.Embed(
            title="Need help?",
            description="Press **Open ticket** to create a private support channel visible only to you and support staff.",
            color=await self.bot.get_embed_color(launcher),
        )
        old_channel = guild.get_channel(data["launcher_message_channel_id"])
        if data["launcher_message_id"] and isinstance(old_channel, discord.TextChannel):
            try:
                old_message = await old_channel.fetch_message(data["launcher_message_id"])
                if old_channel == launcher:
                    await old_message.edit(embed=embed, view=LauncherView(self))
                    return old_message
                await old_message.edit(
                    content=f"The ticket launcher moved to {launcher.mention}.",
                    embed=None,
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.NotFound, discord.Forbidden):
                pass
        message = await launcher.send(embed=embed, view=LauncherView(self))
        await self.config.guild(guild).launcher_message_id.set(message.id)
        await self.config.guild(guild).launcher_message_channel_id.set(launcher.id)
        return message

    @staticmethod
    def verify_ticket_permissions(channel, owner, roles):
        everyone = channel.permissions_for(channel.guild.default_role)
        requester = channel.permissions_for(owner)
        bot = channel.permissions_for(channel.guild.me)
        return (
            not everyone.view_channel
            and requester.view_channel and requester.send_messages and requester.read_message_history
            and bot.view_channel and bot.send_messages and bot.manage_channels
            and all(
                (perms := channel.permissions_for(role)).view_channel
                and perms.send_messages and perms.read_message_history
                for role in roles
            )
        )

    async def create_ticket(self, interaction, *, topic, subject, description):
        guild, owner = interaction.guild, interaction.user
        if guild is None or not isinstance(owner, discord.Member):
            raise TicketError("Tickets can only be opened from a server.")
        async with self._locks[guild.id]:
            data, _, category, roles = await self._configured_parts(guild)
            records = [r for value in data["tickets"].values() if (r := normalized_record(value))]
            active = [r for r in records if is_open(r)]
            own = [r for r in active if r["owner_id"] == owner.id]
            if len(own) >= data["max_open_per_user"]:
                mentions = ", ".join(c.mention for r in own if (c := guild.get_channel(r["channel_id"])))
                raise TicketError(f"You already have the maximum number of open tickets.{f' {mentions}' if mentions else ''}")
            if len(active) >= MAX_ACTIVE_TICKETS:
                raise TicketError("This server has reached its active ticket limit. Please contact staff.")
            now = int(time.time())
            remaining = data["creation_cooldown"] - (now - await self.config.member(owner).last_created_at())
            if remaining > 0:
                raise TicketError(f"Please wait {remaining} more seconds before opening another ticket.")

            number = max(1, int(data["next_ticket_number"]))
            allow = dict(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True)
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                owner: discord.PermissionOverwrite(**allow),
                guild.me: discord.PermissionOverwrite(**allow, manage_channels=True, manage_messages=True),
            }
            for role in roles:
                overwrites[role] = discord.PermissionOverwrite(**allow, manage_messages=True)
            channel = None
            try:
                channel = await guild.create_text_channel(
                    ticket_channel_name(number), category=category, overwrites=overwrites,
                    reason=f"Support ticket #{number} opened by user {owner.id}",
                )
                if not self.verify_ticket_permissions(channel, owner, roles):
                    raise TicketError("The private channel permission check failed; no ticket was opened.")
                embed = discord.Embed(
                    title=f"Ticket #{number}: {safe_display(subject)}",
                    description=safe_display(description, 1800),
                    color=await self.bot.get_embed_color(channel),
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Topic", value=safe_display(topic, 50), inline=False)
                embed.add_field(name="Requester", value=owner.mention)
                embed.add_field(name="Status", value="Open")
                embed.add_field(name="Claimed by", value="Nobody")
                embed.set_footer(text="Use the controls below to manage this ticket.")
                record = new_ticket_record(number, channel.id, owner.id)
                control = await channel.send(
                    content=owner.mention, embed=embed,
                    view=TicketControls(self, channel.id, record),
                    allowed_mentions=discord.AllowedMentions(users=[owner], roles=False, everyone=False),
                )
                record["control_message_id"] = control.id
                await self._save_ticket(guild, record)
                await self.config.guild(guild).next_ticket_number.set(number + 1)
                await self.config.member(owner).last_created_at.set(now)
            except TicketError:
                if channel:
                    try:
                        await channel.delete(reason="Incomplete ticket failed privacy verification")
                    except discord.HTTPException:
                        pass
                raise
            except discord.HTTPException as error:
                if channel:
                    try:
                        await channel.delete(reason="Incomplete ticket creation failed")
                    except discord.HTTPException:
                        pass
                raise TicketError(f"Discord could not create the ticket: {error}") from error
        await self.log_event(guild, "opened", record, owner)
        return channel, number

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
        channel = guild.get_channel(int(channel_id))
        if not record or not isinstance(channel, discord.TextChannel) or not record["control_message_id"]:
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
            channel, owner = guild.get_channel(int(channel_id)), guild.get_member(record["owner_id"])
            if not isinstance(channel, discord.TextChannel):
                raise TicketError("The ticket channel no longer exists.")
            if owner:
                overwrite = channel.overwrites_for(owner)
                overwrite.send_messages = not closed
                try:
                    await channel.set_permissions(owner, overwrite=overwrite, reason=f"Ticket changed by user {actor.id}")
                except discord.HTTPException as error:
                    raise TicketError("I could not update the requester channel permissions.") from error
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
        if not isinstance(channel, discord.TextChannel):
            return
        record = await self.get_ticket(channel.guild, channel.id)
        if record:
            record.update(status="deleted", updated_at=int(time.time()), control_message_id=0)
            await self._save_ticket(channel.guild, record)
            await self.log_event(channel.guild, "channel deleted", record)

    @commands.hybrid_group(name="tickets", invoke_without_command=True)
    @commands.guild_only()
    async def tickets(self, ctx):
        """View your open tickets."""
        records = [
            r for value in (await self.config.guild(ctx.guild).tickets()).values()
            if (r := normalized_record(value)) and is_open(r) and r["owner_id"] == ctx.author.id
        ]
        links = [f"Ticket #{r['number']}: {c.mention}" for r in records if (c := ctx.guild.get_channel(r["channel_id"]))]
        await ctx.send("\n".join(links) if links else "You do not have an open ticket.")

    @tickets.command(name="open")
    async def tickets_open(self, ctx):
        """Show a temporary ticket launcher."""
        await ctx.send("Press the button to open a private support ticket.", view=LauncherView(self), delete_after=300)

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
            for r in records[:25] if (c := ctx.guild.get_channel(r["channel_id"]))
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
