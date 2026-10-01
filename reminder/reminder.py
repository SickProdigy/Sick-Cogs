import asyncio
import datetime
import logging
import re
import time
import uuid
from dataclasses import dataclass
from math import isfinite
from typing import Dict, List, Literal, Optional, Tuple

import discord

from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.commands import Context
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu


log = logging.getLogger("red.sick-cogs.Reminder")


@dataclass
class ReminderEntry:
    reminder_id: str
    content: str
    created_at: float
    due_at: float
    failed_at: Optional[float] = None

    @classmethod
    def from_raw(cls, raw: object) -> Optional["ReminderEntry"]:
        if not isinstance(raw, dict):
            return None
        content = raw.get("content")
        created_at = raw.get("start_time")
        due_at = raw.get("end_time")
        if not isinstance(content, str) or not content:
            return None
        if not ReminderEntry._valid_timestamp(created_at):
            return None
        if not ReminderEntry._valid_timestamp(due_at):
            return None
        reminder_id = raw.get("id")
        if not isinstance(reminder_id, str) or not reminder_id:
            reminder_id = uuid.uuid4().hex
        failed_at = raw.get("failed_at")
        if not ReminderEntry._valid_timestamp(failed_at):
            failed_at = None
        return cls(reminder_id, content, float(created_at), float(due_at), failed_at)

    @staticmethod
    def _valid_timestamp(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)

    def to_raw(self) -> dict:
        raw = {
            "id": self.reminder_id,
            "content": self.content,
            "start_time": self.created_at,
            "end_time": self.due_at,
        }
        if self.failed_at is not None:
            raw["failed_at"] = self.failed_at
        return raw


