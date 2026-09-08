import asyncio
import contextlib
import logging
from copy import deepcopy
from typing import Optional

import discord
from discord.utils import utcnow
from redbot.core import Config, app_commands, commands
from redbot.core.commands.converter import TimedeltaConverter
from redbot.core.utils.chat_formatting import pagify
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu

from .converter import Args, EditArgs
from .menu import GiveawayButton, GiveawayView
from .objects import Giveaway

log = logging.getLogger("red.sick-cogs.Giveaways")
GIVEAWAY_KEY = "giveaways"

# TODO: Add a way to delete giveaways that have ended from the config


class Giveaways(commands.Cog):
    """Giveaway Commands"""

    __version__ = "1.6.1"
    __author__ = ["SickProdigy", "flaree"]

    def format_help_for_context(self, ctx):
        pre_processed = super().format_help_for_context(ctx)
        return f"{pre_processed}\nCog Version: {self.__version__}\nAuthor: {', '.join(self.__author__)}"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=95932766180343808, force_registration=True)
        self.config.init_custom(GIVEAWAY_KEY, 2)
        self.giveaways = {}
        self.entry_locks = {}
        self.giveaway_bgloop = asyncio.create_task(self.init())
        with contextlib.suppress(Exception):
            self.bot.add_dev_env_value("giveaways", lambda x: self)

    @staticmethod
    def _can_create_in(ctx: commands.Context, channel: discord.TextChannel) -> bool:
        author_permissions = channel.permissions_for(ctx.author)
        bot_permissions = channel.permissions_for(ctx.guild.me)
        return (
            author_permissions.view_channel
            and bot_permissions.view_channel
            and bot_permissions.send_messages
            and bot_permissions.embed_links
        )

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        """Remove a user's entrant and winner records from every giveaway."""
        data = await self.config.custom(GIVEAWAY_KEY).all()
        for guild_id, guild_giveaways in data.items():
            for message_id, stored in guild_giveaways.items():
                entrants = stored.get("entrants", [])
                winners = stored.get("winning_users", [])
                cleaned_entrants = [entry for entry in entrants if str(entry) != str(user_id)]
                cleaned_winners = [winner for winner in winners if str(winner) != str(user_id)]
                if cleaned_entrants == entrants and cleaned_winners == winners:
                    continue
                stored["entrants"] = cleaned_entrants
                stored["winning_users"] = cleaned_winners
                await self.config.custom(
                    GIVEAWAY_KEY, guild_id, message_id
                ).set(stored)

        for giveaway in self.giveaways.values():
            giveaway.remove_entrant(user_id)

    async def restore_giveaways(self) -> None:
        """Restore active giveaways and their persistent button views."""
        data = await self.config.custom(GIVEAWAY_KEY).all()
        for guild_giveaways in data.values():
            for message_id, stored in guild_giveaways.items():
                try:
                    if stored.get("ended", False):
                        continue
                    giveaway = Giveaway.from_dict(stored)
                    self.giveaways[int(message_id)] = giveaway
                    view = GiveawayView(self)
                    view.add_item(
                        GiveawayButton(
                            label=giveaway.kwargs.get("button-text", "Join Giveaway"),
                            style=giveaway.kwargs.get("button-style", "green"),
                            emoji=giveaway.emoji,
                            cog=self,
                            update=giveaway.kwargs.get("update_button", False),
                            id=giveaway.messageid,
                        )
                    )
                    self.bot.add_view(view)
                except Exception as exc:
                    log.error(
                        "Error loading giveaway %s", message_id, exc_info=exc
                    )

    async def init(self) -> None:
        await self.bot.wait_until_red_ready()
        await self.restore_giveaways()
        while True:
            try:
                log.debug("Checking giveaways...")
                await self.check_giveaways()
            except Exception as exc:
                log.error("Exception in giveaway loop: ", exc_info=exc)
            await asyncio.sleep(15)

    async def cog_unload(self) -> None:
        for giveaway in self.giveaways.values():
            giveaway_dict = giveaway.to_dict()
            await self.config.custom(GIVEAWAY_KEY, giveaway.guildid, str(giveaway.messageid)).set(
                giveaway_dict
            )
        with contextlib.suppress(Exception):
            self.bot.remove_dev_env_value("giveaways")
        self.giveaway_bgloop.cancel()

    async def check_giveaways(self) -> None:
        to_clear = []
        giveaways = deepcopy(self.giveaways)
        for msgid, giveaway in giveaways.items():
            log.debug(f"Checking giveaway {msgid} with end time {giveaway.endtime}...")
            if giveaway.endtime < utcnow():
                log.debug(f"Drawing winner for giveaway {msgid}")
                completed = await self.draw_winner(giveaway)
                if not completed:
                    continue
                to_clear.append(msgid)
                gw = await self.config.custom(GIVEAWAY_KEY, giveaway.guildid, str(msgid)).all()
                gw["ended"] = True
                await self.config.custom(GIVEAWAY_KEY, giveaway.guildid, str(msgid)).set(gw)
        for msgid in to_clear:
            self.giveaways.pop(msgid, None)
            self.entry_locks.pop(msgid, None)

    async def draw_winner(self, giveaway: Giveaway):
        guild = self.bot.get_guild(giveaway.guildid)
        if guild is None:
            return False
        channel_obj = guild.get_channel(giveaway.channelid)
        if channel_obj is None:
            return False

        valid_members = {
            user_id: guild.get_member(user_id) for user_id in set(giveaway.entrants)
        }
        valid_members = {user_id: member for user_id, member in valid_members.items() if member}
        winners = giveaway.draw_winner(set(valid_members))
        winner_objs = None
        if winners is None:
            txt = "Not enough entries to roll the giveaway."
        else:
            winner_objs = []
            txt = ""
            for winner in winners:
                winner_obj = valid_members.get(winner)
                if winner_obj is None:
                    txt += f"{winner} (Not Found)\n"
                else:
                    txt += f"{winner_obj.mention} ({winner_obj.display_name})\n"
                    winner_objs.append(winner_obj)

        msg = channel_obj.get_partial_message(giveaway.messageid)
        winners = giveaway.kwargs.get("winners", 1) or 1
        embed = discord.Embed(
            title=f"{f'{winners}x ' if winners > 1 else ''}{giveaway.prize}",
            description=f"Winner(s):\n{txt}",
            color=await self.bot.get_embed_color(channel_obj),
            timestamp=utcnow(),
        )
        embed.set_footer(
            text=f"Reroll: {(await self.bot.get_prefix(msg))[-1]}gw reroll {giveaway.messageid} | Ended at"
        )
        try:
            await msg.edit(content="🎉 Giveaway Ended 🎉", embed=embed, view=None)
        except (discord.NotFound, discord.Forbidden) as exc:
            log.error("Error editing giveaway message: ", exc_info=exc)
            self.giveaways.pop(giveaway.messageid, None)
            self.entry_locks.pop(giveaway.messageid, None)
            gw = await self.config.custom(
                GIVEAWAY_KEY, giveaway.guildid, str(giveaway.messageid)
            ).all()
            gw["ended"] = True
            await self.config.custom(GIVEAWAY_KEY, giveaway.guildid, str(giveaway.messageid)).set(
                gw
            )
            return True
        if giveaway.kwargs.get("announce"):
            announce_embed = discord.Embed(
                title="Giveaway Ended",
                description=f"Congratulations to the {f'{str(winners)} ' if winners > 1 else ''}winner{'s' if winners > 1 else ''} of [{giveaway.prize}]({msg.jump_url}).\n{txt}",
                color=await self.bot.get_embed_color(channel_obj),
            )

            announce_embed.set_footer(
                text=f"Reroll: {(await self.bot.get_prefix(msg))[-1]}gw reroll {giveaway.messageid}"
            )
            try:
                await channel_obj.send(
                    content=(
                        "Congratulations " + ",".join([x.mention for x in winner_objs])
                        if winner_objs is not None
                        else ""
                    ),
                    embed=announce_embed,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
            except (discord.NotFound, discord.Forbidden) as exc:
                log.error("Error sending giveaway announcement: ", exc_info=exc)
        if winner_objs is not None:
            if giveaway.kwargs.get("congratulate", False):
                for winner in winner_objs:
                    with contextlib.suppress(discord.Forbidden, discord.HTTPException):
                        await winner.send(
                            f"Congratulations! You won {giveaway.prize} in the giveaway on {guild}!"
                        )
        gw = await self.config.custom(
            GIVEAWAY_KEY, giveaway.guildid, str(giveaway.messageid)
        ).all()
        gw["ended"] = True
        gw["winning_users"] = [x.id for x in winner_objs] if winner_objs is not None else []
        await self.config.custom(GIVEAWAY_KEY, giveaway.guildid, str(giveaway.messageid)).set(gw)
        return True

    @commands.hybrid_group(aliases=["gw", "giveaways"])
    @commands.bot_has_permissions(embed_links=True)
    @commands.has_permissions(manage_guild=True)
    async def giveaway(self, ctx: commands.Context):
        """
        Manage the giveaway system
        """

    @giveaway.command()
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(
        channel="The channel in which to start the giveaway.",
        time="The time the giveaway should last.",
        prize="The prize for the giveaway.",
    )
    async def start(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel],
        time: TimedeltaConverter(default_unit="minutes"),
        *,
        prize: str,
    ):
        """
        Start a giveaway.

        This by default will DM the winner and also DM a user if they cannot enter the giveaway.
        """
        channel = channel or ctx.channel
        if len(prize) > 240:
            return await ctx.send("Prize text must be 240 characters or fewer.")
        if not self._can_create_in(ctx, channel):
            return await ctx.send("You must be able to view that channel, and I need View Channel, Send Messages, and Embed Links there.")
        end = utcnow() + time
        embed = discord.Embed(
            title=f"{prize}",
            description=f"\nClick the button below to enter\n\n**Hosted by:** {ctx.author.mention}\n\nEnds: <t:{int(end.timestamp())}:R>",
            color=await ctx.embed_color(),
        )
        view = GiveawayView(self)

        msg = await channel.send(embed=embed)
        view.add_item(
            GiveawayButton(
                label="Join Giveaway",
                style="green",
                emoji="🎉",
                cog=self,
                id=msg.id,
            )
        )
        self.bot.add_view(view)
        await msg.edit(view=view)
        giveaway_obj = Giveaway(
            ctx.guild.id,
            channel.id,
            msg.id,
            end,
            prize,
            "🎉",
            False,
            **{"congratulate": True, "notify": True},
        )
        if ctx.interaction:
            await ctx.send("Giveaway created!", ephemeral=True)
        self.giveaways[msg.id] = giveaway_obj
        giveaway_dict = giveaway_obj.to_dict()
        await self.config.custom(GIVEAWAY_KEY, str(ctx.guild.id), str(msg.id)).set(giveaway_dict)

    @giveaway.command()
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(msgid="The message ID of the giveaway to end.")
    async def reroll(self, ctx: commands.Context, msgid: int):
        """Reroll a giveaway."""
        data = await self.config.custom(GIVEAWAY_KEY, ctx.guild.id).all()
        if str(msgid) not in data:
            return await ctx.send("Giveaway not found.")
        if data[str(msgid)].get("cancelled", False):
            return await ctx.send("Cancelled giveaways cannot be rerolled.")
        if not data[str(msgid)].get("ended", False):
            return await ctx.send("That giveaway is still active and cannot be rerolled.")
        if msgid in self.giveaways:
            return await ctx.send(
                f"Giveaway already running. Please wait for it to end or end it via `{ctx.clean_prefix}gw end {msgid}`."
            )
        giveaway = Giveaway.from_dict(data[str(msgid)])
        if await self.draw_winner(giveaway):
            await ctx.tick()
        else:
            await ctx.send("The giveaway channel is temporarily unavailable.")

    @giveaway.command()
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(msgid="The message ID of the giveaway to end.")
    async def end(self, ctx: commands.Context, msgid: int):
        """End a giveaway."""
        if msgid in self.giveaways:
            if self.giveaways[msgid].guildid != ctx.guild.id:
                return await ctx.send("Giveaway not found.")
            if not await self.draw_winner(self.giveaways[msgid]):
                return await ctx.send("The giveaway channel is temporarily unavailable.")
            self.giveaways.pop(msgid, None)
            self.entry_locks.pop(msgid, None)
            gw = await self.config.custom(GIVEAWAY_KEY, ctx.guild.id, str(msgid)).all()
            gw["ended"] = True
            await self.config.custom(GIVEAWAY_KEY, ctx.guild.id, str(msgid)).set(gw)
            await ctx.tick()
        else:
            await ctx.send("Giveaway not found.")

    @giveaway.command()
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(msgid="The message ID of the giveaway to cancel.")
    async def cancel(self, ctx: commands.Context, msgid: int):
        """Cancel an active giveaway without drawing a winner."""
        giveaway = self.giveaways.get(msgid)
        if giveaway is None or giveaway.guildid != ctx.guild.id:
            return await ctx.send("Giveaway not found.")

        self.giveaways.pop(msgid, None)
        self.entry_locks.pop(msgid, None)
        giveaway.ended = True
        data = giveaway.to_dict()
        data["cancelled"] = True
        await self.config.custom(GIVEAWAY_KEY, ctx.guild.id, str(msgid)).set(data)

        channel = ctx.guild.get_channel(giveaway.channelid)
        if channel is not None:
            message = channel.get_partial_message(giveaway.messageid)
            with contextlib.suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                await message.edit(content="Giveaway cancelled.", view=None)
        await ctx.tick()

    @giveaway.command(aliases=["adv"])
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(
        arguments="The arguments for the giveaway. See `[p]gw explain` for more info."
    )
    async def advanced(self, ctx: commands.Context, *, arguments: Args):
        """Advanced creation of Giveaways.


        `[p]gw explain` for a further full listing of the arguments.
        """
        prize = arguments["prize"]
        duration = arguments["duration"]
        channel = arguments["channel"] or ctx.channel
        if not self._can_create_in(ctx, channel):
            return await ctx.send("You must be able to view that channel, and I need View Channel, Send Messages, and Embed Links there.")

        winners = arguments.get("winners", 1) or 1
        end = utcnow() + duration
        description = arguments["description"] or ""
        if arguments["show_requirements"]:
            description += "\n\n**Requirements:**\n" + self.generate_settings_text(ctx, arguments)

        emoji = arguments["emoji"] or "🎉"
        if isinstance(emoji, int):
            emoji = self.bot.get_emoji(emoji)
        hosted_by = ctx.guild.get_member(arguments.get("hosted-by", ctx.author.id)) or ctx.author
        embed = discord.Embed(
            title=f"{f'{winners}x ' if winners > 1 else ''}{prize}",
            description=f"{description}\n\nClick the button below to enter\n\n**Hosted by:** {hosted_by.mention}\n\nEnds: <t:{int(end.timestamp())}:R>",
            color=arguments.get("colour", await ctx.embed_color()),
        )
        if arguments["image"] is not None:
            embed.set_image(url=arguments["image"])
        if arguments["thumbnail"] is not None:
            embed.set_thumbnail(url=arguments["thumbnail"])

        requested_everyone = bool(arguments["ateveryone"] or arguments["athere"])
        can_mention_everyone = channel.permissions_for(ctx.author).mention_everyone
        bot_can_mention_everyone = channel.permissions_for(ctx.guild.me).mention_everyone
        if requested_everyone and not can_mention_everyone:
            await ctx.send("You need the Mention Everyone permission to use @everyone or @here.")
            return
        if requested_everyone and not bot_can_mention_everyone:
            await ctx.send("I need the Mention Everyone permission for that giveaway.")
            return
        for role_id in arguments["mentions"] or ():
            role = ctx.guild.get_role(role_id)
            if role is None:
                continue
            if not role.mentionable and not can_mention_everyone:
                await ctx.send(
                    f"You cannot mention {role.name}; make the role mentionable or request "
                    "the Mention Everyone permission."
                )
                return
            if not role.mentionable and not bot_can_mention_everyone:
                await ctx.send(f"I do not have permission to mention {role.name}.")
                return

        txt = "\n"
        if arguments["ateveryone"]:
            txt += "@everyone "
        if arguments["athere"]:
            txt += "@here "
        if arguments["mentions"]:
            for mention in arguments["mentions"]:
                role = ctx.guild.get_role(mention)
                if role is not None:
                    txt += f"{role.mention} "

        view = GiveawayView(self)
        msg = await channel.send(
            content=f"🎉 Giveaway 🎉{txt}",
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                roles=bool(arguments["mentions"]),
                everyone=requested_everyone,
            ),
        )
        view.add_item(
            GiveawayButton(
                label=arguments["button-text"] or "Join Giveaway",
                style=arguments["button-style"] or "green",
                emoji=emoji,
                cog=self,
                update=arguments.get("update_button", False),
                id=msg.id,
            )
        )
        self.bot.add_view(view)
        await msg.edit(view=view)
        if ctx.interaction:
            await ctx.send("Giveaway created!", ephemeral=True)

        giveaway_obj = Giveaway(
            ctx.guild.id,
            channel.id,
            msg.id,
            end,
            prize,
            str(emoji),
            False,
            **{
                k: v
                for k, v in arguments.items()
                if k not in ["prize", "duration", "channel", "emoji"]
            },
        )
        self.giveaways[msg.id] = giveaway_obj
        giveaway_dict = giveaway_obj.to_dict()
        await self.config.custom(GIVEAWAY_KEY, str(ctx.guild.id), str(msg.id)).set(giveaway_dict)

    @giveaway.command()
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(msgid="The message ID of the giveaway to edit.")
    async def entrants(self, ctx: commands.Context, msgid: int):
        """List all entrants for a giveaway."""
        if msgid not in self.giveaways:
            return await ctx.send("Giveaway not found.")
        giveaway = self.giveaways[msgid]
        if not giveaway.entrants:
            return await ctx.send("No entrants.")
        count = {}
        for entrant in giveaway.entrants:
            if entrant not in count:
                count[entrant] = 1
            else:
                count[entrant] += 1
        msg = ""
        for userid, count_int in sorted(count.items(), key=lambda item: item[1], reverse=True):
            user = ctx.guild.get_member(userid)
            msg += f"{user.mention} ({count_int})\n" if user else f"{userid} ({count_int})\n"
        embeds = []
        for page in pagify(msg, delims=["\n"], page_length=800):
            embed = discord.Embed(
                title="Entrants", description=page, color=await ctx.embed_color()
            )
            embed.set_footer(text=f"Total entrants: {len(count)}")
            embeds.append(embed)

        if len(embeds) == 1:
            return await ctx.send(embed=embeds[0])
        return await menu(ctx, embeds, DEFAULT_CONTROLS)

    @giveaway.command()
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(msgid="The message ID of the giveaway to edit.")
    async def info(self, ctx: commands.Context, msgid: int):
        """Information about a giveaway."""
        if msgid not in self.giveaways:
            return await ctx.send("Giveaway not found.")

        giveaway = self.giveaways[msgid]
        winners = giveaway.kwargs.get("winners", 1) or 1
        msg = f"**Entrants:**: {len(giveaway.entrants)}\n**End**: <t:{int(giveaway.endtime.timestamp())}:R>\n"
        for kwarg in giveaway.kwargs:
            if giveaway.kwargs[kwarg]:
                msg += f"**{kwarg.title()}:** {giveaway.kwargs[kwarg]}\n"
        embed = discord.Embed(
            title=f"{f'{winners}x ' if winners > 1 else ''}{giveaway.prize}",
            color=await ctx.embed_color(),
            description=msg,
        )
        embed.set_footer(text=f"Giveaway ID #{msgid}")
        await ctx.send(embed=embed)

    @giveaway.command(name="list")
    @commands.has_permissions(manage_guild=True)
    async def _list(self, ctx: commands.Context):
        """List all giveaways in the server."""
        if not self.giveaways:
            return await ctx.send("No giveaways are running.")
        giveaways = {
            x: self.giveaways[x]
            for x in self.giveaways
            if self.giveaways[x].guildid == ctx.guild.id
        }
        if not giveaways:
            return await ctx.send("No giveaways are running.")
        msg = "".join(
            f"{msgid}: [{giveaways[msgid].prize}](https://discord.com/channels/{value.guildid}/{giveaways[msgid].channelid}/{msgid})\n"
            for msgid, value in giveaways.items()
        )

        embeds = []
        for page in pagify(msg, delims=["\n"]):
            embed = discord.Embed(
                title=f"Giveaways in {ctx.guild}", description=page, color=await ctx.embed_color()
            )
            embeds.append(embed)
        if len(embeds) == 1:
            return await ctx.send(embed=embeds[0])
        return await menu(ctx, embeds, DEFAULT_CONTROLS)

    @giveaway.command()
    @commands.has_permissions(manage_guild=True)
    async def explain(self, ctx: commands.Context):
        """Explanation of giveaway advanced and the arguements it supports."""

        msg = """
        Giveaway advanced creation.
        NOTE: Giveaways are checked every 15 seconds, this means that the giveaway may end up being slightly longer than the specified duration.

        Giveaway advanced contains many different flags that can be used to customize the giveaway.
        The flags are as follows:

        Required arguments:
        `--prize`: The prize to be won.

        Required Mutual Exclusive Arguments:
        You must one ONE of these, but not both:
        `--duration`: The duration of the giveaway. Must be in format such as `2d3h30m`.
        `--end`: The end time of the giveaway. Must be in format such as `2021-12-23T30:00:00.000Z`, `2026-12-23 15:00 UTC`. Defaults to UTC if no timezone is provided.

        Optional arguments:
        `--channel`: The channel to post the giveaway in. Will default to this channel if not specified.
        `--emoji`: The emoji to use for the giveaway.
        `--roles`: Roles that the giveaway will be restricted to. If the role contains a space, use their ID.
        `--multiplier`: Multiplier for those in specified roles. Must be a positive number.
        `--multi-roles`: Roles that will receive the multiplier. If the role contains a space, use their ID.
        `--cost`: Cost of credits to enter the giveaway. Must be a positive number.
        `--joined`: How long the user must be a member of the server for to enter the giveaway. Must be a positive number of days.
        `--created`: How long the user has been on discord for to enter the giveaway. Must be a positive number of days.
        `--blacklist`: Blacklisted roles that cannot enter the giveaway. If the role contains a space, use their ID.
        `--winners`: How many winners to draw. Must be a positive number.
        `--mentions`: Roles to mention in the giveaway notice.
        `--description`: Description of the giveaway.
        `--button-text`: Text to use for the button.
        `--button-style`: Style to use for the button.
        `--image`: Image URL to use for the giveaway embed.
        `--thumbnail`: Thumbnail URL to use for the giveaway embed.
        `--hosted-by`: User of the user hosting the giveaway. Defaults to the author of the command.
        `--colour`: Colour to use for the giveaway embed.
        `--bypass-roles`: Roles that bypass the requirements. If the role contains a space, use their ID.
        `--bypass-type`: Type of bypass to use. Must be one of `or` or `and`. Defaults to `or`.

        Setting Arguments:
        `--congratulate`: Whether or not to congratulate the winner. Not passing will default to off.
        `--notify`: Whether or not to notify a user if they failed to enter the giveaway. Not passing will default to off.
        `--multientry`: Whether or not to allow multiple entries. Not passing will default to off.
        `--announce`: Whether to post a seperate message when the giveaway ends. Not passing will default to off.
        `--ateveryone`: Whether to tag @everyone in the giveaway notice.
        `--show-requirements`: Whether to show the requirements of the giveaway.
        `--athere`: Whether to tag @here in the giveaway notice.
        `--update-button`: Whether to show the unique entrant count on the button.
        `--update-button`: Whether to update the button with the number of entrants.

        Examples:
        `{prefix}gw advanced --prize A new sword --duration 1h30m --restrict Role ID --multiplier 2 --multi-roles RoleID RoleID2`
        `{prefix}gw advanced --prize A better sword --duration 2h3h30m --channel channel-name --cost 250 --joined 50 --congratulate --notify --multientry`""".format(
            prefix=ctx.clean_prefix
        )
        embed = discord.Embed(
            title="Giveaway Advanced Explanation", description=msg, color=await ctx.embed_color()
        )
        await ctx.send(embed=embed)

    @giveaway.command()
    @commands.has_permissions(manage_guild=True)
    async def edit(self, ctx: commands.Context, msgid: int, *, flags: EditArgs):
        """Edit the supported settings of an active giveaway."""
        giveaway = self.giveaways.get(msgid)
        if giveaway is None or giveaway.guildid != ctx.guild.id:
            return await ctx.send("Giveaway not found.")
        if flags.get("channel") is not None:
            return await ctx.send("An active giveaway cannot be moved to another channel.")

        if flags.get("prize"):
            giveaway.prize = flags["prize"]
        if flags.get("duration") is not None:
            giveaway.endtime = utcnow() + flags["duration"]
        if flags.get("emoji") is not None:
            emoji = flags["emoji"]
            if isinstance(emoji, int):
                emoji = self.bot.get_emoji(emoji)
            giveaway.emoji = str(emoji)

        editable_settings = (
            "roles",
            "multi",
            "multi-roles",
            "cost",
            "joined",
            "created",
            "blacklist",
            "winners",
            "mentions",
            "description",
            "button-text",
            "button-style",
            "hosted-by",
            "colour",
            "bypass-roles",
            "bypass-type",
            "multientry",
            "notify",
            "congratulate",
            "announce",
            "ateveryone",
            "athere",
            "show_requirements",
            "update_button",
            "image",
            "thumbnail",
        )
        for setting in editable_settings:
            value = flags.get(setting)
            if value not in (None, False, [], ""):
                giveaway.kwargs[setting] = value

        channel = ctx.guild.get_channel(giveaway.channelid)
        if channel is None:
            return await ctx.send("The giveaway channel is no longer available.")
        hosted_by = (
            ctx.guild.get_member(giveaway.kwargs.get("hosted-by", ctx.author.id)) or ctx.author
        )
        winners = giveaway.kwargs.get("winners", 1) or 1
        description = giveaway.kwargs.get("description") or ""
        if giveaway.kwargs.get("show_requirements"):
            description += "\n\n**Requirements:**\n" + self.generate_settings_text(
                ctx, giveaway.kwargs
            )
        embed = discord.Embed(
            title=f"{f'{winners}x ' if winners > 1 else ''}{giveaway.prize}",
            description=(
                f"{description}\n\nClick the button below to enter\n\n"
                f"**Hosted by:** {hosted_by.mention}\n\n"
                f"Ends: <t:{int(giveaway.endtime.timestamp())}:R>"
            ),
            color=giveaway.kwargs.get("colour") or await ctx.embed_color(),
        )
        if giveaway.kwargs.get("image"):
            embed.set_image(url=giveaway.kwargs["image"])
        if giveaway.kwargs.get("thumbnail"):
            embed.set_thumbnail(url=giveaway.kwargs["thumbnail"])

        view = GiveawayView(self)
        view.add_item(
            GiveawayButton(
                label=giveaway.kwargs.get("button-text", "Join Giveaway"),
                style=giveaway.kwargs.get("button-style", "green"),
                emoji=giveaway.emoji,
                cog=self,
                update=giveaway.kwargs.get("update_button", False),
                id=giveaway.messageid,
            )
        )
        self.bot.add_view(view)
        message = channel.get_partial_message(giveaway.messageid)
        try:
            await message.edit(embed=embed, view=view)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return await ctx.send("I could not update the giveaway message.")

        self.giveaways[msgid] = giveaway
        await self.config.custom(GIVEAWAY_KEY, ctx.guild.id, str(msgid)).set(
            giveaway.to_dict()
        )
        await ctx.tick()

    def generate_settings_text(self, ctx: commands.Context, args):
        def role_mentions(key):
            roles = (ctx.guild.get_role(role_id) for role_id in args.get(key, []))
            return ", ".join(role.mention for role in roles if role is not None)

        lines = []
        for key, label in (
            ("roles", "Roles"),
            ("multi-roles", "Multiplier Roles"),
            ("blacklist", "Blacklist"),
        ):
            mentions = role_mentions(key)
            if mentions:
                lines.append(f"**{label}:** {mentions}")
        if args.get("multi"):
            lines.append(f"**Multiplier:** {args['multi']}")
        if args.get("cost"):
            lines.append(f"**Cost:** {args['cost']}")
        if args.get("joined"):
            lines.append(f"**Joined:** {args['joined']} days")
        if args.get("created"):
            lines.append(f"**Created:** {args['created']} days")
        if args.get("winners"):
            lines.append(f"**Winners:** {args['winners']}")
        bypass_mentions = role_mentions("bypass-roles")
        if bypass_mentions:
            lines.append(
                f"**Bypass Roles:** {bypass_mentions} ({args.get('bypass-type', 'or')})"
            )
        return "\n".join(lines)