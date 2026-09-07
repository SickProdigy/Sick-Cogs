import asyncio
import collections
import datetime
import logging
import re
import time
import uuid
from itertools import islice
from math import ceil, isfinite
from typing import Dict, List, Literal, Optional, Set

import discord

from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.commands import Context
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS


log = logging.getLogger("red.Sick-Cogs.Reminder")


class Reminder(commands.Cog):
    """Utilities to remind yourself of whatever you want"""

    __author__ = ["SickProdigy"]
    __version__ = "1.1.0"

    TIME_AMNT_REGEX = re.compile("([1-9][0-9]*)([a-z]+)", re.IGNORECASE)
    TIME_QUANTITIES = collections.OrderedDict(
        [
            ("seconds", 1),
            ("minutes", 60),
            ("hours", 3600),
            ("days", 86400),
            ("weeks", 604800),
            ("months", 2628000),
            ("years", 31540000),
        ]
    )
    MAX_SECONDS = TIME_QUANTITIES["years"] * 2
    CONFIG_IDENTIFIER = int(
        "1348292267606297903903568219578370169450187613858557601832253276183023563385"
        "860872570416775575021079665631013972557943647633665882074464969932193856474375"
    )

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord", "owner", "user", "user_strict"],
        user_id: int,
    ):
        self._deleted_user_ids.add(user_id)
        self._cancel_user_tasks(user_id)
        await self.config.user_from_id(user_id).clear()

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=self.CONFIG_IDENTIFIER, force_registration=True
        )
        self.config.register_user(reminders=[], invalid_reminders=[], offset=0)
        self.futures: Dict[int, Dict[str, asyncio.Task]] = {}
        self._deleted_user_ids: Set[int] = set()
        self._startup_task = asyncio.create_task(self.start_saved_reminders())

    def cog_unload(self):
        self._startup_task.cancel()
        for user_id in list(self.futures):
            self._cancel_user_tasks(user_id)

    @commands.group(invoke_without_command=True, aliases=["remindme"], name="remind")
    async def command_remind(self, ctx: Context, time: str, *, reminder_text: str):
        """
        Remind yourself of something in a specific amount of time

        Examples for time: `5d`, `10m`, `10m30s`, `1h`, `1y1mo2w5d10h30m15s`
        Abbreviations: `s` for seconds, `m` for minutes, `h` for hours, `d` for days, `w` for weeks, `mo` for months, `y` for years
        Any longer abbreviation is accepted. `m` assumes minutes instead of months.
        One month is counted as exact 365/12 days.
        Ignores all invalid abbreviations.
        """
        seconds = self.get_seconds(time)
        if seconds is None:
            response = ":x: Invalid time format."
        elif seconds > self.MAX_SECONDS:
            response = ":x: Too long amount of time. Maximum: 2 years"
        else:
            user = ctx.message.author
            self._deleted_user_ids.discard(user.id)
            time_now = datetime.datetime.now(datetime.timezone.utc)
            days, secs = divmod(seconds, 3600 * 24)
            end_time = time_now + datetime.timedelta(days=days, seconds=secs)
            reminder = {
                "id": uuid.uuid4().hex,
                "content": reminder_text,
                "start_time": time_now.timestamp(),
                "end_time": end_time.timestamp(),
            }
            async with self.config.user(user).reminders() as user_reminders:
                user_reminders.append(reminder)
            self._schedule_reminder(user, reminder)
            user_offset = await self.config.user(ctx.author).offset()
            if seconds > 86400:
                formatted_time = self.format_absolute_time(end_time.timestamp(), user_offset)
                response = f":white_check_mark: I will remind you of that on {formatted_time}."
            else:
                duration = self.time_from_seconds(seconds)
                response = f":white_check_mark: I will remind you of that in {duration}."
        await ctx.send(response)

    @command_remind.group(name="forget")
    async def command_remind_forget(self, ctx: Context):
        """Forget your reminders"""
        pass

    @command_remind_forget.command(name="all")
    async def command_remind_forget_all(self, ctx: Context):
        """Forget **all** of your reminders"""
        self._cancel_user_tasks(ctx.message.author.id)
        user_config = self.config.user(ctx.message.author)
        async with user_config.reminders() as user_reminders:
            user_reminders.clear()
        await user_config.invalid_reminders.clear()
        await ctx.send(":put_litter_in_its_place: Forgot **all** of your reminders!")

    @command_remind_forget.command(name="one")
    async def command_remind_forget_one(self, ctx: Context, index_number_of_reminder: int):
        """
        Forget one of your reminders

        Use `[p]remind list` to find the index number of the reminder you wish to forget.
        """
        async with self.config.user(ctx.message.author).all() as user_data:
            if not user_data["reminders"]:
                await ctx.send("You don't have any reminders saved.")
                return
            time_sorted_reminders = sorted(user_data["reminders"], key=lambda x: (x["end_time"]))
            if not 1 <= index_number_of_reminder <= len(time_sorted_reminders):
                await ctx.send(f"There is no reminder at index {index_number_of_reminder}.")
                return
            removed = time_sorted_reminders.pop(index_number_of_reminder - 1)
            user_data["reminders"] = time_sorted_reminders
            reminder_id = removed.get("id")
            if reminder_id:
                task = self.futures.get(ctx.author.id, {}).pop(reminder_id, None)
                if task is not None:
                    task.cancel()
            end_time = self.format_absolute_time(removed["end_time"], user_data["offset"])
            msg = f":put_litter_in_its_place: Forgot reminder **#{index_number_of_reminder}**\n"
            msg += f"Date: {end_time}\nContent: `{removed['content']}`"
            await ctx.send(msg)

    @command_remind.command(name="list")
    async def command_remind_list(self, ctx: Context):
        """List your reminders"""
        user_data = await self.config.user(ctx.message.author).all()
        if not user_data["reminders"]:
            await ctx.send("There are no reminders to show.")
            return

        if not ctx.channel.permissions_for(ctx.me).embed_links:
            return await ctx.send(
                "I need the `Embed Messages` permission here to display this information."
            )

        embed_pages = await self.create_remind_list_embeds(ctx, user_data)
        await ctx.send(embed=embed_pages[0]) if len(embed_pages) == 1 else await menu(
            ctx, embed_pages, DEFAULT_CONTROLS
        )

    @command_remind.command(name="offset")
    async def command_remind_offset(self, ctx: Context, offset_time_in_hours: str):
        """
        Set a basic timezone offset
        from the default of UTC for use in [p]remindme list.

        This command accepts number values from `-23.75` to `+23.75`.
        You can look up your timezone offset on https://en.wikipedia.org/wiki/List_of_UTC_offsets
        """
        offset = self.remind_offset_check(offset_time_in_hours)
        if offset is not None:
            await self.config.user(ctx.author).offset.set(offset)
            offset_text = str(offset).replace(".0", "")
            await ctx.send(f"Your timezone offset was set to {offset_text} hours from UTC.")
        else:
            await ctx.send(
                f"That doesn't seem like a valid hour offset. "
                f"Check `{ctx.prefix}help remind offset`."
            )

    @staticmethod
    async def chunker(items: List[dict], chunk_size: int) -> List[List[dict]]:
        chunk_list = []
        iterator = iter(items)
        while chunk := list(islice(iterator, chunk_size)):
            chunk_list.append(chunk)
        return chunk_list

    async def create_remind_list_embeds(self, ctx: Context, user_data: dict) -> List[discord.Embed]:
        """Embed creator for command_remind_list."""
        offset = user_data["offset"]
        reminder_list = []
        time_sorted_reminders = sorted(user_data["reminders"], key=lambda x: (x["end_time"]))
        entry_size = len(str(len(time_sorted_reminders)))

        for i, reminder_dict in enumerate(time_sorted_reminders, 1):
            entry_number = f"{str(i).zfill(entry_size)}"
            end_time = reminder_dict["end_time"]
            exact_time_timestamp = self.format_absolute_time(end_time, offset)
            relative_timestamp = f"<t:{round(end_time)}:R>"
            content = reminder_dict["content"]
            display_content = content if len(content) < 200 else f"{content[:200]} [...]"
            failure_note = (
                " **Delivery failed; remove or recreate this reminder.**"
                if reminder_dict.get("failed_at")
                else ""
            )
            reminder = (
                f"`{entry_number}`. {exact_time_timestamp}, {relative_timestamp}"
                f"{failure_note}:\n{display_content}\n\n"
            )
            reminder_list.append(reminder)

        reminder_text_chunks = await self.chunker(reminder_list, 7)
        max_pages = ceil(len(reminder_list) / 7)
        offset_hours = str(user_data["offset"]).replace(".0", "")
        offset_text = f" • UTC offset of {offset_hours}h applied" if offset != 0 else ""
        menu_pages = []
        for chunk in reminder_text_chunks:
            embed = discord.Embed(title="", description="".join(chunk))
            embed.set_author(
                name=f"Reminders for {ctx.author}", icon_url=ctx.author.display_avatar.url
            )
            embed.set_footer(text=f"Page {len(menu_pages) + 1} of {max_pages}{offset_text}")
            menu_pages.append(embed)
        return menu_pages

    def get_seconds(self, time: str):
        """Returns the amount of converted time or None if invalid"""
        seconds = 0
        for time_match in self.TIME_AMNT_REGEX.finditer(time):
            time_amnt = int(time_match.group(1))
            time_abbrev = time_match.group(2)
            time_quantity = discord.utils.find(
                lambda item: item[0].startswith(time_abbrev), self.TIME_QUANTITIES.items()
            )
            if time_quantity is not None:
                seconds += time_amnt * time_quantity[1]
        return None if seconds == 0 else seconds

    def _cancel_user_tasks(self, user_id: int) -> None:
        for task in self.futures.pop(user_id, {}).values():
            task.cancel()

    def _schedule_reminder(self, user: discord.User, reminder: dict) -> None:
        reminder_id = reminder["id"]
        existing = self.futures.setdefault(user.id, {}).pop(reminder_id, None)
        if existing is not None:
            existing.cancel()
        task = asyncio.create_task(self.remind_later(user, reminder))
        self.futures[user.id][reminder_id] = task

        def discard_finished(finished: asyncio.Task) -> None:
            user_tasks = self.futures.get(user.id)
            if user_tasks is not None and user_tasks.get(reminder_id) is finished:
                user_tasks.pop(reminder_id, None)
                if not user_tasks:
                    self.futures.pop(user.id, None)

        task.add_done_callback(discard_finished)

    async def remind_later(self, user: discord.User, reminder: dict) -> None:
        """Deliver a saved reminder once its UTC timestamp is due."""
        delay = max(0.0, reminder["end_time"] - time.time())
        await asyncio.sleep(delay)
        embed = discord.Embed(
            title="Reminder", description=reminder["content"], color=discord.Colour.blue()
        )
        try:
            await user.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning(
                "Could not deliver reminder %s to user %s: %s",
                reminder["id"],
                user.id,
                exc,
            )
            async with self.config.user(user).reminders() as user_reminders:
                for saved in user_reminders:
                    if isinstance(saved, dict) and saved.get("id") == reminder["id"]:
                        saved["failed_at"] = time.time()
                        break
            return
        async with self.config.user(user).reminders() as user_reminders:
            user_reminders[:] = [
                saved
                for saved in user_reminders
                if not isinstance(saved, dict) or saved.get("id") != reminder["id"]
            ]

    @staticmethod
    def remind_offset_check(offset: str) -> Optional[float]:
        """Float validator for command_remind_offset."""
        try:
            offset = float(offset)
        except ValueError:
            return None
        if not isfinite(offset) or not -23.75 <= offset <= 23.75:
            return None
        return round(offset * 4) / 4.0

    @staticmethod
    def format_absolute_time(timestamp: float, offset: float) -> str:
        if offset == 0:
            return f"<t:{round(timestamp)}:F>"
        local_time = datetime.datetime.fromtimestamp(
            timestamp, tz=datetime.timezone.utc
        ) + datetime.timedelta(hours=offset)
        sign = "+" if offset >= 0 else "-"
        absolute_offset = abs(offset)
        hours = int(absolute_offset)
        minutes = round((absolute_offset - hours) * 60)
        return f"{local_time:%Y-%m-%d %H:%M} (UTC{sign}{hours:02d}:{minutes:02d})"

    @staticmethod
    def normalize_reminder(reminder: object) -> Optional[dict]:
        if not isinstance(reminder, dict):
            return None
        content = reminder.get("content")
        start_time = reminder.get("start_time")
        end_time = reminder.get("end_time")
        if not isinstance(content, str) or not content:
            return None
        if not isinstance(start_time, (int, float)) or not isfinite(start_time):
            return None
        if not isinstance(end_time, (int, float)) or not isfinite(end_time):
            return None
        reminder_id = reminder.get("id")
        if not isinstance(reminder_id, str) or not reminder_id:
            reminder_id = uuid.uuid4().hex
        normalized = {
            "id": reminder_id,
            "content": content,
            "start_time": float(start_time),
            "end_time": float(end_time),
        }
        failed_at = reminder.get("failed_at")
        if isinstance(failed_at, (int, float)) and isfinite(failed_at):
            normalized["failed_at"] = float(failed_at)
        return normalized

    async def start_saved_reminders(self) -> None:
        await self.bot.wait_until_red_ready()
        user_configs = await self.config.all_users()
        for user_id, user_config in list(user_configs.items()):
            if user_id in self._deleted_user_ids:
                continue
            raw_reminders = user_config.get("reminders", [])
            if isinstance(raw_reminders, list):
                saved_reminders = raw_reminders
                malformed = []
            else:
                saved_reminders = []
                malformed = [raw_reminders]
            reminders = []
            for saved in saved_reminders:
                normalized = self.normalize_reminder(saved)
                if normalized is None:
                    malformed.append(saved)
                else:
                    reminders.append(normalized)
            if malformed:
                quarantined = user_config.get("invalid_reminders", [])
                if not isinstance(quarantined, list):
                    quarantined = []
                await self.config.user_from_id(user_id).invalid_reminders.set(
                    quarantined + malformed
                )
            if reminders != saved_reminders:
                await self.config.user_from_id(user_id).reminders.set(reminders)
            if user_id in self._deleted_user_ids:
                await self.config.user_from_id(user_id).clear()
                continue
            active_reminders = [r for r in reminders if "failed_at" not in r]
            if not active_reminders:
                continue
            user = self.bot.get_user(user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(user_id)
                except (discord.NotFound, discord.HTTPException):
                    log.warning("Could not fetch user %s while restoring reminders", user_id)
                    continue
            for reminder in active_reminders:
                self._schedule_reminder(user, reminder)

    @staticmethod
    def time_from_seconds(seconds: int) -> str:
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            msg = f"{hours} hour" if hours == 1 else f"{hours} hours"
            if minutes != 0:
                msg += f" and {minutes} minute" if minutes == 1 else f" and {minutes} minutes"
        elif minutes:
            msg = f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
            if seconds != 0:
                msg += f" and {seconds} second" if seconds == 1 else f" and {seconds} seconds"
        else:
            msg = f"{seconds} second" if seconds == 1 else f"{seconds} seconds"
        return msg
