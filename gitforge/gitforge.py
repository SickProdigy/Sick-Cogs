"""Multi-provider Git forge integration for Red."""

import asyncio
import hashlib
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
MAX_CONCURRENT_POLL_REQUESTS = 6
POLL_BACKOFF_BASE_SECONDS = 120
POLL_BACKOFF_MAX_SECONDS = 1800


class GitForge(commands.Cog):
    """Create reviewed forge issues and route repository events."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_global(allowed_hosts=sorted(PUBLIC_HOSTS))
        self.config.register_guild(connections={}, repositories={})
        self.allowed_hosts = set(PUBLIC_HOSTS)
        self.poll_lock = asyncio.Lock()
        self._poll_backoff = {}

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

    @staticmethod
    def issue_embed(alias, item):
        title = discord.utils.escape_markdown(str(item.get("title") or "Untitled issue"))
        url = str(item.get("url") or "")
        embed = discord.Embed(
            title=f"Issue #{int(item.get('number') or 0)} — {title}"[:256],
            url=url if url.startswith("https://") else None,
            description=str(item.get("body") or "No description provided.")[:3000],
            color=discord.Color.green() if item.get("state") == "open" else discord.Color.dark_grey(),
        )
        embed.add_field(name="Repository", value=alias, inline=True)
        embed.add_field(name="State", value=str(item.get("state") or "unknown").title()[:100], inline=True)
        embed.add_field(name="Author", value=str(item.get("author") or "Unknown")[:100], inline=True)
        embed.add_field(name="Comments", value=str(int(item.get("comments") or 0)), inline=True)
        labels = [discord.utils.escape_markdown(str(label)) for label in item.get("labels") or []]
        embed.add_field(name="Labels", value=", ".join(labels)[:1024] or "None", inline=False)
        if item.get("updated_at"):
            embed.set_footer(text=f"Updated {str(item['updated_at'])[:40]}")
        return embed

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
    async def issue(self, ctx, repo_alias: str, number: int = None):
        """Create an issue draft, or show one by number."""
        alias, repository, client = await self.repository_for(ctx.guild, repo_alias)
        if number is not None:
            item = await client.get_issue(repository["owner"], repository["repo"], number)
            await ctx.send(
                embed=self.issue_embed(alias, item),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
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
            f"[#{item['number']} {discord.utils.escape_markdown(item['title'][:100])}]({item['url']}) - {discord.utils.escape_markdown(item['author'][:100])}"
            for item in issues
        ]
        await ctx.send(
            embed=discord.Embed(title=f"Open issues - {alias}", description="\n".join(lines)[:4000] or "No open issues.", color=discord.Color.blurple()),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @git.command(name="search")
    @commands.cooldown(3, 60, commands.BucketType.user)
    async def search(self, ctx, repo_alias: str, *, query: str):
        """Search issues in a registered repository."""
        alias, repository, client = await self.repository_for(ctx.guild, repo_alias)
        issues = await client.search_issues(
            repository["owner"], repository["repo"], query, limit=10
        )
        lines = []
        for item in issues:
            title = discord.utils.escape_markdown(item["title"][:120])
            author = discord.utils.escape_markdown(item["author"][:80])
            state = str(item.get("state") or "unknown").casefold()
            lines.append(
                f"[#{item['number']} {title}]({item['url']}) — {state} — {author}"
            )
        embed = discord.Embed(
            title=f"Issue search — {alias}",
            description="\n".join(lines)[:4000] or "No matching issues were found.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Query: {query[:100]} · Use git issue {alias} <number> for details")
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @staticmethod
    def browse_text(value, limit):
        return discord.utils.escape_markdown(str(value).replace("@", "@\u200b"))[:limit]

    async def send_browse_list(self, ctx, title, rows, empty):
        lines = []
        for row in rows[:10]:
            label = self.browse_text(row["label"], 20)
            text = self.browse_text(row["title"], 140)
            lines.append(f"[{label}]({row['url']}) — {text}" if row.get("url") else f"{label} — {text}")
        embed = discord.Embed(title=title[:256], description="\n".join(lines)[:4000] or empty,
                              color=discord.Color.blurple())
        embed.set_footer(text="Up to 10 results; follow a link for the full details.")
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @git.command(name="repo")
    @commands.cooldown(3, 60, commands.BucketType.user)
    async def browse_repo(self, ctx, repo_alias: str):
        """Show a repository summary and its default branch."""
        await ctx.defer()
        alias, repository, client = await self.repository_for(ctx.guild, repo_alias)
        data = await client.repository_summary(repository["owner"], repository["repo"])
        embed = discord.Embed(title=self.browse_text(data["name"], 256), url=data["url"],
                              description=self.browse_text(data["description"], 3000), color=discord.Color.blurple())
        embed.add_field(name="Default branch", value=self.browse_text(data["branch"], 200))
        embed.add_field(name="Visibility", value=self.browse_text(data["visibility"], 100))
        embed.add_field(name="Archived", value="Yes" if data["archived"] else "No")
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @git.command(name="commits")
    @commands.cooldown(3, 60, commands.BucketType.user)
    async def browse_commits(self, ctx, repo_alias: str, branch: str = None):
        """Show up to ten commits, optionally from a selected branch."""
        await ctx.defer()
        alias, repository, client = await self.repository_for(ctx.guild, repo_alias)
        rows = await client.recent_commits(repository["owner"], repository["repo"], branch)
        await self.send_browse_list(ctx, f"Recent commits — {alias}", rows, "No commits returned for this repository/ref.")

    @git.command(name="prs")
    @commands.cooldown(3, 60, commands.BucketType.user)
    async def browse_pulls(self, ctx, repo_alias: str):
        """Show up to ten open pull or merge requests."""
        await ctx.defer()
        alias, repository, client = await self.repository_for(ctx.guild, repo_alias)
        rows = await client.open_pulls(repository["owner"], repository["repo"])
        await self.send_browse_list(ctx, f"Open pull/merge requests — {alias}", rows, "No open pull/merge requests.")

    @git.command(name="release")
    @commands.cooldown(3, 60, commands.BucketType.user)
    async def browse_release(self, ctx, repo_alias: str):
        """Show the provider's latest release and release notes."""
        await ctx.defer()
        alias, repository, client = await self.repository_for(ctx.guild, repo_alias)
        data = await client.latest_release(repository["owner"], repository["repo"])
        if data is None:
            await ctx.send("No latest release is available for this repository.", allowed_mentions=discord.AllowedMentions.none())
            return
        embed = discord.Embed(title=self.browse_text(data["name"], 256), url=data["url"],
                              description=self.browse_text(data["body"], 3000), color=discord.Color.blurple())
        embed.add_field(name="Tag", value=self.browse_text(data["tag"], 200))
        embed.add_field(name="Published", value=self.browse_text(data["published"], 100))
        embed.set_footer(text=f"{alias} • Follow the title link for full release notes.")
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @tasks.loop(minutes=2)
    async def repository_poller(self):
        if self.poll_lock.locked():
            return
        async with self.poll_lock:
            await self.poll_once()

    @repository_poller.before_loop
    async def before_repository_poller(self):
        await self.bot.wait_until_red_ready()

    @staticmethod
    def _poll_job_key(client, repository, resource):
        credential = hashlib.sha256(client.token.encode("utf-8")).digest()
        return (
            client.provider,
            client.base_url,
            credential,
            str(repository["owner"]),
            str(repository["repo"]),
            resource,
        )

    def _poll_job_ready(self, key):
        state = self._poll_backoff.get(key)
        return state is None or asyncio.get_running_loop().time() >= state[1]

    def _poll_job_failed(self, key):
        failures = self._poll_backoff.get(key, (0, 0))[0] + 1
        delay = min(
            POLL_BACKOFF_MAX_SECONDS,
            POLL_BACKOFF_BASE_SECONDS * (2 ** min(failures - 1, 4)),
        )
        self._poll_backoff[key] = (
            failures,
            asyncio.get_running_loop().time() + delay,
        )

    def _poll_job_succeeded(self, key):
        self._poll_backoff.pop(key, None)

    async def poll_once(self):
        guild_data = {}
        jobs = {}
        for guild_id, data in (await self.config.all_guilds()).items():
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                continue
            repositories = data.get("repositories", {})
            guild_data[guild.id] = (guild, repositories)
            clients = {}
            for alias, repository in repositories.items():
                resources = []
                if repository.get("issue_channel_id"):
                    resources.append("issues")
                if repository.get("ci_channel_id"):
                    resources.append("ci")
                if not resources:
                    continue
                connection = str(repository.get("connection") or "")
                try:
                    if connection not in clients:
                        clients[connection] = await self.client_for(guild, connection)
                    client = clients[connection]
                except (ForgeError, ValueError):
                    log.debug(
                        "GitForge connection failed for guild %s repository %s",
                        guild.id,
                        alias,
                        exc_info=True,
                    )
                    continue
                for resource in resources:
                    key = self._poll_job_key(client, repository, resource)
                    job = jobs.setdefault(
                        key,
                        {
                            "client": client,
                            "owner": repository["owner"],
                            "repo": repository["repo"],
                            "resource": resource,
                            "consumers": [],
                        },
                    )
                    job["consumers"].append((guild, alias, repository))

        self._poll_backoff = {
            key: state for key, state in self._poll_backoff.items() if key in jobs
        }
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_POLL_REQUESTS)

        async def fetch_job(key, job):
            if not self._poll_job_ready(key):
                return key, None
            try:
                async with semaphore:
                    if job["resource"] == "issues":
                        items = await job["client"].list_issues(job["owner"], job["repo"])
                    else:
                        items = await job["client"].list_ci_runs(job["owner"], job["repo"])
            except asyncio.CancelledError:
                raise
            except ForgeError:
                self._poll_job_failed(key)
                log.debug(
                    "GitForge %s poll failed for %s/%s at %s",
                    job["resource"],
                    job["owner"],
                    job["repo"],
                    job["client"].base_url,
                    exc_info=True,
                )
                return key, None
            except Exception:
                self._poll_job_failed(key)
                log.exception(
                    "Unexpected GitForge %s poll failure for %s/%s at %s",
                    job["resource"],
                    job["owner"],
                    job["repo"],
                    job["client"].base_url,
                )
                return key, None
            self._poll_job_succeeded(key)
            return key, tuple(items)

        results = dict(
            await asyncio.gather(
                *(fetch_job(key, job) for key, job in jobs.items())
            )
        )
        changed_guilds = set()
        for key, job in jobs.items():
            items = results.get(key)
            if items is None:
                continue
            for guild, alias, repository in job["consumers"]:
                try:
                    if job["resource"] == "issues":
                        changed = await self._poll_issues(
                            guild,
                            alias,
                            repository,
                            job["client"],
                            issues=items,
                        )
                    else:
                        changed = await self._poll_ci(
                            guild,
                            alias,
                            repository,
                            job["client"],
                            runs=items,
                        )
                except asyncio.CancelledError:
                    raise
                except (ForgeError, ValueError, discord.HTTPException):
                    log.debug(
                        "GitForge delivery failed for guild %s repository %s",
                        guild.id,
                        alias,
                        exc_info=True,
                    )
                except Exception:
                    log.exception(
                        "Unexpected GitForge delivery failure for guild %s repository %s",
                        guild.id,
                        alias,
                    )
                else:
                    if changed:
                        changed_guilds.add(guild.id)

        for guild_id in changed_guilds:
            guild, repositories = guild_data[guild_id]
            try:
                await self.config.guild(guild).repositories.set(repositories)
            except Exception:
                log.exception("Could not save GitForge poll state for guild %s", guild.id)

    async def _poll_issues(self, guild, alias, repository, client, *, issues=None):
        if issues is None:
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

    async def _poll_ci(self, guild, alias, repository, client, *, runs=None):
        if runs is None:
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
