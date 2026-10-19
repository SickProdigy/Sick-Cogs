"""Collaborative, human-approved bot update announcements."""

import asyncio
import datetime
import logging
import time

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, commands

from .collector import (
    REPOSITORY_BRANCH,
    REPOSITORY_WEB_URL,
    CommitCollectionError,
    GiteaCommitClient,
    GitHubCommitClient,
    MAX_UPSTREAM_SOURCES,
    upstream_source,
    digest_slot,
    digest_text,
)

from .models import (
    add_proposal,
    apply_proposal,
    new_draft,
    normalize_delivery_mode,
    normalize_source,
    should_deliver,
)

CONFIG_IDENTIFIER = 710284193657004821
log = logging.getLogger("red.sick-cogs.BotUpdates")
GLOBAL_DEFAULTS = {
    "review_channel_id": 0,
    "editor_user_ids": [],
    "editor_role_ids": [],
    "publisher_user_ids": [],
    "drafts": {},
    "active_draft_id": 0,
    "next_draft_id": 1,
    "announced_source_keys": [],
    "upstream_sources": {},
    "collector_enabled": False,
    "collector_last_sha": "",
    "collector_last_slot": "",
    "collector_last_check_at": 0,
    "collector_last_error": "",
}
GUILD_DEFAULTS = {
    "enabled": False,
    "channel_id": 0,
    "delivery_mode": "major-only",
    "mention_role_id": 0,
}


