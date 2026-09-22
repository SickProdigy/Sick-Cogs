"""Interactive setup, discovery, and publishing views for MyBB."""

import discord

from .client import MyBBAPIError, MyBBClient


class OwnedView(discord.ui.View):
    def __init__(self, cog, owner, *, timeout=300):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.owner = owner

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Open your own MyBB panel to use these controls.", ephemeral=True
            )
            return False
        return True

    async def show_error(self, interaction, error):
        message = str(error) if isinstance(error, MyBBAPIError) else "The MyBB action could not be completed."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class SearchModal(discord.ui.Modal, title="Search recent MyBB threads"):
    query = discord.ui.TextInput(label="Search terms", min_length=2, max_length=100)

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction):
        forum_id = await self.view.cog.config.guild(interaction.guild).default_forum_id()
        if not forum_id:
            await interaction.response.send_message(
                "A server administrator must set the default forum before using recent search.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            matches = await self.view.cog.search_recent(
                interaction.guild, interaction.user, forum_id, str(self.query).strip()
            )
            description = "\n".join(
                self.view.cog.thread_line(item) for item in matches
            ) or "No matching recent threads were found."
            embed = discord.Embed(
                title=f"MyBB recent search • {str(self.query)[:100]}",
                description=description,
                color=discord.Color.blurple(),
            )
            embed.set_footer(
                text="Checks up to 300 recent threads in the default forum; full-text API search is planned."
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except MyBBAPIError as error:
            await self.view.show_error(interaction, error)


class MyBBHome(OwnedView):
    @discord.ui.button(label="Forums", style=discord.ButtonStyle.primary)
    async def forums(self, interaction, button):
        if not await self.cog.config.guild(interaction.guild).enabled():
            await interaction.response.send_message("The MyBB bridge is disabled.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            response = await self.cog.request(
                interaction.guild, interaction.user, "GET", "/forums"
            )
            forums = self.cog.data_list(response, "forums")
            description = "\n".join(
                self.cog.forum_line(item) for item in forums[:25]
            ) or "No readable forums were returned."
            await interaction.followup.send(
                embed=discord.Embed(
                    title="MyBB forums", description=description, color=discord.Color.blurple()
                ),
                ephemeral=True,
            )
        except MyBBAPIError as error:
            await self.show_error(interaction, error)

    @discord.ui.button(label="Recent threads", style=discord.ButtonStyle.primary)
    async def recent(self, interaction, button):
        if not await self.cog.config.guild(interaction.guild).enabled():
            await interaction.response.send_message("The MyBB bridge is disabled.", ephemeral=True)
            return
        forum_id = await self.cog.config.guild(interaction.guild).default_forum_id()
        if not forum_id:
            await interaction.response.send_message(
                "Set a default forum before using this button.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            response = await self.cog.request(
                interaction.guild,
                interaction.user,
                "GET",
                f"/forums/{forum_id}/threads",
                params={"page": 1, "per_page": 20},
            )
            threads = self.cog.data_list(response, "threads")
            description = "\n".join(
                self.cog.thread_line(item) for item in threads[:20]
            ) or "No readable threads were returned."
            await interaction.followup.send(
                embed=discord.Embed(
                    title=f"Recent MyBB threads • forum {forum_id}",
                    description=description,
                    color=discord.Color.blurple(),
                ),
                ephemeral=True,
            )
        except MyBBAPIError as error:
            await self.show_error(interaction, error)

    @discord.ui.button(label="Search", style=discord.ButtonStyle.primary)
    async def search(self, interaction, button):
        if not await self.cog.config.guild(interaction.guild).enabled():
            await interaction.response.send_message("The MyBB bridge is disabled.", ephemeral=True)
            return
        await interaction.response.send_modal(SearchModal(self))

    @discord.ui.button(label="My connection", style=discord.ButtonStyle.secondary)
    async def connection(self, interaction, button):
        connected = await self.cog.has_personal_connection(
            interaction.guild.id, interaction.user.id
        )
        await interaction.response.send_message(
            "Your personal MyBB connection is active. Use the text command `mybb connect` to replace or remove it."
            if connected
            else "You are using the server connector. Use the text command `mybb connect` to add personal access.",
            ephemeral=True,
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.secondary)
    async def done(self, interaction, button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class GuildConnectionModal(discord.ui.Modal, title="Connect this Discord server to MyBB"):
    board_url = discord.ui.TextInput(
        label="MyBB board URL", placeholder="https://forum.example.com", max_length=500
    )
    token = discord.ui.TextInput(
        label="Server connector token", placeholder="Paste a restricted API token", max_length=500
    )

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Manage Server permission is required.", ephemeral=True)
            return
        try:
            client = MyBBClient(str(self.board_url), str(self.token))
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await interaction.response.defer()
        try:
            health = await client.health()
            forums_response = await client.forums()
        except MyBBAPIError as error:
            await self.view.show_error(interaction, error)
            return
        guild = interaction.guild
        old_url = await self.view.cog.config.guild(guild).board_url()
        if old_url and old_url != client.base_url:
            await self.view.cog.clear_guild_member_connections(guild)
        await self.view.cog.bot.set_shared_api_tokens(
            self.view.cog.guild_namespace(guild.id), token=str(self.token).strip()
        )
        await self.view.cog.config.guild(guild).board_url.set(client.base_url)
        await self.view.cog.config.guild(guild).enabled.set(True)
        forums = self.view.cog.data_list(forums_response, "forums")
        data = health.get("data", {})
        content = (
            f"Connected this Discord server to MyBB API "
            f"{data.get('version') or data.get('api_version') or 'v1'} with access to {len(forums)} forum(s)."
        )
        await interaction.edit_original_response(
            content=content,
            embed=await self.view.cog.settings_embed(guild),
            view=MyBBSetupView(self.view.cog, interaction.user),
        )


class DefaultForumModal(discord.ui.Modal, title="Choose the default MyBB forum"):
    forum_id = discord.ui.TextInput(label="Forum ID", min_length=1, max_length=20)

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction):
        try:
            forum_id = int(str(self.forum_id).strip())
            if forum_id <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Enter a positive numeric forum ID.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            client = await self.view.cog.client_for(interaction.guild, interaction.user)
            await client.threads(forum_id, page=1, per_page=1)
        except MyBBAPIError as error:
            await self.view.show_error(interaction, error)
            return
        await self.view.cog.config.guild(interaction.guild).default_forum_id.set(forum_id)
        await interaction.edit_original_response(
            content=f"Default forum set to `{forum_id}`.",
            embed=await self.view.cog.settings_embed(interaction.guild),
            view=MyBBSetupView(self.view.cog, interaction.user),
        )


class MyBBSetupView(OwnedView):
    @discord.ui.button(label="Connect board", style=discord.ButtonStyle.success)
    async def connect(self, interaction, button):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Manage Server permission is required.", ephemeral=True)
            return
        await interaction.response.send_modal(GuildConnectionModal(self))

    @discord.ui.button(label="Default forum", style=discord.ButtonStyle.primary)
    async def default_forum(self, interaction, button):
        await interaction.response.send_modal(DefaultForumModal(self))

    @discord.ui.button(label="Enable / disable", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction, button):
        group = self.cog.config.guild(interaction.guild)
        enabled = not await group.enabled()
        await group.enabled.set(enabled)
        await interaction.response.edit_message(
            content=f"MyBB bridge {'enabled' if enabled else 'disabled'}.",
            embed=await self.cog.settings_embed(interaction.guild),
            view=MyBBSetupView(self.cog, interaction.user),
        )

    @discord.ui.button(label="Test", style=discord.ButtonStyle.secondary)
    async def test(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            client = await self.cog.client_for(interaction.guild)
            health = await client.health()
            forums = self.cog.data_list(await client.forums(), "forums")
            data = health.get("data", {})
            await interaction.followup.send(
                f"Connected to MyBB API {data.get('version') or data.get('api_version') or 'v1'}; {len(forums)} forum(s) are readable.",
                ephemeral=True,
            )
        except MyBBAPIError as error:
            await self.show_error(interaction, error)

    @discord.ui.button(label="Done", style=discord.ButtonStyle.secondary)
    async def done(self, interaction, button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class PersonalConnectionModal(discord.ui.Modal, title="Connect your MyBB account"):
    token = discord.ui.TextInput(
        label="Your personal API token", placeholder="Use a scoped, revocable token", max_length=500
    )

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction):
        board_url = await self.view.cog.config.guild(interaction.guild).board_url()
        if not board_url:
            await interaction.response.send_message("This server has no connected MyBB board.", ephemeral=True)
            return
        try:
            client = MyBBClient(board_url, str(self.token))
        except ValueError as error:
            await interaction.response.send_message(
                f"The personal connection was not saved: {error}", ephemeral=True
            )
            return
        await interaction.response.defer()
        try:
            await client.forums()
        except MyBBAPIError as error:
            await self.view.show_error(interaction, error)
            return
        namespace = self.view.cog.member_namespace(interaction.guild.id, interaction.user.id)
        await self.view.cog.bot.set_shared_api_tokens(
            namespace, token=str(self.token).strip()
        )
        await self.view.cog.config.member(interaction.user).connected.set(True)
        await interaction.edit_original_response(
            content="Your personal MyBB connection is active for this Discord server. MyBB permissions still control what you can read or publish.",
            view=PersonalConnectionView(self.view.cog, interaction.user, True),
        )


class PersonalConnectionView(OwnedView):
    def __init__(self, cog, owner, connected):
        super().__init__(cog, owner)
        self.connected = connected

    @discord.ui.button(label="Add / replace connection", style=discord.ButtonStyle.success)
    async def add(self, interaction, button):
        await interaction.response.send_modal(PersonalConnectionModal(self))

    @discord.ui.button(label="Remove connection", style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        await self.cog.bot.remove_shared_api_tokens(
            self.cog.member_namespace(interaction.guild.id, interaction.user.id), "token"
        )
        await self.cog.config.member(interaction.user).connected.set(False)
        await interaction.response.edit_message(
            content="Your personal MyBB connection has been removed.",
            view=PersonalConnectionView(self.cog, interaction.user, False),
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.secondary)
    async def done(self, interaction, button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class EditDraftModal(discord.ui.Modal, title="Edit MyBB draft"):
    subject = discord.ui.TextInput(label="Subject", max_length=120)
    body = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph, max_length=4000)

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.subject.default = view.subject
        self.body.default = view.body[:4000]

    async def on_submit(self, interaction):
        self.view.subject = str(self.subject).strip()
        self.view.body = str(self.body).strip()
        await interaction.response.edit_message(embed=self.view.embed(), view=self.view)


class PublishReview(OwnedView):
    def __init__(self, cog, owner, forum_id, subject, body, source_url, message_id, channel_id, guild_id):
        super().__init__(cog, owner, timeout=600)
        self.forum_id = int(forum_id)
        self.subject = subject[:120]
        self.body = body
        self.source_url = source_url
        self.message_id = int(message_id)
        self.channel_id = int(channel_id)
        self.guild_id = int(guild_id)

    def embed(self):
        embed = discord.Embed(title="Review MyBB thread", description=self.body[:4000], color=discord.Color.orange())
        embed.add_field(name="Subject", value=self.subject, inline=False)
        embed.add_field(name="Destination", value=f"Forum ID `{self.forum_id}`")
        embed.add_field(name="Source", value=f"[Discord message]({self.source_url})")
        embed.set_footer(text="Personal access is preferred. Nothing is published until Publish is pressed.")
        return embed

    def payload(self):
        return {
            "forum_id": self.forum_id,
            "source": "discord",
            "source_id": f"{self.guild_id}-{self.channel_id}-{self.message_id}",
            "source_url": self.source_url,
            "subject": self.subject,
            "message": self.body,
        }

    @discord.ui.button(label="Publish", style=discord.ButtonStyle.success)
    async def publish(self, interaction, button):
        if not await self.cog.config.guild(interaction.guild).enabled():
            await interaction.response.send_message("The MyBB bridge is disabled.", ephemeral=True)
            return
        personal = await self.cog.has_personal_connection(interaction.guild.id, interaction.user.id)
        if not personal and not await self.cog.is_manager(interaction.user):
            await interaction.response.send_message(
                "Connect your MyBB account or ask a configured MyBB manager to publish this.", ephemeral=True
            )
            return
        await interaction.response.defer()
        try:
            client = await self.cog.client_for(interaction.guild, interaction.user)
            key = f"discord-{self.guild_id}-{self.channel_id}-{self.message_id}"
            response = await client.publish_thread(self.payload(), key[:128])
            data = response.get("data", {})
            thread_id = data.get("thread_id") or data.get("tid")
            url = data.get("thread_url") or data.get("url")
            result = f"Published **{self.subject}**" + (f" as thread `{thread_id}`" if thread_id else "") + (f". [Open thread]({url})" if url else ".")
            for item in self.children:
                item.disabled = True
            await interaction.edit_original_response(
                embed=discord.Embed(title="MyBB thread published", description=result, color=discord.Color.green()),
                view=self,
            )
            self.stop()
        except MyBBAPIError as error:
            await self.show_error(interaction, error)

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary)
    async def edit(self, interaction, button):
        await interaction.response.send_modal(EditDraftModal(self))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction, button):
        for item in self.children:
            item.disabled = True
        embed = self.embed()
        embed.title = "MyBB draft cancelled"
        embed.color = discord.Color.red()
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()
