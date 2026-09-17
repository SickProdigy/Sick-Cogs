"""Staff reports over Red's existing ModLog records."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Iterable, List

import discord
from redbot.core import commands, modlog


class Incidents(commands.Cog):
    """Browse and summarize Red ModLog cases without duplicating them."""

    __author__ = ["SickProdigy"]
    __version__ = "0.1.0"

    def __init__(self, bot):
        self.bot = bot

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete; Red ModLog owns its own data."""

    @staticmethod
    def _utc(value):
        if value is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def sorted_cases(cls, cases: Iterable):
        return sorted(cases, key=lambda case: cls._utc(getattr(case, "created_at", None)), reverse=True)

    @staticmethod
    def _name(subject):
        if subject is None:
            return "Unknown"
        mention = getattr(subject, "mention", None)
        return mention or str(subject)

    @classmethod
    def case_line(cls, case):
        created = cls._utc(getattr(case, "created_at", None))
        number = getattr(case, "case_number", "?")
        action = str(getattr(case, "action_type", "incident")).replace("_", " ").title()
        target = cls._name(getattr(case, "user", None))
        return f"**#{number} {action}** — {target} • <t:{int(created.timestamp())}:R>"

    @commands.group(name="incidents", aliases=["incidentreport"], invoke_without_command=True)
    @commands.guild_only()
    @commands.mod_or_permissions(manage_messages=True)
    async def incidents(self, ctx):
        """Staff-only reports for the server's existing Red ModLog cases."""
        await ctx.send_help()

    @incidents.command(name="recent")
    async def recent(self, ctx, limit: int = 10):
        """Show recent moderation cases recorded by Red."""
        limit = max(1, min(limit, 25))
        cases = self.sorted_cases(await modlog.get_all_cases(ctx.guild, self.bot))[:limit]
        if not cases:
            await ctx.send("Red ModLog has no cases recorded for this server.")
            return
        embed = discord.Embed(title="Recent incidents", description="\n".join(self.case_line(case) for case in cases), color=discord.Color.orange())
        embed.set_footer(text="Source: Red ModLog • Use case <number> for details")
        await ctx.send(embed=embed)

    @incidents.command(name="member", aliases=["user"])
    async def member(self, ctx, member: discord.Member):
        """Show a member's recorded Red ModLog cases."""
        cases = self.sorted_cases(await modlog.get_cases_for_member(ctx.guild, self.bot, member=member))
        if not cases:
            await ctx.send(f"Red ModLog has no recorded cases for {member.mention}.")
            return
        embed = discord.Embed(title=f"Incidents for {member}", description="\n".join(self.case_line(case) for case in cases[:25]), color=discord.Color.orange())
        if len(cases) > 25:
            embed.set_footer(text=f"Showing 25 of {len(cases)} cases • Source: Red ModLog")
        await ctx.send(embed=embed)

    @incidents.command(name="summary")
    async def summary(self, ctx, days: int = 7):
        """Summarize recorded incidents over the last 1–90 days."""
        days = max(1, min(days, 90))
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        cases = self.sorted_cases(await modlog.get_all_cases(ctx.guild, self.bot))
        window = [case for case in cases if self._utc(getattr(case, "created_at", None)) >= cutoff]
        if not window:
            latest = cases[0] if cases else None
            suffix = "No cases have ever been recorded." if latest is None else f"Last recorded incident: <t:{int(self._utc(latest.created_at).timestamp())}:R>."
            await ctx.send(f"No incidents were recorded in the last {days} day(s). {suffix}")
            return
        actions = Counter(str(getattr(case, "action_type", "incident")).replace("_", " ").title() for case in window)
        moderators = Counter(self._name(getattr(case, "moderator", None)) for case in window)
        action_text = " • ".join(f"{name}: **{count}**" for name, count in actions.most_common())
        moderator_text = "\n".join(f"{name} — **{count}**" for name, count in moderators.most_common(5))
        embed = discord.Embed(title=f"Incident summary: last {days} day(s)", description=f"**Total cases:** {len(window)}\n{action_text}", color=discord.Color.orange())
        embed.add_field(name="Most active moderators", value=moderator_text or "No moderator data", inline=False)
        latest = cases[0]
        embed.set_footer(text=f"Last recorded incident: {self._utc(latest.created_at).strftime('%Y-%m-%d %H:%M UTC')} • Source: Red ModLog")
        await ctx.send(embed=embed)