class BotUpdates(commands.Cog):
    """Prepare, review, approve, and distribute bot update posts."""

    __author__ = ["SickProdigy"]
    __version__ = "0.2.1"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=CONFIG_IDENTIFIER, force_registration=True
        )
        self.config.register_global(**GLOBAL_DEFAULTS)
        self.config.register_guild(**GUILD_DEFAULTS)
        self.session = None
        self._collector_lock = asyncio.Lock()

    async def cog_load(self):
        self.collector_loop.start()

    def cog_unload(self):
        self.collector_loop.cancel()
        if self.session and not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def _session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"User-Agent": "Sick-Cogs-BotUpdates/0.2.1"},
            )
        return self.session

    async def _commit_client(self):
        return GiteaCommitClient(await self._session())

    async def _unfinished_active_draft(self):
        active_id = int(await self.config.active_draft_id())
        if not active_id:
            return None
        draft = (await self.config.drafts()).get(str(active_id))
        if draft and draft.get("status") in {"draft", "approved"}:
            return draft
        return None

    async def _upstream_client(self, source):
        return GitHubCommitClient(await self._session(), source)

    async def collect_project_digest(self, *, force=False, now=None):
        """Collect every enabled source, then save the draft and cursors together."""
        async with self._collector_lock:
            now = now or datetime.datetime.now(datetime.timezone.utc)
            slot = digest_slot(now)
            if not force and not await self.config.collector_enabled():
                return "disabled", None
            if not force and await self.config.collector_last_slot() == slot:
                return "not-due", None
            if await self._unfinished_active_draft():
                return "draft-active", None
            last_sha = await self.config.collector_last_sha()
            if not last_sha:
                return "not-seeded", None
            project = await (await self._commit_client()).since(last_sha)
            commits = [dict(item, source_kind="project", source_label="Sick-Cogs") for item in project]
            upstream = await self.config.upstream_sources()
            for name, source in sorted(upstream.items()):
                if not source["enabled"]:
                    continue
                try:
                    changes = await (await self._upstream_client(source)).since(source["last_sha"])
                except CommitCollectionError as exc:
                    raise CommitCollectionError(f"Upstream {name}: {exc}") from exc
                commits.extend(dict(item, source_kind="upstream", source_label=f"Upstream: {name}") for item in changes)
                if changes:
                    source["last_sha"] = changes[0]["sha"]

            checked_at = int(now.timestamp())
            # One Config transaction prevents a saved draft with stale cursors after restart.
            async with self.config.all() as state:
                announced = set(state["announced_source_keys"])
                sources = []
                seen = set(announced)
                pending = []
                for commit in reversed(commits):
                    try:
                        source = normalize_source(
                            commit["source_kind"],
                            f"{commit['source_label']}: {commit['message']}"[:100],
                            commit["url"], commit["sha"][:12],
                        )
                    except ValueError as exc:
                        raise CommitCollectionError("A collected commit contains an invalid source reference.") from exc
                    if source["key"] not in seen:
                        seen.add(source["key"])
                        sources.append(source)
                        pending.append(commit)
                state["collector_last_check_at"] = checked_at
                state["collector_last_error"] = ""
                state["collector_last_sha"] = project[0]["sha"] if project else last_sha
                state["collector_last_slot"] = slot
                state["upstream_sources"] = upstream
                if not sources:
                    return ("already-announced" if commits else "no-changes"), None
                draft_id = int(state["next_draft_id"])
                record = new_draft(
                    draft_id, 0, "digest", f"Bot updates — {slot}",
                    digest_text(list(reversed(pending))), checked_at,
                )
                record["sources"] = sources
                state["drafts"][str(draft_id)] = record
                state["active_draft_id"] = draft_id
                state["next_draft_id"] = draft_id + 1
            await self._post_review(record, f"Scheduled digest #{draft_id} is ready for human review.")
            return "created", record

    async def collector_once(self, *, force=False, now=None):
        if not force and not await self.config.collector_enabled():
            return "disabled", None
        try:
            return await self.collect_project_digest(force=force, now=now)
        except asyncio.CancelledError:
            raise
        except CommitCollectionError as error:
            await self.config.collector_last_check_at.set(int(time.time()))
            await self.config.collector_last_error.set(str(error))
            return "error", str(error)

    @tasks.loop(hours=1)
    async def collector_loop(self):
        status, detail = await self.collector_once()
        if status == "error":
            log.warning("BotUpdates collector failed: %s", detail)

    @collector_loop.before_loop
    async def before_collector_loop(self):
        await self.bot.wait_until_red_ready()

    async def red_delete_data_for_user(self, *, requester, user_id):
        user_id = int(user_id)
        await self.config.editor_user_ids.set(
            [value for value in await self.config.editor_user_ids() if int(value) != user_id]
        )
        await self.config.publisher_user_ids.set(
            [value for value in await self.config.publisher_user_ids() if int(value) != user_id]
        )
        drafts = await self.config.drafts()
        for draft in drafts.values():
            if int(draft.get("author_id", 0)) == user_id:
                draft["author_id"] = 0
            if int(draft.get("approved_by", 0)) == user_id:
                draft["approved_by"] = 0
            for proposal in draft.get("proposals", []):
                if int(proposal.get("author_id", 0)) == user_id:
                    proposal["author_id"] = 0
            for entry in draft.get("history", []):
                if int(entry.get("changed_by", 0)) == user_id:
                    entry["changed_by"] = 0
        await self.config.drafts.set(drafts)

    async def _is_publisher(self, user):
        if await self.bot.is_owner(user):
            return True
        return int(user.id) in {
            int(value) for value in await self.config.publisher_user_ids()
        }

    async def _is_editor(self, user):
        if await self._is_publisher(user):
            return True
        if int(user.id) in {
            int(value) for value in await self.config.editor_user_ids()
        }:
            return True
        allowed_roles = {int(value) for value in await self.config.editor_role_ids()}
        return bool(
            allowed_roles.intersection(role.id for role in getattr(user, "roles", []))
        )

    async def _require_publisher(self, ctx):
        if not await self._is_publisher(ctx.author):
            raise commands.UserFeedbackCheckFailure(
                "Only the bot owner or a configured BotUpdates publisher can do that."
            )

    async def _require_editor(self, ctx):
        if not await self._is_editor(ctx.author):
            raise commands.UserFeedbackCheckFailure(
                "Only a configured BotUpdates editor or publisher can do that."
            )

    async def _active(self):
        draft_id = int(await self.config.active_draft_id())
        drafts = await self.config.drafts()
        draft = drafts.get(str(draft_id))
        if not draft:
            raise commands.UserFeedbackCheckFailure(
                "There is no active BotUpdates draft."
            )
        return drafts, str(draft_id), draft

    @staticmethod
    def _embed(draft, *, review=False):
        color = {
            "draft": discord.Color.orange(),
            "approved": discord.Color.green(),
            "published": discord.Color.blue(),
        }.get(draft.get("status"), discord.Color.light_grey())
        embed = discord.Embed(
            title=draft.get("title", "Bot updates")[:256],
            description=draft.get("text", "")[:4096],
            color=color,
        )
        sources = draft.get("sources", [])
        if sources:
            lines = [
                f"**{discord.utils.escape_markdown(source['label'])}** - "
                f"{source['kind']} "
                f"`{discord.utils.escape_markdown(source['reference'])}`\n"
                f"<{source['url']}>"
                for source in sources
            ]
            embed.add_field(
                name="What changed", value="\n".join(lines)[:1024], inline=False
            )
        if review:
            pending = sum(
                proposal.get("status") == "pending"
                for proposal in draft.get("proposals", [])
            )
            embed.add_field(
                name="Review",
                value=(
                    f"Status: **{draft.get('status', 'draft')}** | "
                    f"Type: **{draft.get('kind', 'routine')}** | "
                    f"Pending proposals: **{pending}**"
                ),
                inline=False,
            )
            embed.set_footer(text=f"BotUpdates draft #{draft.get('id', '?')}")
        else:
            embed.set_footer(text="What's new from this bot")
        return embed

    async def _post_review(self, draft, notice=""):
        channel_id = int(await self.config.review_channel_id())
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if channel is None:
            return False
        try:
            await channel.send(
                content=notice or None,
                embed=self._embed(draft, review=True),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            return False
        return True

    async def _save_active(self, drafts, key, draft, notice=""):
        drafts[key] = draft
        await self.config.drafts.set(drafts)
        await self._post_review(draft, notice)

    @commands.group(
        name="botupdates", aliases=["botupdate"], invoke_without_command=True
    )
    async def botupdates(self, ctx):
        """Review bot updates or configure this server's subscription."""
        data = await self.config.guild(ctx.guild).all() if ctx.guild else {}
        active_id = int(await self.config.active_draft_id())
        subscription = (
            data.get("delivery_mode", "major-only") if data.get("enabled") else "off"
        )
        await ctx.send(
            f"Active draft: {active_id or 'none'} | Subscription: {subscription}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @botupdates.command(name="subscribe")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def subscribe(
        self, ctx, channel: discord.TextChannel, mode: str = "major-only"
    ):
        """Subscribe this server to approved bot updates."""
        mode = normalize_delivery_mode(mode)
        permissions = channel.permissions_for(ctx.guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            raise commands.UserFeedbackCheckFailure(
                "I need Send Messages and Embed Links in that channel."
            )
        await self.config.guild(ctx.guild).enabled.set(True)
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).delivery_mode.set(mode)
        await ctx.send(f"Bot updates will use {channel.mention} in **{mode}** mode.")

    @botupdates.command(name="unsubscribe")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def unsubscribe(self, ctx):
        """Stop bot update announcements in this server."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Bot update announcements are disabled for this server.")

    @botupdates.command(name="mention")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def mention(self, ctx, role: discord.Role = None):
        """Set the one role update posts may mention, or omit it to disable mentions."""
        await self.config.guild(ctx.guild).mention_role_id.set(
            role.id if role else 0
        )
        await ctx.send(
            f"Update mention set to {role.mention}."
            if role
            else "Update mentions are disabled.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @botupdates.group(name="draft", invoke_without_command=True)
    async def draft(self, ctx):
        """Work with the active update draft."""
        await ctx.send_help()

    @draft.command(name="create")
    async def draft_create(self, ctx, kind: str, *, content: str):
        """Create a draft: create <major|digest|routine> Title | Body"""
        await self._require_publisher(ctx)
        async with self._collector_lock:
            active = await self._unfinished_active_draft()
            if active:
                raise commands.UserFeedbackCheckFailure(
                    f"Finish or publish active draft #{active.get('id', '?')} before creating another."
                )
            if " | " not in content:
                raise commands.UserFeedbackCheckFailure(
                    "Separate the title and body with ` | `."
                )
            title, text = content.split(" | ", 1)
            draft_id = int(await self.config.next_draft_id())
            record = new_draft(
                draft_id, ctx.author.id, kind, title, text, int(time.time())
            )
            async with self.config.all() as state:
                state["drafts"][str(draft_id)] = record
                state["active_draft_id"] = draft_id
                state["next_draft_id"] = draft_id + 1
            posted = await self._post_review(record, f"Draft #{draft_id} created.")
            await ctx.send(
                f"Created draft #{draft_id}."
                + ("" if posted else " Review-channel preview was not sent.")
            )

    @draft.command(name="sources")
    async def draft_sources(self, ctx, page: int = 1):
        """Review all active-draft source references, five per page."""
        await self._require_editor(ctx)
        _, _, record = await self._active()
        sources = record.get("sources", [])
        pages = max(1, (len(sources) + 4) // 5)
        if page < 1 or page > pages:
            raise commands.UserFeedbackCheckFailure(f"Choose a source page from 1 to {pages}.")
        embed = discord.Embed(title=f"Draft #{record['id']} sources — {page}/{pages}")
        for source in sources[(page - 1) * 5:page * 5]:
            embed.add_field(
                name=discord.utils.escape_markdown(source["label"]),
                value=f"{source['kind']} · `{discord.utils.escape_markdown(source['reference'])}`\n<{source['url']}>",
                inline=False,
            )
        if not sources:
            embed.description = "No sources attached."
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @draft.command(name="source")
    async def draft_source(
        self, ctx, kind: str, url: str, reference: str, *, label: str
    ):
        """Attach a source: source <project|upstream> <https-url> <ref> <label>"""
        await self._require_editor(ctx)
        source = normalize_source(kind, label, url, reference)
        if source["key"] in set(await self.config.announced_source_keys()):
            raise commands.UserFeedbackCheckFailure(
                "That source reference has already been published."
            )
        drafts, key, record = await self._active()
        if source["key"] in {
            item.get("key") for item in record.get("sources", [])
        }:
            raise commands.UserFeedbackCheckFailure(
                "That source is already on this draft."
            )
        record.setdefault("sources", []).append(source)
        record["status"] = "draft"
        record["approved_by"] = 0
        record["approved_at"] = 0
        record["updated_at"] = int(time.time())
        await self._save_active(
            drafts, key, record, f"{ctx.author} added a source."
        )
        await ctx.send(f"Added {kind} source **{label}**.")

    @draft.command(name="propose")
    async def draft_propose(self, ctx, *, text: str):
        """Submit complete replacement text for the active draft."""
        await self._require_editor(ctx)
        drafts, key, record = await self._active()
        next_id = 1 + max(
            (
                int(item.get("id", 0))
                for item in record.get("proposals", [])
            ),
            default=0,
        )
        add_proposal(record, next_id, ctx.author.id, text, int(time.time()))
        await self._save_active(
            drafts,
            key,
            record,
            f"{ctx.author} submitted proposal #{next_id}.",
        )
        await ctx.send(f"Submitted proposal #{next_id} for publisher review.")

    @draft.command(name="proposals")
    async def draft_proposals(self, ctx):
        """List pending proposals for the active draft."""
        await self._require_editor(ctx)
        _, _, record = await self._active()
        lines = [
            f"#{item['id']} by <@{item.get('author_id', 0)}> - "
            f"{item.get('text', '')[:160]}"
            for item in record.get("proposals", [])
            if item.get("status") == "pending"
        ]
        await ctx.send(
            "\n".join(lines) or "No pending proposals.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @draft.command(name="apply")
    async def draft_apply(self, ctx, proposal_id: int):
        """Apply a trusted editor's proposed replacement text."""
        await self._require_publisher(ctx)
        drafts, key, record = await self._active()
        apply_proposal(record, proposal_id, ctx.author.id, int(time.time()))
        await self._save_active(
            drafts,
            key,
            record,
            f"{ctx.author} applied proposal #{proposal_id}.",
        )
        await ctx.send(
            f"Applied proposal #{proposal_id}; final approval is required again."
        )

    @draft.command(name="edit")
    async def draft_edit(self, ctx, *, text: str):
        """Directly replace the active draft text as a publisher."""
        await self._require_publisher(ctx)
        text = text.strip()
        if not text or len(text) > 3800:
            raise commands.UserFeedbackCheckFailure(
                "Text must contain 1-3,800 characters."
            )
        drafts, key, record = await self._active()
        record.setdefault("history", []).append(
            {
                "text": record["text"],
                "changed_by": ctx.author.id,
                "changed_at": int(time.time()),
                "proposal_id": 0,
            }
        )
        record["history"] = record["history"][-25:]
        record["text"] = text
        record["status"] = "draft"
        record["approved_by"] = 0
        record["approved_at"] = 0
        record["updated_at"] = int(time.time())
        await self._save_active(
            drafts, key, record, f"{ctx.author} edited the draft."
        )
        await ctx.send("Draft updated; final approval is required.")

    @draft.command(name="preview")
    async def draft_preview(self, ctx):
        """Show the active draft."""
        await self._require_editor(ctx)
        _, _, record = await self._active()
        await ctx.send(
            embed=self._embed(record, review=True),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @draft.command(name="approve")
    async def draft_approve(self, ctx):
        """Approve the active draft for global publishing."""
        await self._require_publisher(ctx)
        drafts, key, record = await self._active()
        if not record.get("sources"):
            raise commands.UserFeedbackCheckFailure(
                "Attach at least one project or upstream source before approval."
            )
        if record.get("status") == "published":
            raise commands.UserFeedbackCheckFailure(
                "This draft is already published."
            )
        record["status"] = "approved"
        record["approved_by"] = ctx.author.id
        record["approved_at"] = int(time.time())
        await self._save_active(
            drafts, key, record, f"{ctx.author} approved this draft."
        )
        await ctx.send(
            "Draft approved. Run the publish command for the final send."
        )

    @draft.command(name="publish")
    async def draft_publish(self, ctx):
        """Publish the approved draft to matching subscribed servers."""
        await self._require_publisher(ctx)
        drafts, key, record = await self._active()
        if record.get("status") != "approved":
            raise commands.UserFeedbackCheckFailure(
                "The active draft needs final approval first."
            )

        delivered = {
            int(value) for value in record.get("delivered_guild_ids", [])
        }
        successes = []
        failures = []
        eligible = 0
        for guild_id, data in (await self.config.all_guilds()).items():
            if not data.get("enabled") or not should_deliver(
                data.get("delivery_mode", "major-only"), record["kind"]
            ):
                continue
            eligible += 1
            guild_id = int(guild_id)
            if guild_id in delivered:
                continue
            guild = self.bot.get_guild(guild_id)
            channel = (
                guild.get_channel(int(data.get("channel_id", 0)))
                if guild
                else None
            )
            if channel is None:
                failures.append(guild_id)
                continue
            role = (
                guild.get_role(int(data.get("mention_role_id", 0)))
                if data.get("mention_role_id")
                else None
            )
            try:
                await channel.send(
                    content=role.mention if role else None,
                    embed=self._embed(record),
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False,
                        users=False,
                        roles=[role] if role else False,
                        replied_user=False,
                    ),
                )
            except (discord.Forbidden, discord.HTTPException):
                failures.append(guild_id)
            else:
                successes.append(guild_id)
                delivered.add(guild_id)

        if not eligible:
            raise commands.UserFeedbackCheckFailure(
                "No subscribed servers match this draft type; nothing was published."
            )
        record["delivered_guild_ids"] = sorted(delivered)
        if failures:
            record["status"] = "approved"
        else:
            record["status"] = "published"
            announced = set(await self.config.announced_source_keys())
            announced.update(
                source["key"] for source in record.get("sources", [])
            )
            await self.config.announced_source_keys.set(
                sorted(announced)[-1000:]
            )
        await self._save_active(
            drafts,
            key,
            record,
            f"Publish result: {len(successes)} new deliveries; "
            f"{len(failures)} failures.",
        )
        await ctx.send(
            f"Published to {len(successes)} new server(s); "
            f"{len(failures)} failed. Successful deliveries will not repeat."
        )

    @commands.group(name="botupdatesowner", invoke_without_command=True)
    @commands.is_owner()
    async def botupdatesowner(self, ctx):
        """Configure the global BotUpdates review team."""
        await ctx.send_help()

    @botupdatesowner.command(name="reviewchannel")
    async def review_channel(self, ctx, channel: discord.TextChannel):
        """Set the private channel used for collaborative draft previews."""
        await self.config.review_channel_id.set(channel.id)
        await ctx.send(
            f"BotUpdates review previews will use {channel.mention}."
        )

    async def _toggle_id(self, group, value, action):
        values = {int(item) for item in await group()}
        if action == "add":
            values.add(int(value))
        elif action == "remove":
            values.discard(int(value))
        else:
            raise commands.UserFeedbackCheckFailure(
                "Action must be add or remove."
            )
        await group.set(sorted(values))

    @botupdatesowner.command(name="editoruser")
    async def editor_user(self, ctx, action: str, user: discord.User):
        """Add or remove a trusted editor user."""
        await self._toggle_id(
            self.config.editor_user_ids, user.id, action.casefold()
        )
        await ctx.send(f"Editor user list updated for {user}.")

    @botupdatesowner.command(name="editorrole")
    async def editor_role(self, ctx, action: str, role: discord.Role):
        """Add or remove a trusted editor role."""
        await self._toggle_id(
            self.config.editor_role_ids, role.id, action.casefold()
        )
        await ctx.send(f"Editor role list updated for {role.name}.")

    @botupdatesowner.command(name="publisher")
    async def publisher(self, ctx, action: str, user: discord.User):
        """Add or remove a user who may approve and publish globally."""
        await self._toggle_id(
            self.config.publisher_user_ids, user.id, action.casefold()
        )
        await ctx.send(f"Publisher list updated for {user}.")


    @botupdatesowner.group(name="collector", invoke_without_command=True)
    async def collector(self, ctx):
        """Manage scheduled project and upstream commit digest drafts."""
        enabled = await self.config.collector_enabled()
        last_sha = await self.config.collector_last_sha()
        slot = await self.config.collector_last_slot()
        checked = int(await self.config.collector_last_check_at())
        error = await self.config.collector_last_error()
        lines = [
            f"Collector: **{'running' if enabled else 'stopped'}**",
            f"Repository: <{REPOSITORY_WEB_URL}> (`{REPOSITORY_BRANCH}`)",
            "Schedule: **1st and 15th of each month (UTC)**",
            f"Cursor: `{last_sha[:12] if last_sha else 'not seeded'}`",
            f"Last completed slot: `{slot or 'none'}`",
            f"Upstream sources: {len(await self.config.upstream_sources())} (see `upstream` for details)",
            f"Last check: <t:{checked}:F>" if checked else "Last check: never",
        ]
        if error:
            lines.append(f"Last error: {error}")
        await ctx.send(
            "\n".join(lines), allowed_mentions=discord.AllowedMentions.none()
        )

    @collector.command(name="start")
    async def collector_start(self, ctx):
        """Seed the current main commit and start twice-monthly draft collection."""
        async with self._collector_lock:
            if await self.config.collector_enabled():
                await ctx.send("The Sick-Cogs collector is already running.")
                return
            if not await self.config.review_channel_id():
                raise commands.UserFeedbackCheckFailure(
                    "Set a private review channel before starting the collector."
                )
            existing_cursor = await self.config.collector_last_sha()
            if not existing_cursor:
                try:
                    latest = await (await self._commit_client()).latest()
                except CommitCollectionError as error:
                    await self.config.collector_last_error.set(str(error))
                    await ctx.send(str(error))
                    return
                now = datetime.datetime.now(datetime.timezone.utc)
                await self.config.collector_last_sha.set(latest["sha"])
                await self.config.collector_last_slot.set(digest_slot(now))
                await self.config.collector_last_check_at.set(int(now.timestamp()))
                message = (
                    "Sick-Cogs collection is running. The current main commit was seeded "
                    "without creating an old-history draft; new review drafts are due on "
                    "the 1st and 15th UTC."
                )
            else:
                message = (
                    "Sick-Cogs collection is running again with its existing commit cursor."
                )
            await self.config.collector_last_error.set("")
            await self.config.collector_enabled.set(True)
            await ctx.send(message)

    @collector.command(name="stop")
    async def collector_stop(self, ctx):
        """Stop scheduled collection without clearing its commit cursor."""
        async with self._collector_lock:
            await self.config.collector_enabled.set(False)
            await ctx.send("Scheduled Sick-Cogs collection is stopped.")

    @collector.command(name="check", aliases=["now"])
    async def collector_check(self, ctx):
        """Collect new commits into a review draft now."""
        status, detail = await self.collector_once(force=True)
        messages = {
            "created": f"Created review draft #{detail['id']} from collected project/upstream commits." if detail else "Created a review draft.",
            "no-changes": "No new project or enabled upstream commits were found.",
            "already-announced": "All collected commits were already announced.",
            "draft-active": "Finish or publish the current active draft before collecting another digest.",
            "not-seeded": "Start the collector once to seed its commit cursor.",
        }
        await ctx.send(messages.get(status, str(detail or status)))


    @botupdatesowner.group(name="upstream", invoke_without_command=True)
    async def upstream(self, ctx):
        """List configured public GitHub sources and their independent cursors."""
        sources = await self.config.upstream_sources()
        if not sources:
            await ctx.send("No upstream sources. Use `upstream add <name> <owner/repo> <branch> [path]`.")
            return
        for name, source in sorted(sources.items()):
            await ctx.send(
                f"**{name}** — {'enabled' if source['enabled'] else 'paused'}\n"
                f"{source['repository']} · branch `{source['branch']}` · path `{source['path'] or '(all)'}`\n"
                f"Cursor: `{source['last_sha'][:12]}`",
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @upstream.command(name="add")
    async def upstream_add(self, ctx, name: str, repository: str, branch: str, *, path: str = ""):
        """Seed a public GitHub source: add <name> <owner/repo> <branch> [cog/path]."""
        try:
            name, source = upstream_source(name, repository, branch, path)
        except ValueError as exc:
            raise commands.UserFeedbackCheckFailure(str(exc)) from exc
        async with self._collector_lock:
            sources = await self.config.upstream_sources()
            if name in sources:
                raise commands.UserFeedbackCheckFailure("That source name already exists; remove it explicitly to reseed.")
            if len(sources) >= MAX_UPSTREAM_SOURCES:
                raise commands.UserFeedbackCheckFailure("At most 10 upstream sources may be configured.")
            if any((item["repository"], item["branch"], item["path"]) ==
                   (source["repository"], source["branch"], source["path"]) for item in sources.values()):
                raise commands.UserFeedbackCheckFailure("That repository, branch and path are already configured.")
            try:
                latest = await (await self._upstream_client(source)).latest()
            except CommitCollectionError as exc:
                raise commands.UserFeedbackCheckFailure(str(exc)) from exc
            source["last_sha"] = latest["sha"]
            sources[name] = source
            await self.config.upstream_sources.set(sources)
        await ctx.send(f"Seeded upstream **{name}** without drafting old history. New changes join the collector's next due draft; use `collector start` if stopped.")

    @upstream.command(name="enabled")
    async def upstream_enabled(self, ctx, name: str, enabled: bool):
        """Pause/resume a source without changing its saved cursor."""
        async with self._collector_lock:
            sources = await self.config.upstream_sources()
            name = name.lower()
            if name not in sources:
                raise commands.UserFeedbackCheckFailure("Unknown upstream source.")
            sources[name]["enabled"] = enabled
            await self.config.upstream_sources.set(sources)
        await ctx.send(f"Upstream {name} {'enabled' if enabled else 'paused'}; its cursor is unchanged.")

    @upstream.command(name="remove")
    async def upstream_remove(self, ctx, name: str):
        """Remove a source and its cursor; existing draft references are retained."""
        async with self._collector_lock:
            sources = await self.config.upstream_sources()
            name = name.lower()
            if name not in sources:
                raise commands.UserFeedbackCheckFailure("Unknown upstream source.")
            del sources[name]
            await self.config.upstream_sources.set(sources)
        await ctx.send(f"Removed upstream {name}. Re-adding it will seed current history, not backfill missed commits.")
