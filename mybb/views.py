"""Interactive setup, discovery, and publishing views for MyBB."""

import asyncio
import time

import discord

from .client import MyBBAPIError, MyBBClient
from .membership import ExternalSignInClient, MembershipError
from .roles import RoleSyncError


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
        if not await self.view.cog.is_publisher(interaction.user):
            await interaction.response.send_message("Forum tools are limited to configured publisher roles.", ephemeral=True)
            return
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
        if not await self.cog.is_publisher(interaction.user):
            await interaction.response.send_message("Forum tools are limited to configured publisher roles.", ephemeral=True)
            return
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
        if not await self.cog.is_publisher(interaction.user):
            await interaction.response.send_message("Forum tools are limited to configured publisher roles.", ephemeral=True)
            return
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
        if not await self.cog.is_publisher(interaction.user):
            await interaction.response.send_message("Forum tools are limited to configured publisher roles.", ephemeral=True)
            return
        if not await self.cog.config.guild(interaction.guild).enabled():
            await interaction.response.send_message("The MyBB bridge is disabled.", ephemeral=True)
            return
        await interaction.response.send_modal(SearchModal(self))

    @discord.ui.button(label="Membership", style=discord.ButtonStyle.success, row=1)
    async def membership(self, interaction, button):
        link = await self.cog.config.member(interaction.user).membership_link()
        enabled = await self.cog.config.guild(interaction.guild).membership_enabled()
        await interaction.response.send_message(
            "Link your website account and manage synchronized membership roles.",
            view=MembershipView(self.cog, interaction.user, bool(link), enabled), ephemeral=True,
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
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("Only the Discord server owner can configure the API connector.", ephemeral=True)
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
            await self.view.cog.config.guild(guild).default_forum_id.clear()
            await self.view.cog.config.guild(guild).forum_aliases.clear()
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
    @discord.ui.button(label="Connect API v1", style=discord.ButtonStyle.success)
    async def connect(self, interaction, button):
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("Only the Discord server owner can configure the API connector.", ephemeral=True)
            return
        await interaction.response.send_modal(GuildConnectionModal(self))

    @discord.ui.button(label="Membership setup", style=discord.ButtonStyle.success, row=1)
    async def membership_setup(self, interaction, button):
        await interaction.response.send_modal(MembershipClientModal(self))

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


class MembershipClientModal(discord.ui.Modal, title="Configure MyBB membership linking"):
    provider_url = discord.ui.TextInput(
        label="External Sign-In URL", placeholder="https://forum.example/external-signin", max_length=500
    )
    client_id = discord.ui.TextInput(label="External Sign-In client ID", max_length=190)
    client_secret = discord.ui.TextInput(label="Client secret", max_length=500)

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Manage Server permission is required.", ephemeral=True)
            return
        try:
            client = ExternalSignInClient(str(self.provider_url), str(self.client_id), str(self.client_secret))
        except (ValueError, MembershipError) as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self.view.cog.bot.set_shared_api_tokens(
            self.view.cog.membership_guild_namespace(interaction.guild.id),
            client_id=str(self.client_id).strip(), client_secret=str(self.client_secret).strip(),
        )
        await self.view.cog.config.guild(interaction.guild).membership_enabled.set(True)
        await self.view.cog.config.guild(interaction.guild).membership_url.set(client.issuer)
        await self.view.cog.config.guild(interaction.guild).membership_next_check.set(0)
        await interaction.response.edit_message(
            content="External Sign-In membership client stored and synchronization enabled. Use your own Membership panel to test linking.",
            view=MembershipSetupView(self.view.cog, interaction.user),
        )


class MembershipSetupView(OwnedView):
    @discord.ui.button(label="Set client", style=discord.ButtonStyle.success)
    async def configure(self, interaction, button):
        await interaction.response.send_modal(MembershipClientModal(self))

    @discord.ui.button(label="Disable", style=discord.ButtonStyle.secondary)
    async def disable(self, interaction, button):
        await self.cog.config.guild(interaction.guild).membership_enabled.set(False)
        await interaction.response.edit_message(
            content="Membership synchronization disabled. Existing roles and links are preserved.", view=self,
        )

    @discord.ui.button(label="Remove client", style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        await interaction.response.defer()
        try:
            await self.cog.clear_guild_memberships(interaction.guild, notify_provider=True)
        except (MembershipError, discord.HTTPException, RoleSyncError) as error:
            await self.show_error(interaction, error)
            return
        await self.cog.bot.remove_shared_api_tokens(
            self.cog.membership_guild_namespace(interaction.guild.id), "client_id", "client_secret"
        )
        await self.cog.config.guild(interaction.guild).membership_enabled.set(False)
        await interaction.edit_original_response(
            content="Membership links were revoked, cog-managed roles removed, and the client credentials deleted.", view=self,
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.secondary)
    async def done(self, interaction, button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class MembershipView(OwnedView):
    def __init__(self, cog, owner, linked, enabled):
        super().__init__(cog, owner, timeout=660)
        self.linked = linked
        self.link.disabled = linked or not enabled
        self.sync.disabled = not linked
        self.unlink.disabled = not linked

    @discord.ui.button(label="Link MyBB", style=discord.ButtonStyle.success)
    async def link(self, interaction, button):
        if not await self.cog.config.guild(interaction.guild).membership_enabled():
            await interaction.response.send_message("Membership linking is not enabled for this server.", ephemeral=True)
            return
        if await self.cog.config.member(interaction.user).membership_link():
            await interaction.response.send_message("Your membership is already linked. Unlink it before linking another account.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            client = await self.cog.membership_client(interaction.guild)
            started = await client.start_link(
                interaction.user.id,
                interaction.user.name,
                f"guild:{interaction.guild.id}",
            )
        except MembershipError as error:
            await interaction.edit_original_response(content=f"Could not start linking: {error}")
            return
        url_view = discord.ui.View(timeout=int(started["expires_in"]))
        url_view.add_item(discord.ui.Button(label="Approve on MyBB", url=str(started["verification_uri_complete"])))
        await interaction.edit_original_response(
            content=(
                f"Open MyBB and approve code `{started['user_code']}`. "
                f"This expires in {int(started['expires_in']) // 60} minutes. "
                "Waiting for approval…"
            ),
            view=url_view,
        )
        deadline = time.monotonic() + int(started["expires_in"])
        interval = int(started["interval"])
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            try:
                result = await client.poll_link(started["device_code"])
            except MembershipError as error:
                if error.code == "authorization_pending":
                    continue
                if error.code == "slow_down":
                    try:
                        interval = max(interval + 5, int(error.retry_after or 5))
                    except ValueError:
                        interval += 5
                    continue
                message = "Linking was denied or expired. Start a new link." if error.code in {"access_denied", "expired_token"} else "Linking could not be confirmed. Start a new link; the single-use completion is never retried after an ambiguous response."
                await interaction.edit_original_response(content=message, view=None)
                return
            try:
                membership = await self.cog.finish_membership_link(interaction.user, client, result)
            except (MembershipError, discord.HTTPException, RoleSyncError) as error:
                await interaction.edit_original_response(content=f"Your accounts linked, but the first role sync did not complete. Existing roles were preserved; use Sync later. {error}", view=None)
                return
            await interaction.edit_original_response(content=f"MyBB membership linked and synchronized: `{membership['status']}`.", view=None)
            return
        await interaction.edit_original_response(content="The linking request expired. Start a new link when ready.", view=None)

    @discord.ui.button(label="Sync now", style=discord.ButtonStyle.primary)
    async def sync(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.cog.reconcile_membership(interaction.user)
            await interaction.followup.send(f"Membership synchronized: `{result['status']}`.", ephemeral=True)
        except (MembershipError, discord.HTTPException, RoleSyncError) as error:
            await interaction.followup.send(f"Sync could not complete; existing roles were preserved. {error}", ephemeral=True)

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary)
    async def status(self, interaction, button):
        group = self.cog.config.member(interaction.user)
        link = await group.membership_link()
        last = await group.membership_last_success()
        error = await group.membership_last_error()
        message = f"Linked · status `{link.get('status', 'unknown')}`" if link else "Not linked"
        if last:
            message += f" · last successful check <t:{last}:R>"
        if error:
            message += f"\nLast error: {error}"
        await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(label="Unlink", style=discord.ButtonStyle.danger)
    async def unlink(self, interaction, button):
        await interaction.response.send_message(
            "This revokes the MyBB association and removes roles granted by membership sync. Confirm?",
            view=ConfirmMembershipUnlink(self.cog, interaction.user), ephemeral=True,
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.secondary)
    async def done(self, interaction, button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class ConfirmMembershipUnlink(OwnedView):
    @discord.ui.button(label="Confirm unlink", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            await self.cog.clear_membership(interaction.guild, interaction.user.id, notify_provider=True)
        except (MembershipError, discord.HTTPException, RoleSyncError) as error:
            await interaction.followup.send(f"Unlink could not be confirmed; the link and existing roles were preserved. {error}", ephemeral=True)
            return
        await interaction.edit_original_response(content="MyBB membership unlinked and cog-managed roles removed.", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Unlink cancelled.", view=None)


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
    def __init__(
        self, cog, owner, forum_id, subject, body, source_url, message_id,
        channel_id, guild_id, *, forum_alias=None
    ):
        super().__init__(cog, owner, timeout=600)
        self.forum_id = int(forum_id)
        self.subject = subject[:120]
        self.body = body
        self.source_url = source_url
        self.message_id = int(message_id)
        self.channel_id = int(channel_id)
        self.guild_id = int(guild_id)
        self.forum_alias = forum_alias

    def embed(self):
        embed = discord.Embed(title="Review MyBB thread", description=self.body[:4000], color=discord.Color.orange())
        embed.add_field(name="Subject", value=self.subject, inline=False)
        destination = (
            f"`{self.forum_alias}` → forum `{self.forum_id}`"
            if self.forum_alias
            else f"Forum ID `{self.forum_id}`"
        )
        embed.add_field(name="Destination", value=destination)
        embed.add_field(name="Source", value=f"[Discord message]({self.source_url})")
        embed.set_footer(text="Uses the server owner's restricted API connector. Nothing is published until Publish is pressed.")
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
        if not await self.cog.is_publisher(interaction.user):
            await interaction.response.send_message(
                "You no longer have a configured forum-publisher role.", ephemeral=True
            )
            return
        if self.forum_id not in set(await self.cog.config.guild(interaction.guild).publishing_forum_ids()):
            await interaction.response.send_message(
                "This destination is no longer in the publishing allowlist.", ephemeral=True
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
