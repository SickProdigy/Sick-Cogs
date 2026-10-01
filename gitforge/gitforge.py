"""Multi-provider Git forge integration for Red."""

import asyncio
import logging
from urllib.parse import urlsplit

import discord
from discord.ext import tasks
from redbot.core import Config, commands

from .providers import ForgeClient, ForgeError, PUBLIC_HOSTS, forge_hostname, normalize_alias
from .views import IssueDraftView, SetupView

log = logging.getLogger("red.sickcogs.gitforge")
CONFIG_IDENTIFIER = 7422161103
FAILURE_STATES = {"failure", "failed", "cancelled", "canceled", "timed_out", "error"}


class GitForge(commands.Cog):
    """Create reviewed forge issues and route repository events."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_global(allowed_hosts=sorted(PUBLIC_HOSTS))
        self.config.register_guild(connections={}, repositories={})
        self.allowed_hosts = set(PUBLIC_HOSTS)
        self.poll_lock = asyncio.Lock()

    async def cog_load(self):
        self.allowed_hosts.update(host.casefold() for host in await self.config.allowed_hosts())
        self.repository_poller.start()

    def cog_unload(self):
        self.repository_poller.cancel()

    async def red_delete_data_for_user(self, **kwargs):
        return

    async def cog_command_error(self, ctx, error):
        original = getattr(error, "original", error)
        if isinstance(original, (ForgeError, ValueError)):
            await ctx.send(str(original), allowed_mentions=discord.AllowedMentions.none())
            return
        raise error

    @staticmethod
    def token_namespace(guild_id, alias):
        return f"gitforge_guild_{int(guild_id)}_{normalize_alias(alias)}"

    @staticmethod
    def make_client(provider, base_url, token):
        return ForgeClient(provider, base_url, token)

    def ensure_host_allowed(self, base_url):
        host = forge_hostname(base_url)
        if host not in self.allowed_hosts:
            raise ValueError(f"The bot owner must approve {host} before this server can connect.")

    async def client_for(self, guild, connection_alias):
        alias = normalize_alias(connection_alias)
        connection = (await self.config.guild(guild).connections()).get(alias)
        if not connection:
            raise ForgeError(f"Connection {alias} is not configured.", code="not_configured")
        self.ensure_host_allowed(connection["base_url"])
        tokens = await self.bot.get_shared_api_tokens(self.token_namespace(guild.id, alias))
        if not tokens.get("token"):
            raise ForgeError(f"Connection {alias} is missing its access token.", code="not_configured")
        return self.make_client(connection["provider"], connection["base_url"], tokens["token"])

    async def repository_for(self, guild, repo_alias):
        alias = normalize_alias(repo_alias)
        repository = (await self.config.guild(guild).repositories()).get(alias)
        if not repository:
            raise ValueError(f"Repository {alias} is not registered.")
        return alias, repository, await self.client_for(guild, repository["connection"])

    @staticmethod
    def can_create(member, repository):
        return repository.get("policy", "admins") == "members" or member.guild_permissions.manage_guild

    async def submit_issue(self, guild, member, repo_alias, title, description, labels, *, source_url=None):
        alias, repository, client = await self.repository_for(guild, repo_alias)
        if not self.can_create(member, repository):
            raise ValueError(f"Repository {alias} only accepts issues from server administrators.")
        allowed = {str(label).casefold(): str(label) for label in repository.get("allowed_labels", [])}
        requested = []
        for label in labels:
            if label.casefold() not in allowed:
                raise ValueError(f"Label {label} is not enabled for Discord submissions.")
            requested.append(allowed[label.casefold()])
        safe_name = str(member.display_name).replace("@", "@\u200b")[:100]
        attribution = f"Submitted through Discord by {safe_name} (user ID {member.id})."
        if source_url:
            attribution += f" Source message: {source_url}"
        safe_title = title.strip().replace("@", "@\u200b")
        safe_description = description.strip().replace("@", "@\u200b")
        body = (safe_description + "\n\n---\n" + attribution).strip()
        return await client.create_issue(repository["owner"], repository["repo"], safe_title, body, requested)

    async def settings_embed(self, guild):
        connections = await self.config.guild(guild).connections()
        repositories = await self.config.guild(guild).repositories()
        embed = discord.Embed(
            title="Git forge setup",
            description="Connections hold credentials securely; repository aliases control access and notifications.",
            color=discord.Color.blurple(),
        )
        connection_lines = [
            f"**{alias}** - {data.get('provider', '?')} at {urlsplit(data.get('base_url', '')).hostname or '?'}"
            for alias, data in sorted(connections.items())
        ]
        repository_lines = []
        for alias, data in sorted(repositories.items()):
            issues = f"<#{data['issue_channel_id']}>" if data.get("issue_channel_id") else "off"
            ci = f"<#{data['ci_channel_id']}>" if data.get("ci_channel_id") else "off"
            repository_lines.append(
                f"**{alias}** - {data.get('owner')}/{data.get('repo')} via {data.get('connection')} "
                f"- create: {data.get('policy', 'admins')} - issues: {issues} - CI: {ci}"
            )
        embed.add_field(name="Connections", value="\n".join(connection_lines)[:1024] or "None", inline=False)
        embed.add_field(name="Repositories", value="\n".join(repository_lines)[:1024] or "None", inline=False)
        return embed

    @commands.group(name="gitownerset", invoke_without_command=True)
    @commands.is_owner()
    async def gitownerset(self, ctx):
        """Control which HTTPS forge hosts guilds may connect."""
        await ctx.send_help()

    @gitownerset.command(name="allowhost")
    async def allow_host(self, ctx, hostname: str):
        host = hostname.strip().casefold().rstrip(".")
        if "://" in host:
            host = (urlsplit(host).hostname or "").casefold()
        if not host or "/" in host or " " in host:
            await ctx.send("Enter a hostname such as git.example.com.")
            return
        self.allowed_hosts.add(host)
        await self.config.allowed_hosts.set(sorted(self.allowed_hosts))
        await ctx.send(f"Approved forge host {host}.")

    @gitownerset.command(name="removehost")
    async def remove_host(self, ctx, hostname: str):
        host = hostname.strip().casefold().rstrip(".")
        if host in PUBLIC_HOSTS:
            await ctx.send("Built-in public forge hosts cannot be removed.")
            return
        self.allowed_hosts.discard(host)
        await self.config.allowed_hosts.set(sorted(self.allowed_hosts))
        await ctx.send(f"Removed forge host {host}.")

    @gitownerset.command(name="hosts")
    async def list_hosts(self, ctx):
        await ctx.send("Approved forge hosts:\n" + "\n".join(f"- {host}" for host in sorted(self.allowed_hosts)))

    @commands.group(name="gitset", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def gitset(self, ctx):
        """Configure this servers forge connections and repositories."""
        await ctx.send(embed=await self.settings_embed(ctx.guild), view=SetupView(self, ctx.author))

    @gitset.command(name="policy")
    async def set_policy(self, ctx, repo_alias: str, policy: str):
        policy = policy.casefold()
        if policy not in {"admins", "members"}:
            await ctx.send("Policy must be admins or members.")
            return
        alias = normalize_alias(repo_alias)
        repositories = await self.config.guild(ctx.guild).repositories()
        if alias not in repositories:
            await ctx.send(f"Repository {alias} is not registered.")
            return
        repositories[alias]["policy"] = policy
        await self.config.guild(ctx.guild).repositories.set(repositories)
        await ctx.send(f"Repository {alias} now allows {policy} to create issues.")

    @gitset.command(name="labels")
    async def set_labels(self, ctx, repo_alias: str, *, labels: str = ""):
        alias = normalize_alias(repo_alias)
        repositories = await self.config.guild(ctx.guild).repositories()
        if alias not in repositories:
            await ctx.send(f"Repository {alias} is not registered.")
            return
        values = [value.strip() for value in labels.split(",") if value.strip()]
        if len(values) > 25 or any(len(value) > 50 for value in values):
            await ctx.send("Configure at most 25 labels of at most 50 characters each.")
            return
        repositories[alias]["allowed_labels"] = values
        await self.config.guild(ctx.guild).repositories.set(repositories)
        await ctx.send(f"Allowed labels for {alias}: {', '.join(values) if values else 'none'}.")

    async def _set_channel(self, ctx, repo_alias, key, channel):
        alias = normalize_alias(repo_alias)
        repositories = await self.config.guild(ctx.guild).repositories()
        if alias not in repositories:
            await ctx.send(f"Repository {alias} is not registered.")
            return
        repositories[alias][key] = channel.id if channel else 0
        await self.config.guild(ctx.guild).repositories.set(repositories)
        label = "issue events" if key == "issue_channel_id" else "CI failures"
        await ctx.send(f"{label.title()} for {alias}: {channel.mention if channel else 'disabled'}.")

    @gitset.command(name="issuechannel")
    async def issue_channel(self, ctx, repo_alias: str, channel: discord.TextChannel = None):
        await self._set_channel(ctx, repo_alias, "issue_channel_id", channel)

    @gitset.command(name="cichannel")
    async def ci_channel(self, ctx, repo_alias: str, channel: discord.TextChannel = None):
        await self._set_channel(ctx, repo_alias, "ci_channel_id", channel)

    @gitset.command(name="removerepo")
    async def remove_repo(self, ctx, repo_alias: str):
        alias = normalize_alias(repo_alias)
        repositories = await self.config.guild(ctx.guild).repositories()
        if repositories.pop(alias, None) is None:
            await ctx.send(f"Repository {alias} is not registered.")
            return
        await self.config.guild(ctx.guild).repositories.set(repositories)
        await ctx.send(f"Removed repository {alias}.")

    @gitset.command(name="removeconnection")
    async def remove_connection(self, ctx, connection_alias: str):
        alias = normalize_alias(connection_alias)
        repositories = await self.config.guild(ctx.guild).repositories()
        if any(repo.get("connection") == alias for repo in repositories.values()):
            await ctx.send("Remove repositories using this connection first.")
            return
        connections = await self.config.guild(ctx.guild).connections()
        if connections.pop(alias, None) is None:
            await ctx.send(f"Connection {alias} is not configured.")
            return
        await self.config.guild(ctx.guild).connections.set(connections)
        await self.bot.remove_shared_api_tokens(self.token_namespace(ctx.guild.id, alias), "token")
        await ctx.send(f"Removed connection {alias} and its stored token.")

    @commands.hybrid_group(name="git", invoke_without_command=True)
    @commands.guild_only()
    async def git(self, ctx):
        """Browse configured repositories and create reviewed issues."""
        if not await self.config.guild(ctx.guild).repositories():
            await ctx.send("No Git repositories are configured for this server.")
            return
        await ctx.send(embed=await self.settings_embed(ctx.guild))

    @git.command(name="issue")
    @commands.cooldown(2, 60, commands.BucketType.user)
    async def issue(self, ctx, repo_alias: str):
        """Open an editable issue preview for a registered repository."""
        alias, repository, unused_client = await self.repository_for(ctx.guild, repo_alias)
        if not self.can_create(ctx.author, repository):
            await ctx.send("This repository only accepts issues from server administrators.")
            return
        resolved = ctx.message.reference and ctx.message.reference.resolved
        source_url = resolved.jump_url if isinstance(resolved, discord.Message) else None
        view = IssueDraftView(self, ctx.author, ctx.guild, alias, source_url)
        kwargs = {"embed": view.embed(), "view": view}
        if ctx.interaction:
            kwargs["ephemeral"] = True
        await ctx.send(**kwargs)

    @git.command(name="issues")
    async def issues(self, ctx, repo_alias: str):
        """List recent open issues."""
        alias, repository, client = await self.repository_for(ctx.guild, repo_alias)
        issues = await client.list_issues(repository["owner"], repository["repo"])
        lines = [
            f"[#{item['number']} {item['title'][:100]}]({item['url']}) - {item['author']}"
            for item in issues
        ]
        await ctx.send(
            embed=discord.Embed(title=f"Open issues - {alias}", description="\n".join(lines)[:4000] or "No open issues.", color=discord.Color.blurple()),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @tasks.loop(minutes=2)
    async def repository_poller(self):
        if self.poll_lock.locked():
            return
        async with self.poll_lock:
            for guild_id, data in (await self.config.all_guilds()).items():
                guild = self.bot.get_guild(int(guild_id))
                if guild:
                    await self._poll_guild(guild, data.get("repositories", {}))

    @repository_poller.before_loop
    async def before_repository_poller(self):
        await self.bot.wait_until_red_ready()

    async def _poll_guild(self, guild, repositories):
        changed = False
        for alias, repository in repositories.items():
            try:
                client = await self.client_for(guild, repository["connection"])
                if repository.get("issue_channel_id"):
                    changed |= await self._poll_issues(guild, alias, repository, client)
                if repository.get("ci_channel_id"):
                    changed |= await self._poll_ci(guild, alias, repository, client)
            except (ForgeError, ValueError, discord.HTTPException):
                log.debug("GitForge poll failed for guild %s repository %s", guild.id, alias, exc_info=True)
        if changed:
            await self.config.guild(guild).repositories.set(repositories)

    async def _poll_issues(self, guild, alias, repository, client):
        issues = await client.list_issues(repository["owner"], repository["repo"])
        current = [item["id"] for item in issues if item["id"]]
        seen = set(int(value) for value in repository.get("seen_issue_ids", []))
        if not repository.get("issues_initialized", False):
            repository["seen_issue_ids"] = current[:50]
            repository["issues_initialized"] = True
            return True
        channel = guild.get_channel(int(repository["issue_channel_id"]))
        if channel:
            for item in reversed([issue for issue in issues if issue["id"] not in seen]):
                embed = discord.Embed(
                    title=f"New issue #{item['number']} - {alias}",
                    description=f"**{item['title'][:256]}**\nOpened by {item['author'][:100]}\n\n[Open issue]({item['url']})",
                    color=discord.Color.blurple(),
                )
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        repository["seen_issue_ids"] = list(dict.fromkeys(current + list(seen)))[:50]
        return True

    async def _poll_ci(self, guild, alias, repository, client):
        try:
            runs = await client.list_ci_runs(repository["owner"], repository["repo"])
        except ForgeError as error:
            if error.status in {404, 405}:
                return False
            raise
        previous = {str(key): value for key, value in repository.get("ci_states", {}).items()}
        current = {run["id"]: run["status"].casefold() for run in runs if run["id"]}
        if not repository.get("ci_initialized", False):
            repository["ci_states"] = current
            repository["ci_initialized"] = True
            return True
        channel = guild.get_channel(int(repository["ci_channel_id"]))
        if channel:
            for run in reversed(runs):
                old = previous.get(run["id"])
                status = run["status"].casefold()
                if old == status or not (status in FAILURE_STATES or old in FAILURE_STATES):
                    continue
                failed = status in FAILURE_STATES
                embed = discord.Embed(
                    title=f"{'CI failed' if failed else 'CI recovered'} - {alias}",
                    description=f"**{run['name'][:200]}**\nBranch: {run['branch'][:100] or 'unknown'}\nStatus: {status}\n\n[Open run]({run['url']})",
                    color=discord.Color.red() if failed else discord.Color.green(),
                )
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        repository["ci_states"] = dict(list(current.items())[:50])
        return True
