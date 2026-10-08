import time
from typing import Optional

import discord
from redbot.core import Config, checks, commands

from .policy import (
    AccessDecision,
    command_capability,
    evaluate_capability,
    normalize_capability,
    normalize_entitlement,
    normalize_target,
)
from .views import AccessControlPanel

CONFIG_IDENTIFIER = 975310864220170196
CONFIG_SCHEMA_VERSION = 2
DEFAULT_GLOBAL = {
    "schema_version": 0,
    "enforcement_enabled": False,
    "guild_mode": "open",
    "allowed_guild_ids": [],
    "home_guild_id": None,
    "vip_role_id": None,
    "command_mappings": {},
    "guild_entitlements": {},
    "audit_log": [],
}
DEFAULT_GUILD = {"capability_grants": {}}


class AccessControl(commands.Cog):
    """Central guild and feature entitlement policy for Sick-Cogs."""

    __author__ = ["SickProdigy"]
    __version__ = "0.2.1"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=CONFIG_IDENTIFIER, force_registration=True
        )
        self.config.register_global(
            **DEFAULT_GLOBAL,
            enabled=False,
            allowed_guilds=[],
            home_guild=None,
            vip_role=None,
            mappings={},
            entitlements={},
        )
        self.config.register_guild(**DEFAULT_GUILD, grants={})

    async def cog_load(self):
        await self._migrate()
        self.bot.add_check(self._global_command_check)

    def cog_unload(self):
        self.bot.remove_check(self._global_command_check)

    async def _migrate(self):
        schema = await self.config.schema_version()
        if schema >= CONFIG_SCHEMA_VERSION:
            return
        if schema < 2:
            await self.config.enforcement_enabled.set(await self.config.enabled())
            await self.config.allowed_guild_ids.set(await self.config.allowed_guilds())
            await self.config.home_guild_id.set(await self.config.home_guild())
            await self.config.vip_role_id.set(await self.config.vip_role())
            await self.config.command_mappings.set(await self.config.mappings())
            old_entitlements = await self.config.entitlements()
            migrated = {}
            for guild_id, record in old_entitlements.items():
                if not isinstance(record, dict):
                    continue
                updated = {
                    "capabilities": record.get("capabilities", []),
                    "expires_at": record.get("expires_at", record.get("expires", 0)),
                    "sponsor_user_id": record.get(
                        "sponsor_user_id", record.get("sponsor")
                    ),
                    "issued_at": record.get("issued_at", 0),
                    "issued_by": record.get("issued_by", 0),
                }
                normalized = normalize_entitlement(updated)
                if normalized is not None:
                    migrated[str(guild_id)] = normalized
            await self.config.guild_entitlements.set(migrated)
            for guild_id, data in (await self.config.all_guilds()).items():
                if data.get("grants") and not data.get("capability_grants"):
                    await self.config.guild_from_id(
                        guild_id
                    ).capability_grants.set(data["grants"])
        await self.config.schema_version.set(CONFIG_SCHEMA_VERSION)

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        for guild_id, data in (await self.config.all_guilds()).items():
            grants = data.get("capability_grants", {})
            changed = False
            for grant in grants.values():
                users = [
                    value for value in grant.get("users", [])
                    if int(value) != int(user_id)
                ]
                if users != grant.get("users", []):
                    grant["users"] = users
                    changed = True
            if changed:
                await self.config.guild_from_id(
                    guild_id
                ).capability_grants.set(grants)
        entitlements = await self.config.guild_entitlements()
        changed = False
        for record in entitlements.values():
            if int(record.get("sponsor_user_id", 0) or 0) == int(user_id):
                record["sponsor_user_id"] = None
                changed = True
            if int(record.get("issued_by", 0) or 0) == int(user_id):
                record["issued_by"] = 0
                changed = True
        if changed:
            await self.config.guild_entitlements.set(entitlements)
        audit = await self.config.audit_log()
        for entry in audit:
            if int(entry.get("actor_id", 0) or 0) == int(user_id):
                entry["actor_id"] = 0
        await self.config.audit_log.set(audit)

    async def audit(self, actor_id: int, action: str, detail: str):
        entries = await self.config.audit_log()
        entries.append({
            "timestamp": int(time.time()),
            "actor_id": int(actor_id),
            "action": action[:60],
            "detail": detail[:200],
        })
        await self.config.audit_log.set(entries[-100:])

    async def _sponsor_valid(self, entitlement) -> bool:
        if not entitlement:
            return True
        home_id = await self.config.home_guild_id()
        role_id = await self.config.vip_role_id()
        if not home_id or not role_id:
            return True
        sponsor_id = int(entitlement.get("sponsor_user_id", 0) or 0)
        guild = self.bot.get_guild(int(home_id))
        if guild is None or not sponsor_id:
            return False
        member = guild.get_member(sponsor_id)
        if member is None:
            try:
                member = await guild.fetch_member(sponsor_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return False
        return any(role.id == int(role_id) for role in member.roles)

    async def guild_allowed(self, guild_id: int) -> AccessDecision:
        if await self.config.guild_mode() == "open":
            return AccessDecision(True, source="open guild mode")
        if int(guild_id) in set(await self.config.allowed_guild_ids()):
            return AccessDecision(True, source="guild allowlist")
        record = normalize_entitlement(
            (await self.config.guild_entitlements()).get(str(guild_id))
        )
        if record is None:
            return AccessDecision(False, "This server is not authorized to use this bot.")
        expires = record["expires_at"]
        if expires and expires <= int(time.time()):
            return AccessDecision(False, "This server's access entitlement has expired.")
        if not await self._sponsor_valid(record):
            return AccessDecision(False, "This server's sponsoring VIP is no longer eligible.")
        return AccessDecision(True, source="guild entitlement")

    async def has_capability(self, guild, user, capability: str) -> AccessDecision:
        """Return a structured policy decision for commands or background work."""
        try:
            capability = normalize_capability(capability)
        except ValueError as error:
            return AccessDecision(False, str(error))
        if await self.bot.is_owner(user):
            return AccessDecision(True, source="bot owner", capability=capability)
        if not await self.config.enforcement_enabled():
            return AccessDecision(
                True, source="enforcement disabled", capability=capability
            )
        if guild is None:
            return AccessDecision(
                False, "This protected feature is server-only.", capability=capability
            )
        guild_access = await self.guild_allowed(guild.id)
        if not guild_access.allowed:
            return AccessDecision(False, guild_access.reason, capability=capability)
        grants = await self.config.guild(guild).capability_grants()
        record = (await self.config.guild_entitlements()).get(str(guild.id))
        return evaluate_capability(
            capability=capability,
            user_id=user.id,
            role_ids=(role.id for role in getattr(user, "roles", [])),
            grants=grants,
            entitlement=record,
            now=int(time.time()),
            sponsor_valid=await self._sponsor_valid(record) if record else True,
        )

    async def _global_command_check(self, ctx):
        if (
            ctx.command is None
            or ctx.cog is self
            or await self.bot.is_owner(ctx.author)
            or not await self.config.enforcement_enabled()
        ):
            return True
        if ctx.guild is None:
            raise commands.UserFeedbackCheckFailure(
                "Access-controlled commands are only available in a server."
            )
        guild_access = await self.guild_allowed(ctx.guild.id)
        if not guild_access.allowed:
            raise commands.UserFeedbackCheckFailure(guild_access.reason)
        capability = command_capability(
            ctx.command.qualified_name,
            ctx.cog.qualified_name if ctx.cog else None,
            await self.config.command_mappings(),
        )
        if capability:
            decision = await self.has_capability(ctx.guild, ctx.author, capability)
            if not decision.allowed:
                raise commands.UserFeedbackCheckFailure(decision.reason)
        return True

    async def status_embed(self, guild=None, notice=""):
        mappings = await self.config.command_mappings()
        entitlements = await self.config.guild_entitlements()
        allowlist = await self.config.allowed_guild_ids()
        embed = discord.Embed(
            title="AccessControl",
            description=notice or "Central guild and feature access policy.",
            color=(
                discord.Color.orange()
                if await self.config.enforcement_enabled()
                else discord.Color.light_grey()
            ),
        )
        embed.add_field(
            name="Enforcement",
            value="Enabled" if await self.config.enforcement_enabled() else "Disabled",
        )
        embed.add_field(name="Guild mode", value=await self.config.guild_mode())
        embed.add_field(name="Allowed guilds", value=str(len(allowlist)))
        embed.add_field(name="Protected targets", value=str(len(mappings)))
        embed.add_field(name="Entitled guilds", value=str(len(entitlements)))
        home, role = (
            await self.config.home_guild_id(),
            await self.config.vip_role_id(),
        )
        embed.add_field(
            name="VIP authority",
            value=f"Guild {home}; role {role}" if home and role else "Not configured",
            inline=False,
        )
        if guild:
            grants = await self.config.guild(guild).capability_grants()
            embed.add_field(
                name=guild.name,
                value=f"{len(grants)} local capability grant records",
                inline=False,
            )
        embed.set_footer(text="Bot owners always retain recovery access.")
        return embed

    @commands.group(name="accesscontrol", aliases=["acctl"], invoke_without_command=True)
    @checks.is_owner()
    async def accesscontrol(self, ctx):
        """Configure central guild and feature access."""
        await ctx.send(
            embed=await self.status_embed(ctx.guild),
            view=AccessControlPanel(self, ctx.author),
        )

    @accesscontrol.command(name="status")
    async def status_command(self, ctx):
        await ctx.send(embed=await self.status_embed(ctx.guild))

    @accesscontrol.command()
    async def enforcement(self, ctx, enabled: bool):
        await self.config.enforcement_enabled.set(enabled)
        await self.audit(ctx.author.id, "enforcement", str(enabled))
        await ctx.send(f"AccessControl enforcement is now {enabled}.")

    @accesscontrol.command()
    async def guildmode(self, ctx, mode: str):
        mode = mode.lower()
        if mode not in {"open", "allowlist"}:
            await ctx.send("Mode must be open or allowlist.")
            return
        await self.config.guild_mode.set(mode)
        await self.audit(ctx.author.id, "guild mode", mode)
        await ctx.send(f"Guild access mode is now {mode}.")

    @accesscontrol.group(name="guild", invoke_without_command=True)
    async def guild_group(self, ctx):
        values = await self.config.allowed_guild_ids()
        await ctx.send(
            "Allowed guild IDs: " + (", ".join(map(str, values)) if values else "None")
        )

    @guild_group.command(name="allow")
    async def guild_allow(self, ctx, guild_id: int):
        values = set(await self.config.allowed_guild_ids())
        values.add(guild_id)
        await self.config.allowed_guild_ids.set(sorted(values))
        await self.audit(ctx.author.id, "allow guild", str(guild_id))
        await ctx.tick()

    @guild_group.command(name="remove")
    async def guild_remove(self, ctx, guild_id: int):
        values = set(await self.config.allowed_guild_ids())
        values.discard(guild_id)
        await self.config.allowed_guild_ids.set(sorted(values))
        await self.audit(ctx.author.id, "remove guild", str(guild_id))
        await ctx.tick()

    @accesscontrol.group(name="vip", invoke_without_command=True)
    async def vip_group(self, ctx):
        await ctx.send(
            f"Authority guild: {await self.config.home_guild_id()}; "
            f"VIP role: {await self.config.vip_role_id()}"
        )

    @vip_group.command(name="set")
    async def vip_set(self, ctx, home_guild_id: int, vip_role_id: int):
        await self.config.home_guild_id.set(home_guild_id)
        await self.config.vip_role_id.set(vip_role_id)
        await self.audit(
            ctx.author.id, "VIP authority", f"{home_guild_id}:{vip_role_id}"
        )
        await ctx.tick()

    @vip_group.command(name="clear")
    async def vip_clear(self, ctx):
        await self.config.home_guild_id.set(None)
        await self.config.vip_role_id.set(None)
        await self.audit(ctx.author.id, "VIP authority", "cleared")
        await ctx.tick()

    @accesscontrol.group(name="map", invoke_without_command=True)
    async def map_group(self, ctx):
        mappings = await self.config.command_mappings()
        lines = [
            f"{target} -> {capability}"
            for target, capability in sorted(mappings.items())
        ]
        await ctx.send("\n".join(lines) if lines else "No protected targets.")

    @map_group.command(name="set")
    async def map_set(self, ctx, target_type: str, target: str, capability: str):
        try:
            key = normalize_target(target_type, target)
            capability = normalize_capability(capability)
        except ValueError as error:
            await ctx.send(str(error))
            return
        mappings = await self.config.command_mappings()
        mappings[key] = capability
        await self.config.command_mappings.set(mappings)
        await self.audit(ctx.author.id, "map target", f"{key}={capability}")
        await ctx.tick()

    @map_group.command(name="remove")
    async def map_remove(self, ctx, target_type: str, *, target: str):
        try:
            key = normalize_target(target_type, target)
        except ValueError as error:
            await ctx.send(str(error))
            return
        mappings = await self.config.command_mappings()
        existed = mappings.pop(key, None)
        await self.config.command_mappings.set(mappings)
        if existed:
            await self.audit(ctx.author.id, "unmap target", key)
        await ctx.send("Mapping removed." if existed else "That mapping does not exist.")

    async def _change_grant(self, guild, capability, kind, object_id, add):
        capability = normalize_capability(capability)
        grants = await self.config.guild(guild).capability_grants()
        grant = grants.setdefault(capability, {"roles": [], "users": []})
        values = {int(value) for value in grant.get(kind, [])}
        values.add(object_id) if add else values.discard(object_id)
        grant[kind] = sorted(values)
        if not grant.get("roles") and not grant.get("users"):
            grants.pop(capability, None)
        await self.config.guild(guild).capability_grants.set(grants)
        return capability

    @accesscontrol.group(name="grant", invoke_without_command=True)
    @commands.guild_only()
    async def grant_group(self, ctx):
        grants = await self.config.guild(ctx.guild).capability_grants()
        await ctx.send(
            "\n".join(
                f"{cap}: {len(data.get('roles', []))} roles, "
                f"{len(data.get('users', []))} users"
                for cap, data in sorted(grants.items())
            ) or "No local grants."
        )

    @grant_group.command(name="role")
    async def grant_role(self, ctx, capability: str, role: discord.Role):
        capability = await self._change_grant(
            ctx.guild, capability, "roles", role.id, True
        )
        await self.audit(
            ctx.author.id, "grant role", f"{ctx.guild.id}:{capability}:{role.id}"
        )
        await ctx.tick()

    @grant_group.command(name="user")
    async def grant_user(self, ctx, capability: str, member: discord.Member):
        capability = await self._change_grant(
            ctx.guild, capability, "users", member.id, True
        )
        await self.audit(
            ctx.author.id, "grant user", f"{ctx.guild.id}:{capability}:{member.id}"
        )
        await ctx.tick()

    @accesscontrol.group(name="revoke", invoke_without_command=True)
    @commands.guild_only()
    async def revoke_group(self, ctx):
        await ctx.send("Choose role or user, followed by capability and target.")

    @revoke_group.command(name="role")
    async def revoke_role(self, ctx, capability: str, role: discord.Role):
        capability = await self._change_grant(
            ctx.guild, capability, "roles", role.id, False
        )
        await self.audit(
            ctx.author.id, "revoke role", f"{ctx.guild.id}:{capability}:{role.id}"
        )
        await ctx.tick()

    @revoke_group.command(name="user")
    async def revoke_user(self, ctx, capability: str, member: discord.Member):
        capability = await self._change_grant(
            ctx.guild, capability, "users", member.id, False
        )
        await self.audit(
            ctx.author.id, "revoke user", f"{ctx.guild.id}:{capability}:{member.id}"
        )
        await ctx.tick()

    @accesscontrol.group(name="entitlement", invoke_without_command=True)
    async def entitlement_group(self, ctx):
        records = await self.config.guild_entitlements()
        lines = []
        for guild_id, raw in sorted(records.items()):
            record = normalize_entitlement(raw)
            state = "invalid" if record is None else (
                "permanent" if not record["expires_at"] else
                f"expires {record['expires_at']}"
            )
            capabilities = "invalid" if record is None else ", ".join(record["capabilities"])
            lines.append(f"{guild_id}: {capabilities}; {state}")
        await ctx.send("\n".join(lines) if lines else "No guild entitlements.")

    @entitlement_group.command(name="grant")
    async def entitlement_grant(
        self, ctx, guild_id: int, capability: str, days: int = 0,
        sponsor_user_id: int = 0
    ):
        if not 0 <= days <= 3650:
            await ctx.send("Days must be from 0 through 3650.")
            return
        try:
            capability = normalize_capability(capability, allow_wildcard=True)
        except ValueError as error:
            await ctx.send(str(error))
            return
        records = await self.config.guild_entitlements()
        existing = normalize_entitlement(records.get(str(guild_id))) or {
            "capabilities": [], "expires_at": 0, "sponsor_user_id": None,
            "issued_at": 0, "issued_by": 0,
        }
        existing["capabilities"] = sorted(
            {*existing["capabilities"], capability}
        )
        existing["expires_at"] = int(time.time() + days * 86400) if days else 0
        existing["sponsor_user_id"] = (
            sponsor_user_id or existing.get("sponsor_user_id")
        )
        existing["issued_at"] = int(time.time())
        existing["issued_by"] = ctx.author.id
        records[str(guild_id)] = existing
        await self.config.guild_entitlements.set(records)
        await self.audit(
            ctx.author.id,
            "grant entitlement",
            f"{guild_id}:{capability}:{days}:{sponsor_user_id}",
        )
        await ctx.tick()

    @entitlement_group.command(name="revoke")
    async def entitlement_revoke(
        self, ctx, guild_id: int, capability: str = "all"
    ):
        records = await self.config.guild_entitlements()
        record = normalize_entitlement(records.get(str(guild_id)))
        if record is None:
            await ctx.send("That guild has no valid entitlement.")
            return
        if capability.lower() == "all":
            records.pop(str(guild_id), None)
        else:
            try:
                capability = normalize_capability(
                    capability, allow_wildcard=True
                )
            except ValueError as error:
                await ctx.send(str(error))
                return
            record["capabilities"] = [
                value for value in record["capabilities"] if value != capability
            ]
            if record["capabilities"]:
                records[str(guild_id)] = record
            else:
                records.pop(str(guild_id), None)
        await self.config.guild_entitlements.set(records)
        await self.audit(
            ctx.author.id, "revoke entitlement", f"{guild_id}:{capability}"
        )
        await ctx.tick()

    @accesscontrol.command()
    async def prune(self, ctx):
        records = await self.config.guild_entitlements()
        now = int(time.time())
        remove = [
            key for key, value in records.items()
            if normalize_entitlement(value) is None
            or (
                normalize_entitlement(value)["expires_at"]
                and normalize_entitlement(value)["expires_at"] <= now
            )
        ]
        for key in remove:
            records.pop(key, None)
        await self.config.guild_entitlements.set(records)
        await self.audit(ctx.author.id, "prune", str(len(remove)))
        await ctx.send(f"Pruned {len(remove)} invalid or expired records.")

    @accesscontrol.command(name="check")
    @commands.guild_only()
    async def check_command(
        self, ctx, capability: str, member: Optional[discord.Member] = None
    ):
        decision = await self.has_capability(
            ctx.guild, member or ctx.author, capability
        )
        detail = decision.source if decision.allowed else decision.reason
        await ctx.send(f"{'Allowed' if decision.allowed else 'Denied'}: {detail}")

    @accesscontrol.command(name="audit")
    async def audit_command(self, ctx, count: int = 10):
        count = max(1, min(count, 25))
        entries = (await self.config.audit_log())[-count:]
        lines = [
            f"{entry.get('timestamp')}: {entry.get('action')} "
            f"({entry.get('detail')}) by {entry.get('actor_id') or 'deleted user'}"
            for entry in entries
        ]
        await ctx.send("\n".join(lines) if lines else "No audit entries.")
