"""Requester-bound setup and issue-review interactions."""

import discord

from .providers import ForgeError, normalize_alias, normalize_base_url, normalize_provider


class OwnedView(discord.ui.View):
    def __init__(self, cog, owner, *, timeout=600):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.owner = owner

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("Open your own Git panel to use these controls.", ephemeral=True)
            return False
        return True

    async def show_error(self, interaction, error):
        message = str(error) if isinstance(error, (ForgeError, ValueError)) else "The forge action could not be completed."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class ConnectionModal(discord.ui.Modal, title="Connect a Git forge"):
    alias = discord.ui.TextInput(label="Connection alias", placeholder="work", max_length=32)
    provider = discord.ui.TextInput(label="Provider", placeholder="gitea, github, or gitlab", max_length=10)
    base_url = discord.ui.TextInput(label="Forge URL", placeholder="https://git.example.com", max_length=300)
    token = discord.ui.TextInput(label="Restricted access token", max_length=500)

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Manage Server permission is required.", ephemeral=True)
            return
        try:
            alias = normalize_alias(self.alias)
            provider = normalize_provider(self.provider)
            base_url = normalize_base_url(self.base_url, provider)
            self.view.cog.ensure_host_allowed(base_url)
            client = self.view.cog.make_client(provider, base_url, str(self.token))
        except (ValueError, ForgeError) as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await interaction.response.defer()
        try:
            identity = await client.test_connection()
        except ForgeError as error:
            await self.view.show_error(interaction, error)
            return
        guild = interaction.guild
        connections = await self.view.cog.config.guild(guild).connections()
        previous = connections.get(alias)
        if previous and (previous.get("provider") != provider or previous.get("base_url") != base_url):
            repositories = await self.view.cog.config.guild(guild).repositories()
            if any(repo.get("connection") == alias for repo in repositories.values()):
                await interaction.followup.send("Remove repositories using this connection before replacing it.", ephemeral=True)
                return
        await self.view.cog.bot.set_shared_api_tokens(
            self.view.cog.token_namespace(guild.id, alias), token=str(self.token).strip()
        )
        connections[alias] = {"provider": provider, "base_url": base_url}
        await self.view.cog.config.guild(guild).connections.set(connections)
        username = identity.get("login") or identity.get("username") or identity.get("name") or "authenticated account"
        await interaction.edit_original_response(
            content=f"Connected {alias} to {provider.title()} as {str(username)[:100]}.",
            embed=await self.view.cog.settings_embed(guild),
            view=SetupView(self.view.cog, interaction.user),
        )


