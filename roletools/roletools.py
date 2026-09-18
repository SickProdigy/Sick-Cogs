import asyncio
from copy import deepcopy
from abc import ABC
from typing import Any, Dict, List, Optional, Union

import discord
from red_commons.logging import getLogger
from redbot.core import Config, bank, commands
from redbot.core.bot import Red
from redbot.core.commands import Context
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils import AsyncIter, bounded_gather
from redbot.core.utils.chat_formatting import humanize_list, humanize_timedelta
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import ReactionPredicate

from .abc import RoleToolsMixin
from .buttons import RoleToolsButtons
from .converter import RawUserIds, RoleHierarchyConverter, SelfRoleConverter
from .events import RoleToolsEvents
from .exclusive import RoleToolsExclusive
from .inclusive import RoleToolsInclusive
from .menus import BaseMenu, ConfirmView, EmbedPages, RolePages
from .messages import RoleToolsMessages
from .picker import RoleToolsPicker
from .reactions import RoleToolsReactions
from .requires import RoleToolsRequires
from .select import RoleToolsSelect
from .setup import RoleToolsSetup
from .settings import RoleToolsSettings
from .temprole import RoleToolsTemporary

roletools = RoleToolsMixin.roletools

LEGACY_CONFIG_IDENTIFIER = 218773382617890828
SICK_COGS_V1_CONFIG_IDENTIFIER = 7194820561938472611
SICK_COGS_CONFIG_IDENTIFIER = 7194820561938472612
ROLETOOLS_SCHEMA_VERSION = 2
GUILD_DEFAULTS = {"reaction_roles": {}, "auto_roles": [], "atomic": None, "buttons": {}, "select_options": {}, "select_menus": {}, "pickers": {}, "restricted_roles": [], "temporary_roles": [], "notification_channel": None, "MIGRATION_REVIEW": []}
ROLE_DEFAULTS = {"sticky": False, "auto": False, "reactions": [], "buttons": [], "select_options": [], "selfassignable": False, "selfremovable": False, "exclusive_to": [], "inclusive_with": [], "required": [], "require_any": False, "cost": 0, "duration": None}
MEMBER_DEFAULTS = {"sticky_roles": []}
ADVANCED_CATALOG_KEYS = ("cost", "duration", "required", "exclusive_to", "inclusive_with")

log = getLogger("red.Sick-Cogs.RoleTools")
_ = Translator("RoleTools", __file__)


class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """
    This allows the metaclass used for proper type detection to
    coexist with discord.py's metaclass
    """

    pass


def custom_cooldown(ctx: commands.Context) -> Optional[discord.app_commands.Cooldown]:
    who = ctx.args[3:]
    members = []

    for entity in who:
        log.debug("custom_cooldown entity: %s", entity)
        if isinstance(entity, discord.TextChannel) or isinstance(entity, discord.Role):
            members += entity.members
        elif isinstance(entity, discord.Member):
            members.append(entity)
        else:
            if entity not in ["everyone", "here", "bots", "humans"]:
                continue
            elif entity == "everyone":
                members = ctx.guild.members
                break
            elif entity == "here":
                members += [m for m in ctx.guild.members if str(m.status) == "online"]
            elif entity == "bots":
                members += [m for m in ctx.guild.members if m.bot]
            elif entity == "humans":
                members += [m for m in ctx.guild.members if not m.bot]
    members = list(set(members))
    log.debug("Returning cooldown of 1 per %s", min(len(members) * 10, 3600))
    return discord.app_commands.Cooldown(1, min(len(members) * 10, 3600))


