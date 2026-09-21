"""Tightly scoped role delegation for game communities and clans."""

import re
from typing import Dict, Optional, Sequence, Tuple

import discord
from redbot.core import Config, commands


GUILD_DEFAULTS = {"games": {}}
GAME_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class GameRoles(commands.Cog):
    """Let trusted game roles assign only explicitly approved member roles."""

    __author__ = ["SickProdigy"]
    __version__ = "0.1.0"
    CONFIG_IDENTIFIER = 771683711016269361842125069837015824197

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=self.CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**GUILD_DEFAULTS)

    @staticmethod
    def normalize_game(value: str) -> Optional[str]:
        key = value.strip().casefold()
        return key if GAME_KEY_RE.fullmatch(key) else None

    @staticmethod
    def _profile(games: Dict, key: str) -> Dict:
        profile = games.get(key, {})
        return profile if isinstance(profile, dict) else {}

    @staticmethod
    def _can_manage(member: discord.Member, profile: Dict) -> bool:
        if member.guild_permissions.manage_roles:
            return True
        manager_ids = {int(role_id) for role_id in profile.get("manager_roles", [])}
        return any(role.id in manager_ids for role in member.roles)

    @staticmethod
    def _role_is_manageable(guild: discord.Guild, role: discord.Role) -> bool:
        bot_member = guild.me
        return bool(
            bot_member
            and bot_member.guild_permissions.manage_roles
            and role != guild.default_role
            and not role.managed
            and role < bot_member.top_role
        )

    async def _send_manager_dashboard(self, ctx):
        from .views import AssignmentDashboard

        games = await self.config.guild(ctx.guild).games()
        eligible = {key: self._profile(games, key) for key in sorted(games) if self._profile(games, key) and self._can_manage(ctx.author, self._profile(games, key))}
        if not eligible:
            await ctx.send("You do not manage any configured game-role profiles.")
            return
        view = AssignmentDashboard(self, ctx.author, eligible)
        await ctx.send(embed=view.embed(), view=view)

    @commands.group(name="gameroles", aliases=["clanroles"], invoke_without_command=True)
    @commands.guild_only()
    async def gameroles(self, ctx):
        """Assign approved game roles through a dashboard or fallback commands."""
        await self._send_manager_dashboard(ctx)

    @commands.group(name="gameroleset", invoke_without_command=True)
    @commands.admin_or_permissions(manage_roles=True)
    @commands.guild_only()
    async def gameroleset(self, ctx):
        """Configure delegated game-role profiles."""
        from .views import SetupDashboard

        view = SetupDashboard(self, ctx.author)
        await view.prepare(ctx.guild)
        await ctx.send(embed=await view.embed(ctx.guild), view=view)

    @gameroleset.group(name="manager", invoke_without_command=True)
    async def gameroles_manager(self, ctx):
        """Configure roles allowed to manage one game's approved roles."""
        await ctx.send_help()

    @gameroles_manager.command(name="add")
    async def gameroles_manager_add(self, ctx, game: str, role: discord.Role):
        """Allow a role to manage one game profile."""
        key = self.normalize_game(game)
        if key is None:
            await ctx.send("Game names must use 1–32 letters, numbers, hyphens, or underscores.")
            return
        if role == ctx.guild.default_role or role.managed:
            await ctx.send("Choose a normal server role, not @everyone or an integration-managed role.")
            return
        async with self.config.guild(ctx.guild).games() as games:
            profile = games.setdefault(key, {"manager_roles": [], "assignable_roles": []})
            role_ids = profile.setdefault("manager_roles", [])
            if role.id not in role_ids:
                role_ids.append(role.id)
        await ctx.send(f"{role.mention} can now manage approved **{key}** roles.")

    @gameroles_manager.command(name="remove", aliases=["delete"])
    async def gameroles_manager_remove(self, ctx, game: str, role: discord.Role):
        """Remove a delegated manager role from one game profile."""
        key = self.normalize_game(game)
        if key is None:
            await ctx.send("That game profile name is invalid.")
            return
        removed = False
        async with self.config.guild(ctx.guild).games() as games:
            profile = self._profile(games, key)
            role_ids = profile.get("manager_roles", [])
            if role.id in role_ids:
                role_ids.remove(role.id)
                removed = True
        await ctx.send(f"{role.mention} is no longer a **{key}** manager." if removed else "That manager role was not configured.")

    @gameroleset.group(name="allow", invoke_without_command=True)
    async def gameroles_allow(self, ctx):
        """Configure roles that delegated managers may add or remove."""
        await ctx.send_help()

    @gameroles_allow.command(name="add")
    async def gameroles_allow_add(self, ctx, game: str, role: discord.Role):
        """Approve a role for delegated management in one game profile."""
        key = self.normalize_game(game)
        if key is None:
            await ctx.send("Game names must use 1–32 letters, numbers, hyphens, or underscores.")
            return
        if not self._role_is_manageable(ctx.guild, role):
            await ctx.send("I cannot manage that role. Check my Manage Roles permission and role position.")
            return
        async with self.config.guild(ctx.guild).games() as games:
            profile = games.setdefault(key, {"manager_roles": [], "assignable_roles": []})
            role_ids = profile.setdefault("assignable_roles", [])
            if role.id not in role_ids:
                role_ids.append(role.id)
        await ctx.send(f"{role.mention} is now an approved **{key}** member role.")

    @gameroles_allow.command(name="remove", aliases=["delete"])
    async def gameroles_allow_remove(self, ctx, game: str, role: discord.Role):
        """Remove a role from one game's approved list."""
        key = self.normalize_game(game)
        if key is None:
            await ctx.send("That game profile name is invalid.")
            return
        removed = False
        async with self.config.guild(ctx.guild).games() as games:
            profile = self._profile(games, key)
            role_ids = profile.get("assignable_roles", [])
            if role.id in role_ids:
                role_ids.remove(role.id)
                removed = True
        await ctx.send(f"{role.mention} is no longer an approved **{key}** role." if removed else "That approved role was not configured.")

    def _authorized_profile(self, member: discord.Member, games: Dict, role: discord.Role, game: Optional[str] = None) -> Optional[str]:
        keys = [self.normalize_game(game)] if game is not None else sorted(games)
        for key in keys:
            if key is None:
                continue
            profile = self._profile(games, key)
            if role.id in profile.get("assignable_roles", []) and self._can_manage(member, profile):
                return key
        return None

    async def _change_role(self, ctx, game: Optional[str], member: discord.Member, role: discord.Role, *, add: bool):
        games = await self.config.guild(ctx.guild).games()
        key = self._authorized_profile(ctx.author, games, role, game)
        if key is None:
            suffix = f" for **{game}**" if game else " in any profile you manage"
            await ctx.send(f"{role.mention} is not an approved role{suffix}.")
            return
        if not self._role_is_manageable(ctx.guild, role):
            await ctx.send("I cannot manage that role. Check my Manage Roles permission and role position.")
            return
        has_role = role in member.roles
        if add and has_role:
            await ctx.send(f"{member.mention} already has {role.mention}.")
            return
        if not add and not has_role:
            await ctx.send(f"{member.mention} does not have {role.mention}.")
            return
        try:
            if add:
                await member.add_roles(role, reason=f"GameRoles {key}: assigned by {ctx.author} ({ctx.author.id})")
            else:
                await member.remove_roles(role, reason=f"GameRoles {key}: removed by {ctx.author} ({ctx.author.id})")
        except discord.Forbidden:
            await ctx.send("Discord refused that role change. Check my permissions and role position.")
            return
        except discord.HTTPException:
            await ctx.send("Discord could not complete that role change. Please try again.")
            return
        action = "added to" if add else "removed from"
        await ctx.send(f"{role.mention} was {action} {member.mention} by {ctx.author.mention}.", allowed_mentions=discord.AllowedMentions.none())

    async def _parse_assignment(self, ctx, arguments: Sequence[str]) -> Optional[Tuple[Optional[str], discord.Member, discord.Role]]:
        if len(arguments) not in (2, 3):
            await ctx.send(f"Use `{ctx.clean_prefix}gameroles add [game] <member> <role>`.")
            return None
        game = arguments[0] if len(arguments) == 3 else None
        member_arg, role_arg = arguments[-2:]
        try:
            member = await commands.MemberConverter().convert(ctx, member_arg)
            role = await commands.RoleConverter().convert(ctx, role_arg)
        except commands.BadArgument as exc:
            await ctx.send(str(exc))
            return None
        return game, member, role

    @gameroles.command(name="add", aliases=["assign"])
    async def gameroles_add(self, ctx, *arguments: str):
        """Add an approved game role; the optional game keeps legacy syntax working."""
        parsed = await self._parse_assignment(ctx, arguments)
        if parsed:
            await self._change_role(ctx, *parsed, add=True)

    @gameroles.command(name="remove", aliases=["unassign"])
    async def gameroles_remove(self, ctx, *arguments: str):
        """Remove an approved game role; the optional game keeps legacy syntax working."""
        parsed = await self._parse_assignment(ctx, arguments)
        if parsed:
            await self._change_role(ctx, *parsed, add=False)

    @gameroleset.command(name="show", aliases=["status"])
    async def gameroles_show(self, ctx, game: str):
        """Show the configured manager and member roles for a game."""
        key = self.normalize_game(game)
        profile = self._profile(await self.config.guild(ctx.guild).games(), key or "")
        if key is None or not profile:
            await ctx.send("That game-role profile is not configured.")
            return
        managers = [ctx.guild.get_role(role_id) for role_id in profile.get("manager_roles", [])]
        allowed = [ctx.guild.get_role(role_id) for role_id in profile.get("assignable_roles", [])]
        manager_text = ", ".join(role.mention for role in managers if role) or "None"
        allowed_text = ", ".join(role.mention for role in allowed if role) or "None"
        embed = discord.Embed(title=f"Game roles: {key}", color=discord.Color.blurple())
        embed.add_field(name="Delegated managers", value=manager_text, inline=False)
        embed.add_field(name="Approved member roles", value=allowed_text, inline=False)
        await ctx.send(embed=embed)