class RepositoryModal(discord.ui.Modal, title="Register a repository"):
    alias = discord.ui.TextInput(label="Repository alias", placeholder="sick-cogs", max_length=32)
    connection = discord.ui.TextInput(label="Connection alias", placeholder="work", max_length=32)
    repository = discord.ui.TextInput(label="Repository", placeholder="owner/name", max_length=200)

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Manage Server permission is required.", ephemeral=True)
            return
        try:
            alias = normalize_alias(self.alias)
            connection = normalize_alias(self.connection)
            value = str(self.repository).strip().strip("/")
            owner, repo = value.split("/", 1)
            if not owner or not repo or "/" in repo:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Use a repository in owner/name form.", ephemeral=True)
            return
        connections = await self.view.cog.config.guild(interaction.guild).connections()
        if connection not in connections:
            await interaction.response.send_message(f"Connection {connection} does not exist.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            client = await self.view.cog.client_for(interaction.guild, connection)
            details = await client.repository(owner, repo)
        except ForgeError as error:
            await self.view.show_error(interaction, error)
            return
        repositories = await self.view.cog.config.guild(interaction.guild).repositories()
        old = repositories.get(alias, {})
        repositories[alias] = {
            "connection": connection, "owner": owner, "repo": repo,
            "policy": old.get("policy", "admins"),
            "issue_channel_id": int(old.get("issue_channel_id", 0) or 0),
            "ci_channel_id": int(old.get("ci_channel_id", 0) or 0),
            "seen_issue_ids": old.get("seen_issue_ids", []),
            "issues_initialized": bool(old.get("issues_initialized", False)),
            "ci_states": old.get("ci_states", {}),
            "ci_initialized": bool(old.get("ci_initialized", False)),
        }
        await self.view.cog.config.guild(interaction.guild).repositories.set(repositories)
        name = details.get("full_name") or details.get("path_with_namespace") or value
        await interaction.edit_original_response(
            content=f"Registered {alias} for {str(name)[:200]}.",
            embed=await self.view.cog.settings_embed(interaction.guild),
            view=SetupView(self.view.cog, interaction.user),
        )


class SetupView(OwnedView):
    @discord.ui.button(label="Connect forge", style=discord.ButtonStyle.success)
    async def connect(self, interaction, button):
        await interaction.response.send_modal(ConnectionModal(self))

    @discord.ui.button(label="Add repository", style=discord.ButtonStyle.primary)
    async def repository(self, interaction, button):
        await interaction.response.send_modal(RepositoryModal(self))

    @discord.ui.button(label="Test connections", style=discord.ButtonStyle.secondary)
    async def test(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        connections = await self.cog.config.guild(interaction.guild).connections()
        results = []
        for alias in sorted(connections):
            try:
                await (await self.cog.client_for(interaction.guild, alias)).test_connection()
            except ForgeError as error:
                results.append(f"FAIL {alias}: {error}")
            else:
                results.append(f"OK {alias}")
        await interaction.followup.send("\n".join(results) or "No connections configured.", ephemeral=True)

    @discord.ui.button(label="Done", style=discord.ButtonStyle.secondary)
    async def done(self, interaction, button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class IssueDraftModal(discord.ui.Modal, title="Edit Git issue"):
    issue_title = discord.ui.TextInput(label="Title", min_length=1, max_length=256)
    description = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=False, max_length=4000)
    labels = discord.ui.TextInput(label="Labels (comma separated)", required=False, max_length=300)

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.issue_title.default = view.title[:256]
        self.description.default = view.description[:4000]
        self.labels.default = ", ".join(view.labels)[:300]

    async def on_submit(self, interaction):
        self.view.title = str(self.issue_title).strip()
        self.view.description = str(self.description).strip()
        self.view.labels = [label.strip() for label in str(self.labels).split(",") if label.strip()][:10]
        self.view.submitted = False
        await interaction.response.edit_message(embed=self.view.embed(), view=self.view)


class IssueDraftView(OwnedView):
    def __init__(self, cog, owner, guild, repo_alias, source_url=None):
        super().__init__(cog, owner, timeout=600)
        self.repo_alias = repo_alias
        self.source_url = source_url
        self.title = ""
        self.description = ""
        self.labels = []
        self.submitted = False

    def embed(self):
        embed = discord.Embed(title="Review Git issue", description=self.description[:3500] or "No description yet.", color=discord.Color.orange())
        embed.add_field(name="Repository", value=self.repo_alias, inline=False)
        embed.add_field(name="Title", value=self.title or "Not entered yet.", inline=False)
        embed.add_field(name="Labels", value=", ".join(self.labels) or "None", inline=False)
        embed.set_footer(text="Nothing is created until Submit issue is pressed.")
        return embed

    @discord.ui.button(label="Edit issue", style=discord.ButtonStyle.primary)
    async def edit(self, interaction, button):
        await interaction.response.send_modal(IssueDraftModal(self))

    @discord.ui.button(label="Submit issue", style=discord.ButtonStyle.success)
    async def submit(self, interaction, button):
        if self.submitted:
            await interaction.response.send_message("This draft is already being submitted.", ephemeral=True)
            return
        if not self.title:
            await interaction.response.send_message("Edit the draft and enter a title first.", ephemeral=True)
            return
        self.submitted = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=self.embed(), view=self)
        try:
            issue = await self.cog.submit_issue(
                interaction.guild, interaction.user, self.repo_alias, self.title,
                self.description, self.labels, source_url=self.source_url,
            )
        except (ForgeError, ValueError) as error:
            uncertain = isinstance(error, ForgeError) and error.code == "submission_uncertain"
            self.submitted = uncertain
            if not uncertain:
                for item in self.children:
                    item.disabled = False
            await interaction.edit_original_response(embed=self.embed(), view=self)
            await interaction.followup.send(str(error), ephemeral=True)
            if uncertain:
                self.stop()
            return
        result = discord.Embed(
            title=f"Issue #{issue.number} created",
            description=f"**{issue.title[:256]}**\n\n[Open issue]({issue.url})",
            color=discord.Color.green(),
        )
        await interaction.edit_original_response(embed=result, view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction, button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Issue draft cancelled.", embed=None, view=self)
        self.stop()