@cog_i18n(_)
class RoleTools(
    RoleToolsEvents,
    RoleToolsButtons,
    RoleToolsExclusive,
    RoleToolsInclusive,
    RoleToolsMessages,
    RoleToolsPicker,
    RoleToolsReactions,
    RoleToolsRequires,
    RoleToolsSettings,
    RoleToolsSelect,
    RoleToolsSetup,
    RoleToolsTemporary,
    commands.Cog,
    metaclass=CompositeMetaClass,
):
    """
    Role related tools for moderation
    """

    __author__ = ["SickProdigy", "TrustyJAID"]
    __version__ = "1.12.11"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=SICK_COGS_CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_global(
            version="0.0.0", atomic=True, enable_slash=False, schema_version=0,
            legacy_migration={"state": "not_started", "review": []},
        )
        self.config.register_guild(**GUILD_DEFAULTS)
        self.config.register_role(**ROLE_DEFAULTS)
        self.config.register_member(**MEMBER_DEFAULTS)
        self.settings: Dict[int, Any] = {}
        self._ready: asyncio.Event = asyncio.Event()
        self.views: Dict[int, Dict[str, discord.ui.View]] = {}
        self.layouts: Dict[int, Dict[str, discord.ui.LayoutView]] = {}
        self.picker_views: List[discord.ui.View] = []
        self._role_transaction_locks: Dict[tuple, asyncio.Lock] = {}
        self._repo = ""
        self._commit = ""
        self.is_discord: bool = discord.utils.oauth_url("").startswith("https://discord.com/")

    def cog_check(self, ctx: commands.Context) -> bool:
        return self._ready.is_set()

    def format_help_for_context(self, ctx: commands.Context) -> str:
        """
        Thanks Sinbad!
        """
        pre_processed = super().format_help_for_context(ctx)
        ret = f"{pre_processed}\n\n- Cog Version: {self.__version__}\n"
        # we'll only have a repo if the cog was installed through Downloader at some point
        if self._repo:
            ret += f"- Repo: {self._repo}\n"
        # we should have a commit if we have the repo but just incase
        if self._commit:
            ret += f"- Commit: [{self._commit[:9]}]({self._repo}/tree/{self._commit})"
        return ret

    async def add_cog_to_dev_env(self):
        await self.bot.wait_until_red_ready()
        if self.bot.owner_ids and 218773382617890828 in self.bot.owner_ids:
            try:
                self.bot.add_dev_env_value("roletools", lambda x: self)
            except Exception:
                pass

    async def _get_commit(self):
        downloader = self.bot.get_cog("Downloader")
        if not downloader:
            return
        cogs = await downloader.installed_cogs()
        for cog in cogs:
            if cog.name == "roletools":
                if cog.repo is not None:
                    self._repo = cog.repo.clean_url
                self._commit = cog.commit

    async def load_views(self):
        await self.bot.wait_until_red_ready()
        await self._migrate_role_catalogs()
        self.settings = await self.config.all_guilds()
        try:
            await self.initialize_select()
        except Exception:
            log.exception("Error initializing Select")
        try:
            await self.initialize_buttons()
        except Exception:
            log.exception("Error initializing Buttons")
        try:
            await self.register_picker_views()
        except Exception:
            log.exception("Error initializing role picker cards")
        for guild_id, guild_views in self.views.items():
            for msg_ids, view in guild_views.items():
                log.debug("Adding view %r to %s", view, guild_id)
                channel_id, message_id = msg_ids.split("-")
                self.bot.add_view(view, message_id=int(message_id))
                # These should be unique messages containing views
                # and we should track them seperately
        self._ready.set()

    @staticmethod
    def _normalise(defaults, data, scope):
        data = data if isinstance(data, dict) else {}
        review = []
        unknown = sorted(set(data).difference(defaults))
        if unknown:
            review.append(f"{scope}: unrecognized keys: {', '.join(unknown)}")
        result = deepcopy(defaults)
        for key, default in defaults.items():
            value = data.get(key, deepcopy(default))
            if isinstance(default, dict) and isinstance(value, dict):
                merged = deepcopy(default)
                merged.update(value)
                value = merged
            elif value is not None and isinstance(default, list) and not isinstance(value, list):
                review.append(f"{scope}.{key}: malformed value")
                value = deepcopy(default)
            result[key] = value
        return result, review

    async def _migrate_legacy_config(self):
        """Copy the best available prior RoleTools namespace into this generation."""
        if await self.config.schema_version() >= 1:
            return
        target_data = await self.config.all_guilds()
        state = await self.config.legacy_migration()
        if target_data and state.get("state") != "migrating":
            await self.config.legacy_migration.set({
                "state": "needs_owner_review",
                "review": ["Target namespace already has guild data."],
            })
            return

        sources = (
            (SICK_COGS_V1_CONFIG_IDENTIFIER, "Sick-Cogs RoleTools main"),
            (LEGACY_CONFIG_IDENTIFIER, "external RoleTools"),
        )
        source = None
        source_id = None
        source_name = None
        source_guilds = source_roles = source_members = None
        for candidate_id, candidate_name in sources:
            candidate = Config.get_conf(None, identifier=candidate_id, cog_name="RoleTools")
            guilds = await candidate.all_guilds()
            roles = await candidate.all_roles()
            members = await candidate.all_members()
            if guilds or roles or members:
                source = candidate
                source_id = candidate_id
                source_name = candidate_name
                source_guilds, source_roles, source_members = guilds, roles, members
                break

        await self.config.legacy_migration.set({"state": "migrating", "review": []})
        review = []
        imported_guilds = imported_roles = imported_members = 0
        if source is not None:
            source_global = await source.all()
            for key in ("version", "atomic", "enable_slash"):
                if key in source_global:
                    await getattr(self.config, key).set(deepcopy(source_global[key]))
            for guild_id, data in source_guilds.items():
                value, notes = self._normalise(GUILD_DEFAULTS, data, f"guild {guild_id}")
                await self.config.guild_from_id(int(guild_id)).set(value)
                review.extend(notes)
                imported_guilds += 1
            for role_id, data in source_roles.items():
                value, notes = self._normalise(ROLE_DEFAULTS, data, f"role {role_id}")
                await self.config.role_from_id(int(role_id)).set(value)
                review.extend(notes)
                imported_roles += 1
            for guild_id, members in source_members.items():
                for member_id, data in members.items():
                    value, notes = self._normalise(MEMBER_DEFAULTS, data, f"member {member_id}")
                    await self.config.member_from_ids(int(guild_id), int(member_id)).set(value)
                    review.extend(notes)
                    imported_members += 1

        # Only the external namespace predates the separate StickyRoles and
        # Autorole imports. Sick-Cogs v1 already contains their imported state.
        if source_id == LEGACY_CONFIG_IDENTIFIER:
            sticky = Config.get_conf(None, identifier=1358454876, cog_name="StickyRoles")
            for guild_id, data in (await sticky.all_guilds()).items():
                for role_id in data.get("sticky_roles", []):
                    await self.config.role_from_id(int(role_id)).sticky.set(True)
            autorole = Config.get_conf(None, identifier=45463543548, cog_name="Autorole")
            for guild_id, data in (await autorole.all_guilds()).items():
                if data.get("ENABLED", True) and data.get("AGREE_CHANNEL") is None:
                    for role_id in data.get("ROLE", []):
                        await self.config.role_from_id(int(role_id)).auto.set(True)
                        async with self.config.guild_from_id(int(guild_id)).auto_roles() as roles:
                            if int(role_id) not in roles:
                                roles.append(int(role_id))

        await self.config.schema_version.set(1)
        status = {
            "state": "completed",
            "review": review,
            "source_identifier": source_id,
            "source_name": source_name,
            "imported_guilds": imported_guilds,
            "imported_roles": imported_roles,
            "imported_members": imported_members,
        }
        await self.config.legacy_migration.set(status)
        log.info(
            "Imported RoleTools configuration from %s (%s): %s guilds, %s roles, %s members; %s warnings",
            source_name or "no prior namespace", source_id, imported_guilds,
            imported_roles, imported_members, len(review),
        )

    @staticmethod
    def _uses_advanced_role_rules(data: dict) -> bool:
        return any(data.get(key) for key in ADVANCED_CATALOG_KEYS)

    @classmethod
    def _partition_role_catalog(cls, stored_roles: dict, live_role_ids, existing_advanced):
        live_role_ids = {int(role_id) for role_id in live_role_ids}
        advanced = {int(role_id) for role_id in existing_advanced if int(role_id) in live_role_ids}
        configured = {
            int(role_id) for role_id, data in stored_roles.items()
            if int(role_id) in live_role_ids
            and (data.get("selfassignable") or data.get("selfremovable")
                 or cls._uses_advanced_role_rules(data))
        }
        advanced.update(
            role_id for role_id in configured
            if cls._uses_advanced_role_rules(stored_roles[role_id])
        )
        return configured - advanced, advanced

    async def _migrate_role_catalogs(self) -> None:
        """Convert schema 1 role settings into the shared ordinary/Advanced catalogs."""
        schema = await self.config.schema_version()
        if schema < 1 or schema >= ROLETOOLS_SCHEMA_VERSION:
            return

        stored_roles = {
            int(role_id): data for role_id, data in (await self.config.all_roles()).items()
        }
        admin = self.bot.get_cog("Admin")
        for guild in self.bot.guilds:
            live_role_ids = {role.id for role in guild.roles}
            ordinary, advanced = self._partition_role_catalog(
                stored_roles,
                live_role_ids,
                await self.config.guild(guild).restricted_roles(),
            )

            if admin is None:
                # Preserve access through RoleTools without risking native selfrole
                # bypass of paid or otherwise controlled roles.
                advanced.update(ordinary)
                ordinary.clear()
                async with self.config.guild(guild).MIGRATION_REVIEW() as notes:
                    note = "Admin cog was unavailable; existing self-roles were kept as Advanced roles."
                    if note not in notes:
                        notes.append(note)
            else:
                admin_roles = [
                    int(role_id) for role_id in await admin.config.guild(guild).selfroles()
                    if int(role_id) in live_role_ids and int(role_id) not in advanced
                ]
                admin_roles.extend(role_id for role_id in ordinary if role_id not in admin_roles)
                await admin.config.guild(guild).selfroles.set(admin_roles)

            await self.config.guild(guild).restricted_roles.set(sorted(advanced))
            log.info(
                "Migrated RoleTools catalogs in guild %s: %s ordinary, %s Advanced",
                guild.id, len(ordinary), len(advanced),
            )

        await self.config.schema_version.set(ROLETOOLS_SCHEMA_VERSION)

    async def cog_load(self) -> None:
        await self._migrate_legacy_config()
        self.temporary_roles_task.start()
        loop = asyncio.get_running_loop()
        loop.create_task(self.load_views())
        loop.create_task(self.add_cog_to_dev_env())
        loop.create_task(self._get_commit())

    async def cog_unload(self):
        for views in self.views.values():
            for view in views.values():
                # Don't forget to remove persistent views when the cog is unloaded.
                log.debug("Stopping view %s", view)
                view.stop()
        for view in self.picker_views:
            log.debug("Stopping picker view %s", view)
            view.stop()
        self.picker_views.clear()
        try:
            self.bot.remove_dev_env_value("roletools")
        except Exception:
            pass
        self.temporary_roles_task.cancel()

    async def confirm_selfassignable(
        self, ctx: commands.Context, roles: List[discord.Role]
    ) -> None:
        not_assignable = [r for r in roles if not await self.config.role(r).selfassignable()]
        if not_assignable:
            role_list = "\n".join(f"- {role.mention}" for role in not_assignable)
            msg_str = _(
                "The following roles are not self-assignable:\n{roles}\n"
                "Would you liked to make them self-assignable and self-removable?"
            ).format(
                roles=role_list,
            )
            if self.is_discord:
                pred = ConfirmView(ctx.author)
                pred.message = await ctx.send(
                    msg_str, view=pred, allowed_mentions=discord.AllowedMentions(roles=False)
                )
                await pred.wait()
            else:
                msg = await ctx.send(
                    msg_str, allowed_mentions=discord.AllowedMentions(roles=False)
                )
                start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(msg, ctx.author)
                await ctx.bot.wait_for("reaction_add", check=pred)

            if pred.result:
                for role in not_assignable:
                    await self.config.role(role).selfassignable.set(True)
                    await self.config.role(role).selfremovable.set(True)
                await ctx.channel.send(
                    _(
                        "The following roles have been made self-assignable and self-removeable:\n{roles}"
                    ).format(roles=role_list)
                )
            else:
                await ctx.channel.send(
                    _("Okay I won't make the following roles self-assignable:\n{roles}").format(
                        roles=role_list
                    )
                )

    @roletools.command(name="migrationstatus")
    @commands.is_owner()
    async def roletools_migration_status(self, ctx: Context) -> None:
        """Owner migration status."""
        status = await self.config.legacy_migration()
        await ctx.send(
            f"**RoleTools migration status**\nState: `{status.get('state')}`"
            f"\nSource: `{status.get('source_name') or 'None'}`"
            f"\nImported: `{status.get('imported_guilds', 0)}` guilds, "
            f"`{status.get('imported_roles', 0)}` roles, `{status.get('imported_members', 0)}` members"
            f"\nReview warnings: `{len(status.get('review', []))}`"
            f"\nSchema version: `{await self.config.schema_version()}`"
        )

    @roletools.command(name="adminhelp", aliases=["setuphelp"])
    @commands.admin_or_permissions(manage_roles=True)
    async def roletools_admin_help(self, ctx: Context) -> None:
        """Manager command guide."""
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="RoleTools setup guide",
            description="Start by allowing a role, then give members a clear way to choose it.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="1. Allow a self-role",
            value=f"`{prefix}roletools selfassignable true @Role`\n`{prefix}roletools selfremovable true @Role`",
            inline=False,
        )
        embed.add_field(
            name="2. Choose the member experience",
            value=(f"Members can browse with `{prefix}roletools viewroles` and toggle with "
                   f"`{prefix}roletools selfrole @Role`, or you can configure "
                   f"reaction roles, buttons, or select menus with `{prefix}help roletools buttons`."),
            inline=False,
        )
        embed.add_field(
            name="3. Interactive setup",
            value=(f"`{prefix}roletools setup` manages Red self-roles, advanced self-roles, and the "
                   "published member card without internal option names."),
            inline=False,
        )
        embed.add_field(
            name="4. Optional role-change notices",
            value=(f"`{prefix}roletools notify channel #role-log` posts successful reaction, "
                   "button, and select role changes there. Use "
                   f"`{prefix}roletools notify disable` to stop them."),
            inline=False,
        )
        embed.set_footer(text=f"More detail: {prefix}help roletools <command>")
        await ctx.send(embed=embed)

    @roletools.command()
    @commands.guild_only()
    @commands.bot_has_permissions(manage_roles=True)
    async def selfrole(self, ctx: Context, *, role: SelfRoleConverter) -> None:
        """
        Toggle a self-role.

        `<role>` accepts a role mention, ID, or name. If you already have the
        role, it is removed; otherwise it is added when server rules allow it.

        Use `[p]roletools viewroles` to see available self-roles first.
        """
        if role not in ctx.author.roles:
            await self.selfrole_add(ctx, role=role)
        else:
            await self.selfrole_remove(ctx, role=role)

    async def selfrole_add(self, ctx: Context, *, role: discord.Role) -> None:
        """
        Give yourself a role

        `<role>` The role you want to give yourself
        """
        await ctx.typing()
        author: discord.Member = ctx.author

        if not await self.config.role(role).selfassignable():
            msg = _("The {role} role is not currently selfassignable.").format(role=role.mention)
            await ctx.send(msg)
            return
        response = await self.give_roles(author, [role], _("Selfrole command."))
        if response:
            msg = _("I could not assign that role for the following reasons:\n")
            for r in response:
                msg += r.reason
            await ctx.send(msg)
            return
        await self.notify_role_change(author, role, "received")
        msg = _("You have been given the {role} role.").format(role=role.mention)
        await ctx.send(msg)

    async def selfrole_remove(self, ctx: Context, *, role: discord.Role) -> None:
        """
        Remove a role from yourself

        `<role>` The role you want to remove.
        """
        await ctx.typing()
        author: discord.Member = ctx.author

        if not await self.config.role(role).selfremovable():
            msg = _("The {role} role is not currently self-removable.").format(role=role.mention)
            await ctx.send(msg)
            return
        response = await self.remove_roles(author, [role], _("Selfrole command."))
        if response:
            msg = _("I could not remove that role for the following reasons:\n")
            msg += "".join(item.reason for item in response)
            await ctx.send(msg)
            return
        await self.notify_role_change(author, role, "removed")
        msg = _("The {role} role has been removed from you.").format(role=role.mention)
        await ctx.send(msg)

    @roletools.command(cooldown_after_parsing=True, with_app_command=False)
    @commands.bot_has_permissions(manage_roles=True)
    @commands.admin_or_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    @commands.dynamic_cooldown(custom_cooldown, commands.BucketType.guild)
    async def giverole(
        self,
        ctx: Context,
        role: RoleHierarchyConverter,
        *who: Union[discord.Role, discord.TextChannel, discord.Thread, discord.Member, str],
    ) -> None:
        """
        Give a role in bulk.

        `<role>` The role you want to give.
        `[who...]` Who you want to give the role to. This can include any of the following:```diff
        + Member
            A specified member of the server.
        + Role
            People who already have a specified role.
        + TextChannel
            People who have access to see the channel provided.
        Or one of the following:
        + everyone - everyone in the server.
        + here     - everyone who appears online in the server.
        + bots     - all the bots in the server.
        + humans   - all the humans in the server.
        ```
        **Note:** This runs through exclusive and inclusive role checks
        which may cause unintended roles to be removed/applied.

        **This command is on a cooldown of 10 seconds per member who receives
        a role up to a maximum of 1 hour.**
        """
        await ctx.typing()

        if len(who) == 0:
            await ctx.send_help()
            ctx.command.reset_cooldown(ctx)
            return
        async with ctx.typing():
            members = []
            for entity in who:
                if isinstance(entity, discord.Thread):
                    try:
                        thread_members = await entity.fetch_members()
                        for m in thread_members:
                            if mem := ctx.guild.get_member(m.id):
                                members.append(mem)
                    except Exception:
                        log.error("Could not find members of thread in %s", entity)

                elif isinstance(entity, discord.TextChannel) or isinstance(entity, discord.Role):
                    members += entity.members
                elif isinstance(entity, discord.Member):
                    members.append(entity)
                else:
                    if entity not in ["everyone", "here", "bots", "humans"]:
                        msg = _("`{who}` cannot have roles assigned to them.").format(who=entity)
                        await ctx.send(msg)
                        ctx.command.reset_cooldown(ctx)
                        return
                    elif entity == "everyone":
                        members = ctx.guild.members
                        break
                    elif entity == "here":
                        members += [
                            m
                            async for m in AsyncIter(ctx.guild.members, steps=500)
                            if str(m.status) == "online"
                        ]
                    elif entity == "bots":
                        members += [
                            m async for m in AsyncIter(ctx.guild.members, steps=500) if m.bot
                        ]
                    elif entity == "humans":
                        members += [
                            m async for m in AsyncIter(ctx.guild.members, steps=500) if not m.bot
                        ]
            members = list(set(members))
            tasks = []
            async for m in AsyncIter(members, steps=500):
                if m.top_role >= ctx.me.top_role or role in m.roles:
                    continue
                # tasks.append(m.add_roles(role, reason=_("Roletools Giverole command")))
                tasks.append(
                    self.give_roles(
                        m, [role], _("Roletools Giverole command"), check_cost=False, atomic=False
                    )
                )
            await bounded_gather(*tasks)
        added_to = humanize_list([getattr(en, "name", en) for en in who])
        msg = _("Added {role} to {added}.").format(role=role.mention, added=added_to)
        await ctx.send(msg)

    @roletools.command(with_app_command=False)
    @commands.bot_has_permissions(manage_roles=True)
    @commands.admin_or_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    @commands.dynamic_cooldown(custom_cooldown, commands.BucketType.guild)
    async def removerole(
        self,
        ctx: Context,
        role: RoleHierarchyConverter,
        *who: Union[discord.Role, discord.TextChannel, discord.Member, str],
    ) -> None:
        """
        Remove a role in bulk.

        `<role>` The role you want to give.
        `[who...]` Who you want to give the role to. This can include any of the following:```diff
        + Member
            A specified member of the server.
        + Role
            People who already have a specified role.
        + TextChannel
            People who have access to see the channel provided.
        Or one of the following:
        + everyone - everyone in the server.
        + here     - everyone who appears online in the server.
        + bots     - all the bots in the server.
        + humans   - all the humans in the server.
        ```
        **Note:** This runs through exclusive and inclusive role checks
        which may cause unintended roles to be removed/applied.

        **This command is on a cooldown of 10 seconds per member who receives
        a role up to a maximum of 1 hour.**
        """
        await ctx.typing()

        if len(who) == 0:
            return await ctx.send_help()
        async with ctx.typing():
            members = []
            for entity in who:
                if isinstance(entity, discord.TextChannel) or isinstance(entity, discord.Role):
                    members += entity.members
                elif isinstance(entity, discord.Member):
                    members.append(entity)
                else:
                    if entity not in ["everyone", "here", "bots", "humans"]:
                        msg = _("`{who}` cannot have roles removed from them.").format(who=entity)
                        await ctx.send(msg)
                        ctx.command.reset_cooldown(ctx)
                        return
                    elif entity == "everyone":
                        members = ctx.guild.members
                        break
                    elif entity == "here":
                        members += [
                            m
                            async for m in AsyncIter(ctx.guild.members, steps=500)
                            if str(m.status) == "online"
                        ]
                    elif entity == "bots":
                        members += [
                            m async for m in AsyncIter(ctx.guild.members, steps=500) if m.bot
                        ]
                    elif entity == "humans":
                        members += [
                            m async for m in AsyncIter(ctx.guild.members, steps=500) if not m.bot
                        ]
            members = list(set(members))
            tasks = []
            async for m in AsyncIter(members, steps=500):
                if m.top_role >= ctx.me.top_role or role not in m.roles:
                    continue
                # tasks.append(m.add_roles(role, reason=_("Roletools Giverole command")))
                tasks.append(
                    self.remove_roles(m, [role], _("Roletools Removerole command"), atomic=False)
                )
            await bounded_gather(*tasks)
        removed_from = humanize_list([getattr(en, "name", en) for en in who])
        msg = _("Removed the {role} from {removed}.").format(
            role=role.mention, removed=removed_from
        )
        await ctx.send(msg)

    @roletools.command()
    @commands.admin_or_permissions(manage_roles=True)
    async def forcerole(
        self,
        ctx: Context,
        users: commands.Greedy[Union[discord.Member, RawUserIds]],
        *,
        role: RoleHierarchyConverter,
    ) -> None:
        """
        Force a sticky role.

        `<users>` The users you want to have a forced stickyrole applied to.
        `<roles>` The role you want to set.

        Note: The only way to remove this would be to manually remove the role from
        the user.
        """
        await ctx.typing()
        errors = []
        for user in users:
            if isinstance(user, int):
                async with self.config.member_from_ids(
                    ctx.guild.id, user
                ).sticky_roles() as setting:
                    if role.id not in setting:
                        setting.append(role.id)
            elif isinstance(user, discord.Member):
                async with self.config.member(user).sticky_roles() as setting:
                    if role.id not in setting:
                        setting.append(role.id)
                try:
                    await self.give_roles(user, [role], reason=_("Forced Sticky Role"))
                except discord.HTTPException:
                    errors.append(
                        _("There was an error force applying the role to {user}.\n").format(
                            user=user
                        )
                    )
        msg = _("{users} will have the role {role} force applied to them.").format(
            users=humanize_list(users), role=role.name
        )
        await ctx.send(msg)
        if errors:
            await ctx.channel.send("".join([e for e in errors]))

    @roletools.command()
    @commands.admin_or_permissions(manage_roles=True)
    async def forceroleremove(
        self,
        ctx: Context,
        users: commands.Greedy[Union[discord.Member, RawUserIds]],
        *,
        role: RoleHierarchyConverter,
    ) -> None:
        """
        Remove a forced sticky role.

        `<users>` The users you want to have a forced stickyrole applied to.
        `<roles>` The role you want to set.

        Note: This is generally only useful for users who have left the server.
        """
        await ctx.typing()

        errors = []
        for user in users:
            if isinstance(user, int):
                async with self.config.member_from_ids(
                    ctx.guild.id, user
                ).sticky_roles() as setting:
                    if role in setting:
                        setting.remove(role.id)
            elif isinstance(user, discord.Member):
                async with self.config.member(user).sticky_roles() as setting:
                    if role.id in setting:
                        setting.append(role.id)
                try:
                    await self.remove_roles(user, [role], reason=_("Force removed sticky role"))
                except discord.HTTPException:
                    errors.append(
                        _("There was an error force removing the role from {user}.\n").format(
                            user=user
                        )
                    )
        msg = _("{users} will have the role {role} force removed from them.").format(
            users=humanize_list(users), role=role.name
        )
        await ctx.send(msg)
        if errors:
            await ctx.channel.send("".join([e for e in errors]))

    @commands.command(name="selfroles")
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def selfroles_shortcut(self, ctx: Context, *, selection: Optional[str] = None) -> None:
        """List available self-roles.

        This is a shortcut for `[p]roletools viewroles`. An optional role mention,
        ID, or name filters the list to that role.
        """
        await type(self).viewroles.callback(self, ctx, selection=selection)

    @roletools.command(aliases=["viewrole"])
    @commands.bot_has_permissions(embed_links=True)
    async def viewroles(self, ctx: Context, *, selection: Optional[str] = None) -> None:
        """View available or configured roles.

        `[selection]` may be `available`, `configured`, or a role mention, ID, or name.
        Members see availability by default. Managers can use `configured` for the full report.
        """
        requested = (selection or "available").strip()
        mode = requested.lower()
        manager = ctx.author.guild_permissions.manage_roles
        role = None
        if mode not in {"available", "configured"}:
            role = await commands.RoleConverter().convert(ctx, requested)
            if manager:
                await BaseMenu(
                    source=RolePages(roles=[role]),
                    delete_message_after=False,
                    clear_reactions_after=True,
                    timeout=60,
                    cog=self,
                ).start(ctx=ctx)
                return
            mode = "available"

        if mode == "configured" and not manager:
            await ctx.send(
                f"The configured-role report requires Manage Roles. "
                f"Use `{ctx.clean_prefix}roletools viewroles` for available self-roles."
            )
            return

        raw_settings = await self.config.all_roles()
        settings_by_id = {int(role_id): data for role_id, data in raw_settings.items()}
        guild_roles = {item.id: item for item in ctx.guild.roles}
        pages = []

        if mode == "available":
            lines = []
            member_role_ids = {item.id for item in ctx.author.roles}
            currency = await bank.get_currency_name(ctx.guild)
            advanced_ids = set(await self.restricted_role_ids(ctx.guild))
            candidates = [role] if role else ctx.guild.roles
            for item in candidates:
                data = settings_by_id.get(item.id, {})
                can_add = bool(data.get("selfassignable")) and item.id not in member_role_ids
                can_remove = bool(data.get("selfremovable")) and item.id in member_role_ids
                if not can_add and not can_remove:
                    continue
                details = []
                blockers = []
                required_ids = {int(role_id) for role_id in data.get("required", [])}
                required = [guild_roles.get(role_id) for role_id in required_ids]
                required_mentions = [required_role.mention for required_role in required if required_role]
                conflicts = [guild_roles.get(int(role_id)) for role_id in data.get("exclusive_to", [])]
                conflicts = [conflict.mention for conflict in conflicts if conflict]
                if required_mentions:
                    qualifier = "any" if data.get("require_any") else "all"
                    details.append(f"requires {qualifier}: {humanize_list(required_mentions)}")
                    has_required = bool(member_role_ids & required_ids)
                    if not data.get("require_any"):
                        has_required = required_ids <= member_role_ids
                    if can_add and not has_required:
                        blockers.append("missing required role")
                if conflicts:
                    details.append(f"removes conflicts: {humanize_list(conflicts)}")
                if data.get("cost"):
                    details.append(f"cost: {data['cost']} {currency}")
                    if can_add and not await bank.can_spend(ctx.author, data["cost"]):
                        blockers.append("insufficient credits")
                if data.get("duration"):
                    details.append(f"temporary: {humanize_timedelta(seconds=data['duration'])}")
                if item >= ctx.guild.me.top_role:
                    blockers.append("above the bot's highest role")
                action = "can remove now" if can_remove else "can add now"
                if blockers:
                    action = f"cannot {'remove' if can_remove else 'add'} yet ({humanize_list(blockers)})"
                suffix = f" — {'; '.join(details)}" if details else ""
                label = f"{item.name} (Advanced)" if item.id in advanced_ids else item.name
                lines.append(f"**{label}** — {action}{suffix}")

            if not lines:
                await ctx.send(
                    "No self-roles are currently available to you. A server manager can configure "
                    f"them with `{ctx.clean_prefix}roletools adminhelp`."
                )
                return
            intro = (
                f"Use Red’s `{ctx.clean_prefix}selfrole @Role` for ordinary roles. "
                f"Advanced Bank/rule roles use `{ctx.clean_prefix}roletools selfrole @Role`."
            )
            title = "Available self-roles"
        else:
            lines = []
            for role_id, data in sorted(settings_by_id.items()):
                if role_id not in guild_roles:
                    continue
                if not any(data.get(key) for key in ROLE_DEFAULTS):
                    continue
                item = guild_roles.get(role_id)
                label = item.mention if item else f"Deleted role (`{role_id}`) — cleanup needed"
                flags = []
                for key, name in (
                    ("selfassignable", "self-assignable"),
                    ("selfremovable", "self-removable"),
                    ("sticky", "sticky"),
                    ("auto", "autorole"),
                ):
                    if data.get(key):
                        flags.append(name)
                if data.get("duration"):
                    flags.append(f"temporary {humanize_timedelta(seconds=data['duration'])}")
                if data.get("cost"):
                    flags.append(f"cost {data['cost']}")
                for key, name in (
                    ("required", "required"),
                    ("inclusive_with", "included"),
                    ("exclusive_to", "excluded"),
                ):
                    references = [guild_roles.get(int(value)) for value in data.get(key, [])]
                    present = [ref.mention for ref in references if ref]
                    missing = len(references) - len(present)
                    if present or missing:
                        value = humanize_list(present) if present else ""
                        if missing:
                            value = f"{value}{', ' if value else ''}{missing} deleted"
                        flags.append(f"{name}: {value}")
                for key, name in (
                    ("reactions", "reactions"),
                    ("buttons", "buttons"),
                    ("select_options", "select options"),
                ):
                    if data.get(key):
                        flags.append(f"{name}: {len(data[key])}")
                lines.append(f"**{label}** — {', '.join(flags) or 'stored configuration'}")

            if not lines:
                await ctx.send(
                    f"No RoleTools role configuration exists yet. Start with `{ctx.clean_prefix}roletools adminhelp`."
                )
                return
            intro = "Only roles with stored RoleTools settings are shown. Deleted IDs are flagged for cleanup."
            title = "RoleTools configuration"

        for start in range(0, len(lines), 10):
            embed = discord.Embed(
                title=title,
                description=f"{intro}\n\n" + "\n".join(lines[start : start + 10]),
                color=discord.Color.blurple(),
            )
            pages.append(embed)
        await BaseMenu(
            source=EmbedPages(pages),
            delete_message_after=False,
            clear_reactions_after=True,
            timeout=60,
            cog=self,
        ).start(ctx=ctx)

    # @roletools.group(name="slash")
    # @commands.admin_or_permissions(manage_guild=True)
    async def roletools_slash(self, ctx: Context) -> None:
        """
        Slash command toggling for roletools
        """
        pass

    # @roletools_slash.command(name="global")
    # @commands.is_owner()
    async def roletools_global_slash(self, ctx: Context) -> None:
        """Toggle this cog to register slash commands"""
        current = await self.config.enable_slash()
        await self.config.enable_slash.set(not current)
        verb = _("enabled") if not current else _("disabled")
        await ctx.send(_("Slash commands are {verb}.").format(verb=verb))
        if not current:
            self.bot.tree.add_command(self, override=True)
        else:
            self.bot.tree.remove_command("role-tools")