class Reminder(commands.Cog):
    """Create private reminders that survive cog reloads and bot restarts."""

    __author__ = ["SickProdigy"]
    __version__ = "1.0.0"

    CONFIG_IDENTIFIER = int(
        "1348292267606297903903568219578370169450187613858557601832253276183023563385"
        "860872570416775575021079665631013972557943647633665882074464969932193856474375"
    )
    MAX_SECONDS = 63_080_000
    CHECK_INTERVAL = 3600.0
    DURATION_PATTERN = re.compile(r"([1-9][0-9]*)([a-z]+)", re.IGNORECASE)
    DURATION_UNITS: Tuple[Tuple[str, int], ...] = (
        ("seconds", 1),
        ("minutes", 60),
        ("hours", 3600),
        ("days", 86_400),
        ("weeks", 604_800),
        ("months", 2_628_000),
        ("years", 31_540_000),
    )

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=self.CONFIG_IDENTIFIER, force_registration=True
        )
        self.config.register_user(reminders=[], invalid_reminders=[], offset=0)
        self._wake_scheduler = asyncio.Event()
        self._deleted_users = set()
        self._scheduler = asyncio.create_task(self._scheduler_loop())

    def cog_unload(self) -> None:
        self._scheduler.cancel()

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        self._deleted_users.add(user_id)
        await self.config.user_from_id(user_id).clear()
        self._wake_scheduler.set()

    @commands.group(name="remind", aliases=["remindme"], invoke_without_command=True)
    async def remind(self, ctx: Context, duration: str, *, text: str) -> None:
        """Create a reminder. Durations may be combined, for example `1h30m`."""
        seconds = self.parse_duration(duration)
        if seconds is None:
            await ctx.send(":x: Invalid time format.")
            return
        if seconds > self.MAX_SECONDS:
            await ctx.send(":x: Too long amount of time. Maximum: 2 years")
            return

        now = time.time()
        entry = ReminderEntry(uuid.uuid4().hex, text, now, now + seconds)
        self._deleted_users.discard(ctx.author.id)
        async with self.config.user(ctx.author).reminders() as saved:
            saved.append(entry.to_raw())
        self._wake_scheduler.set()

        if seconds > 86_400:
            offset = self.validate_offset(await self.config.user(ctx.author).offset()) or 0.0
            due_text = self.format_due_time(entry.due_at, offset)
            await ctx.send(f":white_check_mark: I will remind you of that on {due_text}.")
        else:
            await ctx.send(
                f":white_check_mark: I will remind you of that in {self.describe_duration(seconds)}."
            )

    @remind.group(name="forget")
    async def remind_forget(self, ctx: Context) -> None:
        """Remove pending reminders."""

    @remind_forget.command(name="all")
    async def remind_forget_all(self, ctx: Context) -> None:
        """Remove all of your pending and quarantined reminders."""
        user_config = self.config.user(ctx.author)
        await user_config.reminders.clear()
        await user_config.invalid_reminders.clear()
        self._wake_scheduler.set()
        await ctx.send(":put_litter_in_its_place: Forgot **all** of your reminders!")

    @remind_forget.command(name="one")
    async def remind_forget_one(self, ctx: Context, number: int) -> None:
        """Remove one reminder by its number from `[p]remind list`."""
        async with self.config.user(ctx.author).all() as user_data:
            entries = self._valid_entries(user_data.get("reminders", []))
            entries.sort(key=lambda entry: entry.due_at)
            if not 1 <= number <= len(entries):
                await ctx.send(f"There is no reminder at index {number}.")
                return
            removed = entries.pop(number - 1)
            user_data["reminders"] = [entry.to_raw() for entry in entries]
            offset = self.validate_offset(user_data.get("offset")) or 0.0

        self._wake_scheduler.set()
        due_text = self.format_due_time(removed.due_at, offset)
        await ctx.send(
            f":put_litter_in_its_place: Forgot reminder **#{number}**\n"
            f"Date: {due_text}\nContent: `{removed.content}`"
        )

    @remind.command(name="list")
    @commands.bot_has_permissions(embed_links=True)
    async def remind_list(self, ctx: Context) -> None:
        """List your pending reminders."""
        user_data = await self.config.user(ctx.author).all()
        entries = self._valid_entries(user_data.get("reminders", []))
        if not entries:
            await ctx.send("There are no reminders to show.")
            return
        if not ctx.channel.permissions_for(ctx.me).embed_links:
            await ctx.send("I need the `Embed Messages` permission here to display reminders.")
            return

        offset = self.validate_offset(user_data.get("offset")) or 0.0
        pages = self.build_list_pages(ctx.author, entries, offset)
        if len(pages) == 1:
            await ctx.send(embed=pages[0])
        else:
            await menu(ctx, pages, DEFAULT_CONTROLS)

    @remind.command(name="offset")
    async def remind_offset(self, ctx: Context, hours: str) -> None:
        """Set a UTC offset from -23.75 through +23.75 for calendar displays."""
        offset = self.validate_offset(hours)
        if offset is None:
            await ctx.send(
                f"That doesn't seem like a valid hour offset. "
                f"Check `{ctx.clean_prefix}help remind offset`."
            )
            return
        await self.config.user(ctx.author).offset.set(offset)
        await ctx.send(f"Your timezone offset was set to {offset:g} hours from UTC.")

    async def _scheduler_loop(self) -> None:
        await self.bot.wait_until_red_ready()
        await self._normalize_saved_data()
        while True:
            try:
                self._wake_scheduler.clear()
                await self._deliver_due_reminders()
                delay = await self._next_check_delay()
                await asyncio.wait_for(self._wake_scheduler.wait(), timeout=delay)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Unexpected error in the Reminder scheduler")
                await asyncio.sleep(30)

    async def _normalize_saved_data(self) -> None:
        users = await self.config.all_users()
        for user_id, snapshot in users.items():
            if user_id in self._deleted_users:
                continue
            raw_reminders = snapshot.get("reminders", [])
            malformed = []
            if not isinstance(raw_reminders, list):
                malformed.append(raw_reminders)
                raw_reminders = []
            entries = []
            for raw in raw_reminders:
                entry = ReminderEntry.from_raw(raw)
                if entry is None:
                    malformed.append(raw)
                else:
                    entries.append(entry)
            user_config = self.config.user_from_id(user_id)
            if malformed:
                quarantined = snapshot.get("invalid_reminders", [])
                if not isinstance(quarantined, list):
                    quarantined = [quarantined]
                await user_config.invalid_reminders.set(quarantined + malformed)
            normalized = [entry.to_raw() for entry in entries]
            if normalized != raw_reminders:
                await user_config.reminders.set(normalized)
            if user_id in self._deleted_users:
                await user_config.clear()

    async def _deliver_due_reminders(self) -> None:
        now = time.time()
        users = await self.config.all_users()
        for user_id, snapshot in users.items():
            if user_id in self._deleted_users:
                continue
            entries = self._valid_entries(snapshot.get("reminders", []))
            due = [entry for entry in entries if entry.failed_at is None and entry.due_at <= now]
            if not due:
                continue
            user = self.bot.get_user(user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(user_id)
                except (discord.NotFound, discord.HTTPException) as exc:
                    log.warning("Could not fetch reminder user %s: %s", user_id, exc)
                    await self._mark_failed(user_id, due, now)
                    continue
            for entry in due:
                await self._deliver_one(user, entry)

    async def _deliver_one(self, user: discord.User, entry: ReminderEntry) -> None:
        embed = discord.Embed(
            title="Reminder", description=entry.content, color=discord.Colour.blue()
        )
        try:
            await user.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Could not deliver reminder %s to user %s: %s", entry.reminder_id, user.id, exc)
            await self._mark_failed(user.id, [entry], time.time())
            return
        async with self.config.user(user).reminders() as saved:
            saved[:] = [
                raw
                for raw in saved
                if not isinstance(raw, dict) or raw.get("id") != entry.reminder_id
            ]

    async def _mark_failed(
        self, user_id: int, entries: List[ReminderEntry], failed_at: float
    ) -> None:
        failed_ids = {entry.reminder_id for entry in entries}
        async with self.config.user_from_id(user_id).reminders() as saved:
            for raw in saved:
                if isinstance(raw, dict) and raw.get("id") in failed_ids:
                    raw["failed_at"] = failed_at

    async def _next_check_delay(self) -> float:
        now = time.time()
        next_due = None
        for snapshot in (await self.config.all_users()).values():
            for entry in self._valid_entries(snapshot.get("reminders", [])):
                if entry.failed_at is not None:
                    continue
                next_due = entry.due_at if next_due is None else min(next_due, entry.due_at)
        if next_due is None:
            return self.CHECK_INTERVAL
        return max(0.05, min(self.CHECK_INTERVAL, next_due - now))

    @staticmethod
    def _valid_entries(raw_entries: object) -> List[ReminderEntry]:
        if not isinstance(raw_entries, list):
            return []
        return [entry for raw in raw_entries if (entry := ReminderEntry.from_raw(raw))]

    @classmethod
    def parse_duration(cls, value: str) -> Optional[int]:
        compact = re.sub(r"\s+", "", value or "")
        matches = list(cls.DURATION_PATTERN.finditer(compact))
        if not matches or "".join(match.group(0) for match in matches) != compact:
            return None

        seconds = 0
        for match in matches:
            amount = int(match.group(1))
            abbreviation = match.group(2).lower()
            unit = next(
                (multiplier for name, multiplier in cls.DURATION_UNITS if name.startswith(abbreviation)),
                None,
            )
            if unit is None:
                return None
            seconds += amount * unit
        return seconds or None

    @staticmethod
    def validate_offset(value: object) -> Optional[float]:
        try:
            offset = float(value)
        except (TypeError, ValueError):
            return None
        if not isfinite(offset) or not -23.75 <= offset <= 23.75:
            return None
        return round(offset * 4) / 4.0

    @staticmethod
    def format_due_time(timestamp: float, offset: float) -> str:
        if offset == 0:
            return f"<t:{round(timestamp)}:F>"
        local_time = datetime.datetime.fromtimestamp(
            timestamp, tz=datetime.timezone.utc
        ) + datetime.timedelta(hours=offset)
        sign = "+" if offset >= 0 else "-"
        absolute = abs(offset)
        offset_hours = int(absolute)
        offset_minutes = round((absolute - offset_hours) * 60)
        return (
            f"{local_time:%Y-%m-%d %H:%M} "
            f"(UTC{sign}{offset_hours:02d}:{offset_minutes:02d})"
        )

    @staticmethod
    def describe_duration(seconds: int) -> str:
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        parts = []
        if hours:
            parts.append(f"{hours} hour" if hours == 1 else f"{hours} hours")
        if minutes:
            parts.append(f"{minutes} minute" if minutes == 1 else f"{minutes} minutes")
        if seconds or not parts:
            parts.append(f"{seconds} second" if seconds == 1 else f"{seconds} seconds")
        return " and ".join(parts)

    @classmethod
    def build_list_pages(
        cls, author: discord.abc.User, entries: List[ReminderEntry], offset: float
    ) -> List[discord.Embed]:
        entries.sort(key=lambda entry: entry.due_at)
        rows = []
        width = len(str(len(entries)))
        for number, entry in enumerate(entries, 1):
            failed = " **Delivery failed; remove or recreate.**" if entry.failed_at else ""
            content = entry.content if len(entry.content) <= 200 else f"{entry.content[:200]} […]"
            rows.append(
                f"`{number:0{width}}`. {cls.format_due_time(entry.due_at, offset)}, "
                f"<t:{round(entry.due_at)}:R>{failed}:\n{content}\n\n"
            )

        pages = []
        chunks = [rows[index : index + 7] for index in range(0, len(rows), 7)]
        offset_note = f" • UTC offset {offset:g}h" if offset else ""
        for page_number, chunk in enumerate(chunks, 1):
            embed = discord.Embed(description="".join(chunk))
            embed.set_author(name=f"Reminders for {author}", icon_url=author.display_avatar.url)
            embed.set_footer(text=f"Page {page_number} of {len(chunks)}{offset_note}")
            pages.append(embed)
        return pages
