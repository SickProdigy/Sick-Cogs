import argparse
import shlex
from datetime import datetime, timezone

from dateutil import parser as date_parser
from discord.ext.commands.converter import (
    ColourConverter,
    EmojiConverter,
    MemberConverter,
    RoleConverter,
    TextChannelConverter,
)
from redbot.core.commands import BadArgument, Converter
from redbot.core.commands.converter import TimedeltaConverter

from .menu import BUTTON_STYLE


class NoExitParser(argparse.ArgumentParser):
    def error(self, message):
        raise BadArgument()


class Args(Converter):
    require_prize = True
    require_time = True

    async def convert(self, ctx, argument):
        argument = argument.replace("—", "--")
        parser = NoExitParser(description="Giveaway Created", add_help=False)

        # Required Arguments

        parser.add_argument("--prize", "--p", dest="prize", nargs="*", default=[])

        timer = parser.add_mutually_exclusive_group()
        timer.add_argument("--duration", "--d", dest="duration", nargs="*", default=[])
        timer.add_argument("--end", "--e", dest="end", nargs="*", default=[])

        # Optional Arguments
        parser.add_argument("--channel", dest="channel", default=None, nargs="?")
        parser.add_argument("--roles", "--r", "--restrict", dest="roles", nargs="*", default=[])
        parser.add_argument("--multiplier", "--m", dest="multi", default=None, type=int, nargs="?")
        parser.add_argument("--multi-roles", "--mr", nargs="*", dest="multi-roles", default=[])
        parser.add_argument("--joined", dest="joined", default=None, type=int, nargs="?")
        parser.add_argument("--created", dest="created", default=None, type=int, nargs="?")
        parser.add_argument("--blacklist", dest="blacklist", nargs="*", default=[])
        parser.add_argument("--winners", dest="winners", default=None, type=int, nargs="?")
        parser.add_argument("--mentions", dest="mentions", nargs="*", default=[])
        parser.add_argument("--description", dest="description", default=[], nargs="*")
        parser.add_argument("--button-text", dest="button-text", default=[], nargs="*")
        parser.add_argument("--button-style", dest="button-style", default=[], nargs="*")
        parser.add_argument("--emoji", dest="emoji", default=None, nargs="*")
        parser.add_argument("--image", dest="image", default=None, nargs="*")
        parser.add_argument("--thumbnail", dest="thumbnail", default=None, nargs="*")
        parser.add_argument("--hosted-by", dest="hosted-by", default=None, nargs="*")
        parser.add_argument("--colour", dest="colour", default=None, nargs="*")
        parser.add_argument("--bypass-roles", nargs="*", dest="bypass-roles", default=[])
        parser.add_argument("--bypass-type", dest="bypass-type", default=None, nargs="?")
        # Setting arguments
        parser.add_argument("--multientry", action="store_true")
        parser.add_argument("--notify", action="store_true")
        parser.add_argument("--congratulate", action="store_true")
        parser.add_argument("--announce", action="store_true")
        parser.add_argument("--ateveryone", action="store_true")
        parser.add_argument("--athere", action="store_true")
        parser.add_argument("--show-requirements", action="store_true")
        parser.add_argument("--update-button", action="store_true")

        # Core entry options
        parser.add_argument("--cost", dest="cost", default=None, type=int, nargs="?")

        try:
            vals = vars(parser.parse_args(shlex.split(argument)))
        except Exception as error:
            raise BadArgument(
                "Could not parse flags correctly, ensure flags are correctly used."
            ) from error

        if self.require_prize and not vals["prize"]:
            raise BadArgument("You must specify a prize. Use `--prize` or `-p`")  #

        if self.require_time and not any([vals["duration"], vals["end"]]):
            raise BadArgument(
                "You must specify a duration or end date. Use `--duration` or `-d` or `--end` or `-e`"
            )

        nums = [vals["cost"], vals["joined"], vals["created"], vals["winners"], vals["multi"]]
        for val in nums:
            if val is None:
                continue
            if val < 1:
                raise BadArgument("Number must be greater than 0")
        if vals["multi"] is not None and vals["multi"] > 100:
            raise BadArgument("Multiplier cannot be greater than 100")

        valid_multi_roles = []
        for role in vals["multi-roles"]:
            try:
                role = await RoleConverter().convert(ctx, role)
                valid_multi_roles.append(role.id)
            except BadArgument:
                raise BadArgument(f"The role {role} does not exist within this server.")
        vals["multi-roles"] = valid_multi_roles

        valid_bypass_roles = []
        for role in vals["bypass-roles"]:
            try:
                role = await RoleConverter().convert(ctx, role)
                valid_bypass_roles.append(role.id)
            except BadArgument:
                raise BadArgument(f"The role {role} does not exist within this server.")
        vals["bypass-roles"] = valid_bypass_roles

        if vals["bypass-type"]:
            if vals["bypass-type"] not in ["or", "and"]:
                raise BadArgument("Bypass type must be either `or` or `and` - default is `or`")
        else:
            vals["bypass-type"] = "or"

        valid_exclusive_roles = []
        for role in vals["roles"]:
            try:
                role = await RoleConverter().convert(ctx, role)
                valid_exclusive_roles.append(role.id)
            except BadArgument:
                raise BadArgument(f"The role {role} does not exist within this server.")
        vals["roles"] = valid_exclusive_roles

        valid_blacklist_roles = []
        for role in vals["blacklist"]:
            try:
                role = await RoleConverter().convert(ctx, role)
                valid_blacklist_roles.append(role.id)
            except BadArgument:
                raise BadArgument(f"The role {role} does not exist within this server.")
        vals["blacklist"] = valid_blacklist_roles

        valid_mentions = []
        for role in vals["mentions"]:
            try:
                role = await RoleConverter().convert(ctx, role)
                valid_mentions.append(role.id)
            except BadArgument:
                raise BadArgument(f"The role {role} does not exist within this server.")
        vals["mentions"] = valid_mentions

        if vals["channel"]:
            try:
                vals["channel"] = await TextChannelConverter().convert(ctx, vals["channel"])
            except BadArgument:
                raise BadArgument("Invalid channel.")

        if (vals["multi"] or vals["multi-roles"]) and not (vals["multi"] and vals["multi-roles"]):
            raise BadArgument(
                "You must specify a multiplier and roles. Use `--multiplier` or `-m` and `--multi-roles` or `-mr`"
            )

        target_channel = vals["channel"] or ctx.channel
        if vals["ateveryone"] or vals["athere"]:
            if not target_channel.permissions_for(ctx.author).mention_everyone:
                raise BadArgument("You need the Mention Everyone permission in the target channel.")
            if not target_channel.permissions_for(ctx.me).mention_everyone:
                raise BadArgument("The bot needs the Mention Everyone permission in the target channel.")

        if vals["description"]:
            vals["description"] = " ".join(vals["description"])
            if len(vals["description"]) > 1000:
                raise BadArgument("Description must be less than 1000 characters.")

        if vals["button-text"]:
            vals["button-text"] = " ".join(vals["button-text"])
            if len(vals["button-text"]) > 70:
                raise BadArgument("Button text must be less than 70 characters.")
        else:
            vals["button-text"] = "Join Giveaway"

        if vals["button-style"]:
            vals["button-style"] = " ".join(vals["button-style"]).lower()
            if vals["button-style"] not in BUTTON_STYLE.keys():
                raise BadArgument(
                    f"Button style must be one of the following: {', '.join(BUTTON_STYLE.keys())}"
                )
        else:
            vals["button-style"] = "green"

        if vals["hosted-by"]:
            vals["hosted-by"] = " ".join(vals["hosted-by"])
            user = await MemberConverter().convert(ctx, vals["hosted-by"])
            if user is None:
                raise BadArgument("Invalid user.")
            vals["hosted-by"] = user.id

        if vals["colour"]:
            vals["colour"] = " ".join(vals["colour"]).lower()
            try:
                vals["colour"] = (await ColourConverter().convert(ctx, vals["colour"])).value
            except Exception:
                raise BadArgument("Invalid colour.")

        if vals["emoji"]:
            vals["emoji"] = " ".join(vals["emoji"]).rstrip().lstrip()
            custom = False
            try:
                vals["emoji"] = await EmojiConverter().convert(ctx, vals["emoji"])
                custom = True
            except Exception:
                vals["emoji"] = str(vals["emoji"]).replace("\N{VARIATION SELECTOR-16}", "")
            try:
                await ctx.message.add_reaction(vals["emoji"])
                await ctx.message.remove_reaction(vals["emoji"], ctx.me)
            except Exception:
                raise BadArgument("Invalid emoji.")
            if custom:
                vals["emoji"] = vals["emoji"].id

        vals["prize"] = " ".join(vals["prize"])
        if len(vals["prize"]) > 240:
            raise BadArgument("Prize text must be 240 characters or fewer.")
        if vals["duration"]:
            tc = TimedeltaConverter()
            try:
                duration = await tc.convert(ctx, " ".join(vals["duration"]))
                vals["duration"] = duration
            except BadArgument:
                raise BadArgument("Invalid duration. Use `--duration` or `-d`")
            else:
                if duration.total_seconds() < 60:
                    raise BadArgument("Duration must be greater than 60 seconds.")
        elif vals["end"]:
            try:
                time = date_parser.parse(" ".join(vals["end"]))
                if time.tzinfo is None:
                    time = time.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > time:
                    raise BadArgument("End date must be in the future.")
                time = time - datetime.now(timezone.utc)
                vals["duration"] = time
                if time.total_seconds() < 60:
                    raise BadArgument("End date must be at least 1 minute in the future.")
            except Exception:
                raise BadArgument(
                    "Invalid end date. Use `--end` or `-e`. Ensure to pass a timezone, otherwise it defaults to UTC."
                )
        else:
            vals["duration"] = None
        vals["image"] = " ".join(vals["image"]) if vals["image"] else None
        vals["thumbnail"] = " ".join(vals["thumbnail"]) if vals["thumbnail"] else None
        return vals


class EditArgs(Args):
    require_prize = False
    require_time = False
