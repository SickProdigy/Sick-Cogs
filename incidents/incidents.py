"""Interactive staff reports over Red's existing ModLog records."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import re
from typing import Iterable, Optional

import discord
from redbot.core import Config, commands, modlog


GUILD_DEFAULTS = {"access_roles": []}
USER_ID_RE = re.compile(r"^(?:<@!?)?(\d{15,22})>?$")
TRACKED_ACTIONS = ("warning", "kick", "mute", "ban")


class Incidents(commands.Cog):
    """Browse and summarize Red ModLog cases without duplicating them."""

    __author__ = ["SickProdigy"]
    __version__ = "0.2.0"
    CONFIG_IDENTIFIER = 6202609220111001

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=self.CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**GUILD_DEFAULTS)

    async def red_delete_data_for_user(self, **kwargs):
        """No case data is owned here; Red ModLog owns its records."""

    async def _has_access(self, member: discord.Member) -> bool:
        perms = member.guild_permissions
        if perms.administrator or perms.manage_guild or perms.manage_messages:
            return True
        if await self.bot.is_mod(member):
            return True
        role_ids = set(await self.config.guild(member.guild).access_roles())
        return any(role.id in role_ids for role in member.roles)

    async def cog_check(self, ctx):
        if ctx.guild is None:
            return False
        return await self._has_access(ctx.author)

    @staticmethod
    def _utc(value):
        if value is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def action_name(case) -> str:
        return str(getattr(case, "action_type", "incident")).replace("_", " ").title()

    @staticmethod
    def _name(subject, fallback: Optional[str] = None):
        if subject is None:
            return fallback or "Unknown"
        if isinstance(subject, int):
            return f"{fallback or 'Unknown user'} (`{subject}`)"
        mention = getattr(subject, "mention", None)
        user_id = getattr(subject, "id", None)
        value = mention or str(subject)
        return f"{value} (`{user_id}`)" if user_id else value

    @staticmethod
    def parse_user_id(value: str) -> Optional[int]:
        match = USER_ID_RE.fullmatch(value.strip())
        return int(match.group(1)) if match else None

    @classmethod
    def sorted_cases(cls, cases: Iterable):
        return sorted(cases, key=lambda case: cls._utc(getattr(case, "created_at", None)), reverse=True)

    @classmethod
    def filter_cases(cls, cases: Iterable, *, action: Optional[str] = None, days: Optional[int] = None):
        action_key = action.strip().casefold().replace(" ", "_") if action else None
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365))) if days else None
        result = []
        for case in cls.sorted_cases(cases):
            if action_key and str(getattr(case, "action_type", "")).casefold() != action_key:
                continue
            if cutoff and cls._utc(getattr(case, "created_at", None)) < cutoff:
                continue
            result.append(case)
        return result

    @classmethod
    def case_line(cls, case):
        created = cls._utc(getattr(case, "created_at", None))
        number = getattr(case, "case_number", "?")
        target = cls._name(getattr(case, "user", None), getattr(case, "last_known_username", None))
        return f"**#{number} {cls.action_name(case)}** — {target} • <t:{int(created.timestamp())}:R>"

    @classmethod
    def case_embed(cls, case, *, position: Optional[int] = None, total: Optional[int] = None):
        created = cls._utc(getattr(case, "created_at", None))
        number = getattr(case, "case_number", "?")
        embed = discord.Embed(title=f"Case #{number} • {cls.action_name(case)}", color=discord.Color.orange(), timestamp=created)
        embed.add_field(name="User", value=cls._name(getattr(case, "user", None), getattr(case, "last_known_username", None)), inline=False)
        embed.add_field(name="Moderator", value=cls._name(getattr(case, "moderator", None)), inline=False)
        embed.add_field(name="Reason", value=(getattr(case, "reason", None) or "No reason recorded")[:1024], inline=False)
        until = getattr(case, "until", None)
        if until:
            until_dt = cls._utc(until)
            embed.add_field(name="Effective until", value=f"<t:{int(until_dt.timestamp())}:F> (<t:{int(until_dt.timestamp())}:R>)", inline=False)
        channel = getattr(case, "channel", None)
        if channel:
            embed.add_field(name="Channel", value=cls._name(channel), inline=False)
        modified = getattr(case, "modified_at", None)
        if modified:
            modified_dt = cls._utc(modified)
            embed.add_field(name="Last amended", value=f"<t:{int(modified_dt.timestamp())}:R> by {cls._name(getattr(case, 'amended_by', None))}", inline=False)
        footer = "Source: Red ModLog"
        if position is not None and total is not None:
            footer = f"Case {position + 1} of {total} • {footer}"
        embed.set_footer(text=footer)
        return embed

    @classmethod
    def summary_embed(cls, cases: Iterable, days: int = 7):
        all_cases = cls.sorted_cases(cases)
        window = cls.filter_cases(all_cases, days=days)
        embed = discord.Embed(title=f"Incident summary • last {days} day(s)", color=discord.Color.orange())
        if not window:
            embed.description = "No incidents were recorded in this period."
        else:
            actions = Counter(cls.action_name(case) for case in window)
            moderators = Counter(cls._name(getattr(case, "moderator", None)) for case in window)
            embed.description = f"**Total cases:** {len(window)}\n" + " • ".join(f"{name}: **{count}**" for name, count in actions.most_common())
            embed.add_field(name="Most active moderators", value="\n".join(f"{name} — **{count}**" for name, count in moderators.most_common(5)) or "No moderator data", inline=False)
        now = datetime.now(timezone.utc)
        latest_lines = []
        if all_cases:
            latest_lines.append(f"Any incident: **{(now - cls._utc(all_cases[0].created_at)).days} day(s)** ago")
        for label in TRACKED_ACTIONS:
            match = next((case for case in all_cases if label in str(getattr(case, "action_type", "")).casefold()), None)
            latest_lines.append(f"{label.title()}: **{(now - cls._utc(match.created_at)).days} day(s)** ago" if match else f"{label.title()}: none recorded")
        embed.add_field(name="Time since last incident", value="\n".join(latest_lines) or "No cases recorded", inline=False)
        embed.set_footer(text="Source: Red ModLog")
        return embed

    async def all_cases(self, guild):
        return self.sorted_cases(await modlog.get_all_cases(guild, self.bot))

    async def user_cases(self, guild, user_id: int):
        return self.sorted_cases(await modlog.get_cases_for_member(guild, self.bot, member_id=user_id))

    @commands.group(name="incidents", aliases=["incidentreport"], invoke_without_command=True)
    @commands.guild_only()
    async def incidents(self, ctx):
        """Open the staff Incident Center."""
        from .views import IncidentHome

        cases = await self.all_cases(ctx.guild)
        view = IncidentHome(self, ctx.author, cases)
        await ctx.send(embed=view.embed(), view=view)

    @incidents.command(name="recent")
    async def recent(self, ctx, limit: int = 10):
        """Browse recent Red ModLog cases."""
        from .views import CaseBrowser

        cases = (await self.all_cases(ctx.guild))[: max(1, min(limit, 100))]
        if not cases:
            await ctx.send("Red ModLog has no cases recorded for this server.")
            return
        view = CaseBrowser(self, ctx.author, cases)
        await ctx.send(embed=view.embed(), view=view)

    @incidents.command(name="member", aliases=["user"])
    async def member(self, ctx, *, user: str):
        """Browse cases for a member mention or Discord user ID."""
        from .views import CaseBrowser

        user_id = self.parse_user_id(user)
        if user_id is None:
            await ctx.send("Provide a member mention or numeric Discord user ID.")
            return
        cases = await self.user_cases(ctx.guild, user_id)
        if not cases:
            await ctx.send(f"Red ModLog has no cases recorded for `{user_id}`.")
            return
        view = CaseBrowser(self, ctx.author, cases)
        await ctx.send(embed=view.embed(), view=view)

    @incidents.command(name="case")
    async def case(self, ctx, case_number: int):
        """Show one Red ModLog case by number."""
        try:
            case = await modlog.get_case(case_number, ctx.guild, self.bot)
        except RuntimeError:
            await ctx.send("That case does not exist in this server.")
            return
        await ctx.send(embed=self.case_embed(case))

    @incidents.command(name="filter")
    async def filter_command(self, ctx, action: str, days: int = 30):
        """Browse cases matching an action type and date window."""
        from .views import CaseBrowser

        cases = self.filter_cases(await self.all_cases(ctx.guild), action=action, days=days)
        if not cases:
            await ctx.send("No cases match that action and date window.")
            return
        view = CaseBrowser(self, ctx.author, cases)
        await ctx.send(embed=view.embed(), view=view)

    @incidents.command(name="summary")
    async def summary(self, ctx, days: int = 7):
        """Summarize recorded incidents over the last 1–365 days."""
        days = max(1, min(days, 365))
        await ctx.send(embed=self.summary_embed(await self.all_cases(ctx.guild), days))

    @commands.group(name="incidentset", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def incidentset(self, ctx):
        """Configure additional roles allowed to use the Incident Center."""
        from .views import IncidentSetup

        view = IncidentSetup(self, ctx.author)
        await ctx.send(embed=await view.embed(ctx.guild), view=view)

    @incidentset.group(name="access", invoke_without_command=True)
    async def incidentset_access(self, ctx):
        """Configure additional Incident Center access roles."""
        await ctx.send_help()

    @incidentset_access.command(name="add")
    async def incidentset_access_add(self, ctx, role: discord.Role):
        async with self.config.guild(ctx.guild).access_roles() as role_ids:
            if role.id not in role_ids:
                role_ids.append(role.id)
        await ctx.send(f"{role.mention} can now use the Incident Center.")

    @incidentset_access.command(name="remove", aliases=["delete"])
    async def incidentset_access_remove(self, ctx, role: discord.Role):
        async with self.config.guild(ctx.guild).access_roles() as role_ids:
            if role.id in role_ids:
                role_ids.remove(role.id)
        await ctx.send(f"{role.mention} no longer has additional Incident Center access.")
