from __future__ import annotations

import datetime
import re
import secrets
from typing import Dict

import discord

from .client import NavidromeError, validate_base_url, validate_public_base_url
from .lidarr import LidarrError


async def owner_check(interaction: discord.Interaction, author: discord.abc.User) -> bool:
    if interaction.user.id != author.id:
        await interaction.response.send_message(
            "Only the administrator who opened this setup panel can use it.", ephemeral=True
        )
        return False
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Manage Server permission is required.", ephemeral=True)
        return False
    return True


class ConnectionModal(discord.ui.Modal):
    def __init__(self, cog):
        super().__init__(title="Connect a Navidrome server")
        self.cog = cog
        self.name = discord.ui.TextInput(
            label="Connection name", placeholder="home", min_length=1, max_length=32
        )
        self.url = discord.ui.TextInput(
            label="Navidrome URL", placeholder="https://music.example.com", max_length=500
        )
        self.username = discord.ui.TextInput(
            label="Navidrome admin username", max_length=100
        )
        self.password = discord.ui.TextInput(
            label="Admin password (not posted)", max_length=500
        )
        self.allow_http = discord.ui.TextInput(
            label="Allow private HTTP? (yes/no)", default="no", max_length=3
        )
        for item in (self.name, self.url, self.username, self.password, self.allow_http):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.cog.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "Only the bot owner can save Navidrome server credentials.", ephemeral=True
            )
            return
        name = str(self.name.value).strip().lower()
        if not name or not re.fullmatch(r"[a-z0-9_-]+", name):
            await interaction.response.send_message(
                "Connection names may contain only letters, numbers, underscores, and hyphens.",
                ephemeral=True,
            )
            return
        allow_http_value = str(self.allow_http.value).strip().casefold()
        if allow_http_value not in {"yes", "no"}:
            await interaction.response.send_message(
                "Enter `yes` or `no` for private HTTP.", ephemeral=True
            )
            return
        allow_http = allow_http_value == "yes"
        try:
            base_url = validate_base_url(str(self.url.value), allow_http=allow_http)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        username = str(self.username.value).strip()
        password = str(self.password.value)
        if not username or not password:
            await interaction.response.send_message(
                "The Navidrome admin username and password are required.", ephemeral=True
            )
            return

        await interaction.response.defer()
        namespace = f"navidrome_{name}"
        previous_tokens = await self.cog.bot.get_shared_api_tokens(namespace)
        profiles = await self.cog.config.connections()
        previous_profile = profiles.get(name)
        await self.cog.bot.set_shared_api_tokens(
            namespace, username=username, password=password
        )
        profiles[name] = {"base_url": base_url, "allow_http": allow_http}
        await self.cog.config.connections.set(profiles)
        try:
            client = await self.cog._client(name)
            ping = await client.ping()
            users = await client.users()
        except NavidromeError as exc:
            if previous_tokens:
                await self.cog.bot.set_shared_api_tokens(namespace, **previous_tokens)
            else:
                await self.cog.bot.remove_shared_api_tokens(
                    namespace, "username", "password"
                )
            if previous_profile is None:
                profiles.pop(name, None)
            else:
                profiles[name] = previous_profile
            await self.cog.config.connections.set(profiles)
            await interaction.followup.send(
                f"Connection was not saved: {exc}", ephemeral=True
            )
            return

        group = self.cog.config.guild(interaction.guild)
        previous_mode = await group.connection_mode()
        previous_name = await group.connection()
        changed_server = previous_mode != "owner_managed" or previous_name != name
        if previous_mode == "guild_managed":
            await self.cog.bot.remove_shared_api_tokens(
                self.cog.guild_token_namespace(interaction.guild.id),
                "username", "password", "lidarr_url", "lidarr_api_key",
            )
            await group.guild_connection.set(None)
        await group.connection_mode.set("owner_managed")
        await group.connection.set(name)
        await self.cog._reset_connection_state(
            interaction.guild, clear_accounts=changed_server
        )
        server = str(ping.get("type") or "Navidrome")
        version = str(ping.get("serverVersion") or "unknown version")
        await interaction.edit_original_response(
            content=(
                f"Connected `{name}` to {server} {version}. "
                f"User management is ready ({len(users)} users visible)."
            ),
            embed=await self.cog.setup_embed(interaction.guild),
            view=await NavidromeSetupView.create(
                self.cog, interaction.user, interaction.guild
            ),
        )


class GuildConnectionModal(discord.ui.Modal, title="Connect this server to Navidrome"):
    url = discord.ui.TextInput(
        label="Public Navidrome HTTPS URL",
        placeholder="https://music.example.com",
        max_length=500,
    )
    username = discord.ui.TextInput(label="Navidrome admin username", max_length=100)
    password = discord.ui.TextInput(label="Admin password (stored securely)", max_length=500)
    confirmation = discord.ui.TextInput(
        label="Type CONNECT to confirm replacement", min_length=7, max_length=7
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "Manage Server permission is required.", ephemeral=True
            )
            return
        if not await self.cog.config.guild_connections_enabled():
            await interaction.response.send_message(
                "The bot owner has disabled guild-managed Navidrome connections.",
                ephemeral=True,
            )
            return
        if str(self.confirmation.value).strip().casefold() != "connect":
            await interaction.response.send_message(
                "Type `CONNECT` to confirm storing or replacing this server's credentials.",
                ephemeral=True,
            )
            return
        if not self.cog.claim_connection_test(interaction.guild.id):
            await interaction.response.send_message(
                "Please wait one minute before testing another guild-managed connection.",
                ephemeral=True,
            )
            return
        try:
            base_url = await validate_public_base_url(str(self.url.value))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        username = str(self.username.value).strip()
        password = str(self.password.value)
        if not username or not password:
            await interaction.response.send_message(
                "The Navidrome admin username and password are required.", ephemeral=True
            )
            return

        await interaction.response.defer()
        guild = interaction.guild
        group = self.cog.config.guild(guild)
        previous = await group.all()
        namespace = self.cog.guild_token_namespace(guild.id)
        previous_tokens = await self.cog.bot.get_shared_api_tokens(namespace)
        await self.cog.bot.set_shared_api_tokens(
            namespace, username=username, password=password
        )
        await group.connection_mode.set("guild_managed")
        staged_profile = {"base_url": base_url}
        old_profile = previous.get("guild_connection") or {}
        if (
            previous.get("connection_mode") == "guild_managed"
            and old_profile.get("base_url") == base_url
            and old_profile.get("lidarr")
        ):
            staged_profile["lidarr"] = old_profile["lidarr"]
        await group.guild_connection.set(staged_profile)
        try:
            _, client = await self.cog._guild_client(guild)
            ping = await client.ping()
            users = await client.users()
        except NavidromeError as exc:
            if previous_tokens:
                await self.cog.bot.set_shared_api_tokens(namespace, **previous_tokens)
            else:
                await self.cog.bot.remove_shared_api_tokens(
                    namespace, "username", "password"
                )
            await group.connection_mode.set(
                previous.get("connection_mode", "owner_managed")
            )
            await group.guild_connection.set(previous.get("guild_connection"))
            await interaction.followup.send(
                f"Guild connection was not saved: {exc}", ephemeral=True
            )
            return

        changed_server = (
            previous.get("connection_mode") != "guild_managed"
            or (previous.get("guild_connection") or {}).get("base_url") != base_url
        )
        if changed_server:
            await self.cog.bot.remove_shared_api_tokens(
                namespace, "lidarr_url", "lidarr_api_key"
            )
        await group.connection.set(None)
        await self.cog._reset_connection_state(guild, clear_accounts=changed_server)
        server = str(ping.get("type") or "Navidrome")
        version = str(ping.get("serverVersion") or "unknown version")
        await interaction.edit_original_response(
            content=(
                f"Connected this Discord server to {server} {version}. "
                f"User management is ready ({len(users)} users visible)."
            ),
            embed=await self.cog.setup_embed(guild),
            view=await NavidromeSetupView.create(self.cog, interaction.user, guild),
        )


class GuildLidarrModal(discord.ui.Modal, title="Connect this server to Lidarr"):
    url = discord.ui.TextInput(label="Public Lidarr HTTPS URL", max_length=500)
    api_key = discord.ui.TextInput(label="Lidarr API key (stored securely)", max_length=500)
    root = discord.ui.TextInput(label="Root folder path", placeholder="/music", max_length=500)
    profiles = discord.ui.TextInput(label="Quality ID, metadata ID", placeholder="3,4", max_length=30)
    confirmation = discord.ui.TextInput(label="Type CONNECT to confirm", min_length=7, max_length=7)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(
                "Manage Server permission is required.", ephemeral=True
            )
        if str(self.confirmation.value).strip().casefold() != "connect":
            return await interaction.response.send_message(
                "Type `CONNECT` to confirm storing or replacing Lidarr credentials.", ephemeral=True
            )
        group = self.cog.config.guild(interaction.guild)
        profile = await group.guild_connection()
        if not profile or await group.connection_mode() != "guild_managed":
            return await interaction.response.send_message(
                "Connect this guild's Navidrome server before adding guild-owned Lidarr.",
                ephemeral=True,
            )
        try:
            base_url = await validate_public_base_url(str(self.url.value))
            parts = [int(value.strip()) for value in str(self.profiles.value).split(",")]
            if len(parts) != 2 or any(value < 1 for value in parts):
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "Enter a public HTTPS URL and profile IDs as `quality,metadata`.", ephemeral=True
            )
        api_key = str(self.api_key.value).strip()
        root = str(self.root.value).strip()
        if not api_key or not root:
            return await interaction.response.send_message(
                "The API key and root folder are required.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        namespace = self.cog.guild_token_namespace(interaction.guild.id)
        previous_tokens = await self.cog.bot.get_shared_api_tokens(namespace)
        previous_profile = dict(profile)
        new_profile = dict(profile)
        new_profile["lidarr"] = {
            "enabled": True, "root_folder_path": root, "quality_profile_id": parts[0],
            "metadata_profile_id": parts[1], "monitor": "all",
        }
        await self.cog.bot.set_shared_api_tokens(
            namespace, lidarr_url=base_url, lidarr_api_key=api_key
        )
        await group.guild_connection.set(new_profile)
        try:
            _, client, settings = await self.cog._lidarr_client(interaction.guild)
            status = await client.validate_configuration(
                root_folder_path=settings["root_folder_path"],
                quality_profile_id=settings["quality_profile_id"],
                metadata_profile_id=settings["metadata_profile_id"],
            )
        except LidarrError as exc:
            if previous_tokens.get("lidarr_url") and previous_tokens.get("lidarr_api_key"):
                await self.cog.bot.set_shared_api_tokens(
                    namespace, lidarr_url=previous_tokens["lidarr_url"],
                    lidarr_api_key=previous_tokens["lidarr_api_key"],
                )
            else:
                await self.cog.bot.remove_shared_api_tokens(
                    namespace, "lidarr_url", "lidarr_api_key"
                )
            await group.guild_connection.set(previous_profile)
            return await interaction.followup.send(
                f"Lidarr was not saved: {exc}", ephemeral=True
            )
        await interaction.followup.send(
            f"Lidarr {status.get('version', 'unknown')} is enabled. The API key will not be displayed.",
            ephemeral=True,
        )


class IntervalModal(discord.ui.Modal):
    def __init__(self, cog, author: discord.abc.User, current: int):
        super().__init__(title="Navidrome polling interval")
        self.cog, self.author = cog, author
        self.minutes = discord.ui.TextInput(
            label="Minutes (15-1440)", default=str(current), min_length=1, max_length=4
        )
        self.add_item(self.minutes)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            minutes = int(str(self.minutes.value).strip())
        except ValueError:
            await interaction.response.send_message("Enter a whole number of minutes.", ephemeral=True)
            return
        if not 15 <= minutes <= 1440:
            await interaction.response.send_message(
                "The interval must be between 15 and 1440 minutes.", ephemeral=True
            )
            return
        await self.cog.config.guild(interaction.guild).interval_minutes.set(minutes)
        await self.cog._set_next_check(interaction.guild, minutes)
        await interaction.response.edit_message(
            content=f"Polling interval set to {minutes} minutes.",
            embed=await self.cog.setup_embed(interaction.guild),
            view=await NavidromeSetupView.create(self.cog, interaction.user, interaction.guild),
        )


class ConnectionSelect(discord.ui.Select):
    def __init__(self, parent: "NavidromeSetupView", profiles: Dict[str, dict], selected: str | None):
        options = [
            discord.SelectOption(
                label=name[:100],
                value=name,
                description=str(profile.get("base_url") or "Approved connection")[:100],
                default=name == selected,
            )
            for name, profile in sorted(profiles.items())[:25]
        ]
        super().__init__(placeholder="Choose an approved Navidrome connection", options=options, row=0)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        name = self.values[0]
        try:
            await (await self.parent_view.cog._client(name)).ping()
        except NavidromeError as exc:
            await interaction.followup.send(f"Connection test failed: {exc}", ephemeral=True)
            return
        group = self.parent_view.cog.config.guild(interaction.guild)
        previous_mode = await group.connection_mode()
        previous_name = await group.connection()
        changed_server = previous_mode != "owner_managed" or previous_name != name
        if previous_mode == "guild_managed":
            await self.parent_view.cog.bot.remove_shared_api_tokens(
                self.parent_view.cog.guild_token_namespace(interaction.guild.id),
                "username", "password", "lidarr_url", "lidarr_api_key",
            )
            await group.guild_connection.set(None)
        await group.connection_mode.set("owner_managed")
        await group.connection.set(name)
        await self.parent_view.cog._reset_connection_state(
            interaction.guild, clear_accounts=changed_server
        )
        await interaction.edit_original_response(
            content=f"Selected `{name}`. Announcements remain disabled.",
            embed=await self.parent_view.cog.setup_embed(interaction.guild),
            view=await NavidromeSetupView.create(
                self.parent_view.cog, interaction.user, interaction.guild
            ),
        )


class AnnouncementChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "NavidromeSetupView"):
        super().__init__(
            placeholder="Optional: choose an album notification channel",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
            row=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        resolved = await self.parent_view.cog._channel(interaction.guild, int(channel.id))
        if not resolved:
            await interaction.response.send_message(
                "I cannot send messages in that channel.", ephemeral=True
            )
            return
        await self.parent_view.cog.config.guild(interaction.guild).announcement_channel_id.set(
            int(channel.id)
        )
        await interaction.response.edit_message(
            content=f"Announcement channel set to <#{channel.id}>.",
            embed=await self.parent_view.cog.setup_embed(interaction.guild),
            view=await NavidromeSetupView.create(
                self.parent_view.cog, interaction.user, interaction.guild
            ),
        )


async def account_state(cog, guild: discord.Guild, member: discord.Member):
    account = (await cog.config.guild(guild).accounts()).get(str(member.id))
    if not account:
        return None, None
    try:
        _, client = await cog._guild_client(guild)
        remote = await client.user_by_username(str(account.get("username") or ""))
    except NavidromeError:
        remote = None
    return account, remote


def account_embed(member: discord.Member, account: dict | None, remote: dict | None):
    embed = discord.Embed(
        title="Navidrome account manager",
        description=f"Managing {member.mention}",
        colour=discord.Colour.blurple(),
    )
    if not account:
        embed.add_field(
            name="Status", value="No Navidrome account is linked through this server.", inline=False
        )
        return embed
    embed.add_field(name="Username", value=str(account.get("username") or "Unknown"), inline=True)
    embed.add_field(
        name="Remote account",
        value="Found" if remote else "Missing or unavailable",
        inline=True,
    )
    if remote:
        embed.add_field(name="Display name", value=str(remote.get("name") or "Not set"), inline=True)
        embed.add_field(name="Email", value=str(remote.get("email") or "Not set"), inline=True)
        embed.add_field(
            name="Administrator", value="Yes" if remote.get("isAdmin") else "No", inline=True
        )
    return embed


class AccountCreateModal(discord.ui.Modal):
    def __init__(self, cog, author, member: discord.Member):
        super().__init__(title="Create Navidrome account")
        self.cog, self.author, self.member = cog, author, member
        suggested = re.sub(r"[^A-Za-z0-9._-]", "", member.display_name)[:64]
        self.username = discord.ui.TextInput(
            label="Username", default=suggested if len(suggested) >= 3 else "", max_length=64
        )
        self.email = discord.ui.TextInput(
            label="Email (optional)", required=False, max_length=254
        )
        self.add_item(self.username)
        self.add_item(self.email)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        username = str(self.username.value).strip()
        email = str(self.email.value).strip()
        if not 3 <= len(username) <= 64 or not re.fullmatch(r"[A-Za-z0-9._-]+", username):
            await interaction.followup.send(
                "Usernames must be 3-64 characters using letters, numbers, dots, underscores, or hyphens.",
                ephemeral=True,
            )
            return
        if email and ("@" not in email or len(email) > 254):
            await interaction.followup.send("Enter a valid email address or leave it blank.", ephemeral=True)
            return
        group = self.cog.config.guild(interaction.guild)
        accounts = await group.accounts()
        if str(self.member.id) in accounts:
            await interaction.followup.send("That member already has a linked account.", ephemeral=True)
            return
        try:
            _, client = await self.cog._guild_client(interaction.guild)
            if await client.user_by_username(username):
                await interaction.followup.send("That Navidrome username already exists.", ephemeral=True)
                return
            password = secrets.token_urlsafe(18)
            remote = await client.create_user(
                username, password, name=self.member.display_name[:100], email=email
            )
            try:
                await self.member.send(
                    f"Your Navidrome account for **{interaction.guild.name}** is ready.\n"
                    f"Username: `{username}`\nTemporary password: `{password}`\n"
                    "Sign in and change this password as soon as possible."
                )
            except discord.HTTPException:
                await client.delete_user(str(remote.get("id") or ""))
                await interaction.followup.send(
                    "I could not DM that member, so account creation was rolled back.",
                    ephemeral=True,
                )
                return
        except NavidromeError as exc:
            await interaction.followup.send(f"Account creation failed: {exc}", ephemeral=True)
            return
        accounts[str(self.member.id)] = {
            "id": str(remote.get("id") or ""),
            "username": str(remote.get("userName") or username),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        await group.accounts.set(accounts)
        await interaction.edit_original_response(
            content="Account created. Credentials were sent to the member by DM.",
            embed=account_embed(self.member, accounts[str(self.member.id)], remote),
            view=UserActionView(self.cog, interaction.user, self.member, accounts[str(self.member.id)], remote),
        )


class AccountEditModal(discord.ui.Modal):
    def __init__(self, cog, author, member, account, remote):
        super().__init__(title="Edit Navidrome account")
        self.cog, self.author, self.member, self.account, self.remote = (
            cog, author, member, account, remote
        )
        self.display_name = discord.ui.TextInput(
            label="Display name", default=str((remote or {}).get("name") or ""), max_length=100
        )
        self.email = discord.ui.TextInput(
            label="Email", default=str((remote or {}).get("email") or ""),
            required=False, max_length=254,
        )
        self.add_item(self.display_name)
        self.add_item(self.email)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        email = str(self.email.value).strip()
        if email and ("@" not in email or len(email) > 254):
            await interaction.followup.send("Enter a valid email address or leave it blank.", ephemeral=True)
            return
        try:
            _, client = await self.cog._guild_client(interaction.guild)
            remote = await client.user_by_username(str(self.account.get("username") or ""))
            if not remote:
                await interaction.followup.send("The linked Navidrome user no longer exists.", ephemeral=True)
                return
            remote = await client.update_user(
                remote, name=str(self.display_name.value).strip()[:100], email=email
            )
        except NavidromeError as exc:
            await interaction.followup.send(f"Account update failed: {exc}", ephemeral=True)
            return
        await interaction.edit_original_response(
            content="Navidrome account updated.",
            embed=account_embed(self.member, self.account, remote),
            view=UserActionView(self.cog, interaction.user, self.member, self.account, remote),
        )


class UserSelect(discord.ui.UserSelect):
    def __init__(self, parent: "AccountManagerView"):
        super().__init__(placeholder="Choose a Discord member", min_values=1, max_values=1, row=0)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(int(self.values[0].id))
        if not member or member.bot:
            await interaction.response.send_message("Choose a non-bot member of this server.", ephemeral=True)
            return
        await interaction.response.defer()
        account, remote = await account_state(self.parent_view.cog, interaction.guild, member)
        await interaction.edit_original_response(
            content=None,
            embed=account_embed(member, account, remote),
            view=UserActionView(
                self.parent_view.cog, interaction.user, member, account, remote
            ),
        )


def direct_account_embed(remote: dict, linked_member_id=None):
    embed = discord.Embed(
        title=f"Navidrome user: {remote.get('userName') or 'Unknown'}",
        colour=discord.Colour.blurple(),
    )
    embed.add_field(name="Display name", value=str(remote.get("name") or "Not set"), inline=True)
    embed.add_field(name="Email", value=str(remote.get("email") or "Not set"), inline=True)
    embed.add_field(name="Administrator", value="Yes" if remote.get("isAdmin") else "No", inline=True)
    embed.add_field(
        name="Discord link",
        value=f"<@{linked_member_id}>" if linked_member_id else "Not linked",
        inline=False,
    )
    return embed


class DirectAccountCreateModal(discord.ui.Modal):
    def __init__(self, cog):
        super().__init__(title="Create a Navidrome user")
        self.cog = cog
        self.username = discord.ui.TextInput(label="Username", min_length=3, max_length=64)
        self.display_name = discord.ui.TextInput(label="Display name", required=False, max_length=100)
        self.email = discord.ui.TextInput(label="Email (optional)", required=False, max_length=254)
        for item in (self.username, self.display_name, self.email):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        username = str(self.username.value).strip()
        email = str(self.email.value).strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,64}", username):
            await interaction.followup.send(
                "Usernames must be 3-64 characters using letters, numbers, dots, underscores, or hyphens.",
                ephemeral=True,
            )
            return
        if email and ("@" not in email or len(email) > 254):
            await interaction.followup.send("Enter a valid email address or leave it blank.", ephemeral=True)
            return
        try:
            _, client = await self.cog._guild_client(interaction.guild)
            if await client.user_by_username(username):
                await interaction.followup.send("That Navidrome username already exists.", ephemeral=True)
                return
            password = secrets.token_urlsafe(18)
            await client.create_user(
                username, password,
                name=str(self.display_name.value).strip()[:100] or username,
                email=email,
            )
        except NavidromeError as exc:
            await interaction.followup.send(f"Account creation failed: {exc}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Created Navidrome user {username}.\nTemporary password: {password}\n"
            "Copy it now and have the user change it after signing in; the bot does not store it.",
            ephemeral=True,
        )
        embed, view = await DirectUsersView.create(self.cog, interaction.user, interaction.guild)
        await interaction.edit_original_response(content=None, embed=embed, view=view)


class DirectAccountEditModal(discord.ui.Modal):
    def __init__(self, cog, remote, linked_member_id=None):
        super().__init__(title="Edit Navidrome user")
        self.cog, self.remote, self.linked_member_id = cog, remote, linked_member_id
        self.display_name = discord.ui.TextInput(
            label="Display name", default=str(remote.get("name") or ""), max_length=100
        )
        self.email = discord.ui.TextInput(
            label="Email", default=str(remote.get("email") or ""), required=False, max_length=254
        )
        self.add_item(self.display_name)
        self.add_item(self.email)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        email = str(self.email.value).strip()
        if email and ("@" not in email or len(email) > 254):
            await interaction.followup.send("Enter a valid email address or leave it blank.", ephemeral=True)
            return
        try:
            _, client = await self.cog._guild_client(interaction.guild)
            remote = await client.update_user(
                self.remote, name=str(self.display_name.value).strip()[:100], email=email
            )
        except NavidromeError as exc:
            await interaction.followup.send(f"Account update failed: {exc}", ephemeral=True)
            return
        await interaction.edit_original_response(
            content="Navidrome user updated.",
            embed=direct_account_embed(remote, self.linked_member_id),
            view=DirectUserActionView(self.cog, interaction.user, remote, self.linked_member_id),
        )


class DirectDeleteConfirmView(discord.ui.View):
    def __init__(self, cog, author, remote, linked_member_id=None):
        super().__init__(timeout=120)
        self.cog, self.author, self.remote = cog, author, remote
        self.linked_member_id = linked_member_id

    async def interaction_check(self, interaction):
        return await owner_check(interaction, self.author)

    @discord.ui.button(label="Delete permanently", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer()
        try:
            _, client = await self.cog._guild_client(interaction.guild)
            await client.delete_user(str(self.remote.get("id") or ""))
        except NavidromeError as exc:
            await interaction.followup.send(f"Account deletion failed: {exc}", ephemeral=True)
            return
        if self.linked_member_id:
            group = self.cog.config.guild(interaction.guild)
            accounts = await group.accounts()
            accounts.pop(str(self.linked_member_id), None)
            await group.accounts.set(accounts)
        embed, view = await DirectUsersView.create(self.cog, interaction.user, interaction.guild)
        await interaction.edit_original_response(content="Navidrome user deleted.", embed=embed, view=view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(
            content="No changes made.",
            embed=direct_account_embed(self.remote, self.linked_member_id),
            view=DirectUserActionView(self.cog, interaction.user, self.remote, self.linked_member_id),
        )


class DirectUserActionView(discord.ui.View):
    def __init__(self, cog, author, remote, linked_member_id=None):
        super().__init__(timeout=600)
        self.cog, self.author, self.remote = cog, author, remote
        self.linked_member_id = linked_member_id
        if remote.get("isAdmin"):
            next(item for item in self.children if item.custom_id == "navidrome:direct:delete").disabled = True

    async def interaction_check(self, interaction):
        return await owner_check(interaction, self.author)

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary)
    async def edit(self, interaction, button):
        await interaction.response.send_modal(
            DirectAccountEditModal(self.cog, self.remote, self.linked_member_id)
        )

    @discord.ui.button(label="Reset password", style=discord.ButtonStyle.secondary)
    async def password(self, interaction, button):
        await interaction.response.defer()
        password = secrets.token_urlsafe(18)
        try:
            _, client = await self.cog._guild_client(interaction.guild)
            remote = await client.update_user(self.remote, password=password)
        except NavidromeError as exc:
            await interaction.followup.send(f"Password reset failed: {exc}", ephemeral=True)
            return
        await interaction.followup.send(
            f"New temporary password for {remote.get('userName')}: {password}\n"
            "Copy it now; the bot does not store it.",
            ephemeral=True,
        )
        await interaction.edit_original_response(
            content="Password reset complete.",
            embed=direct_account_embed(remote, self.linked_member_id),
            view=DirectUserActionView(self.cog, interaction.user, remote, self.linked_member_id),
        )

    @discord.ui.button(
        label="Delete", style=discord.ButtonStyle.danger, custom_id="navidrome:direct:delete"
    )
    async def delete(self, interaction, button):
        await interaction.response.edit_message(
            content=f"Permanently delete Navidrome user {self.remote.get('userName')}? This cannot be undone.",
            embed=None,
            view=DirectDeleteConfirmView(
                self.cog, interaction.user, self.remote, self.linked_member_id
            ),
        )

    @discord.ui.button(label="Back to users", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        await interaction.response.defer()
        embed, view = await DirectUsersView.create(self.cog, interaction.user, interaction.guild)
        await interaction.edit_original_response(content=None, embed=embed, view=view)


class DirectUserSelect(discord.ui.Select):
    def __init__(self, parent, users, linked, page):
        options = []
        for remote in users[page * 25:(page + 1) * 25]:
            username = str(remote.get("userName") or "Unknown")
            member_id = linked.get(username.casefold())
            details = "Linked to Discord" if member_id else "Not linked to Discord"
            if remote.get("isAdmin"):
                details += " - Administrator"
            options.append(discord.SelectOption(
                label=username[:100], value=str(remote.get("id") or username),
                description=details[:100],
            ))
        super().__init__(placeholder="Choose a Navidrome user", options=options, row=0)
        self.parent_view = parent

    async def callback(self, interaction):
        remote = next(
            user for user in self.parent_view.users
            if str(user.get("id") or user.get("userName")) == self.values[0]
        )
        member_id = self.parent_view.linked.get(str(remote.get("userName") or "").casefold())
        await interaction.response.edit_message(
            content=None,
            embed=direct_account_embed(remote, member_id),
            view=DirectUserActionView(self.parent_view.cog, interaction.user, remote, member_id),
        )


class DirectUsersView(discord.ui.View):
    def __init__(self, cog, author, users, linked, page=0):
        super().__init__(timeout=600)
        self.cog, self.author, self.users, self.linked, self.page = cog, author, users, linked, page
        self.pages = max(1, (len(users) + 24) // 25)
        if users:
            self.add_item(DirectUserSelect(self, users, linked, page))
        self.previous.disabled = page <= 0
        self.next.disabled = page >= self.pages - 1

    @classmethod
    async def create(cls, cog, author, guild, page=0):
        _, client = await cog._guild_client(guild)
        users = sorted(await client.users(), key=lambda user: str(user.get("userName") or "").casefold())
        page = min(max(0, page), max(0, (len(users) - 1) // 25))
        accounts = await cog.config.guild(guild).accounts()
        linked = {
            str(account.get("username") or "").casefold(): member_id
            for member_id, account in accounts.items() if account.get("username")
        }
        embed = discord.Embed(
            title="Navidrome users",
            description=(
                f"{len(users)} user(s) on this Navidrome server. Select one to manage it, "
                "or create an account without linking a Discord member."
            ),
            colour=discord.Colour.blurple(),
        )
        if users:
            embed.set_footer(text=f"Page {page + 1}/{max(1, (len(users) + 24) // 25)}")
        return embed, cls(cog, author, users, linked, page)

    async def interaction_check(self, interaction):
        return await owner_check(interaction, self.author)

    async def show_page(self, interaction, page):
        embed, view = await self.create(self.cog, interaction.user, interaction.guild, page)
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction, button):
        await self.show_page(interaction, self.page - 1)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction, button):
        await self.show_page(interaction, self.page + 1)

    @discord.ui.button(label="Create unlinked user", style=discord.ButtonStyle.success, row=2)
    async def create_user(self, interaction, button):
        await interaction.response.send_modal(DirectAccountCreateModal(self.cog))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction, button):
        await interaction.response.edit_message(
            content=None,
            embed=discord.Embed(
                title="Navidrome account manager",
                description=(
                    "Browse every Navidrome account, or choose a Discord member to create "
                    "and manage a linked account."
                ),
                colour=discord.Colour.blurple(),
            ),
            view=AccountManagerView(self.cog, interaction.user),
        )


class AccountManagerView(discord.ui.View):
    def __init__(self, cog, author):
        super().__init__(timeout=600)
        self.cog, self.author = cog, author
        self.add_item(UserSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_check(interaction, self.author)

    @discord.ui.button(label="Browse all users (owner)", style=discord.ButtonStyle.primary, row=1)
    async def browse(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "Only the bot owner can manage unlinked Navidrome users.", ephemeral=True
            )
            return
        await interaction.response.defer()
        try:
            embed, view = await DirectUsersView.create(
                self.cog, interaction.user, interaction.guild
            )
        except NavidromeError as exc:
            await interaction.followup.send(
                f"Could not load Navidrome users: {exc}", ephemeral=True
            )
            return
        await interaction.edit_original_response(content=None, embed=embed, view=view)

    @discord.ui.button(label="Back to setup", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=None,
            embed=await self.cog.setup_embed(interaction.guild),
            view=await NavidromeSetupView.create(self.cog, interaction.user, interaction.guild),
        )


class AccountConfirmView(discord.ui.View):
    def __init__(self, cog, author, member, account, remote, action: str):
        super().__init__(timeout=120)
        self.cog, self.author, self.member, self.account, self.remote, self.action = (
            cog, author, member, account, remote, action
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_check(interaction, self.author)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        group = self.cog.config.guild(interaction.guild)
        accounts = await group.accounts()
        if self.action == "delete":
            try:
                _, client = await self.cog._guild_client(interaction.guild)
                await client.delete_user(str(self.account.get("id") or ""))
            except NavidromeError as exc:
                await interaction.followup.send(f"Account deletion failed: {exc}", ephemeral=True)
                return
            message = "Deleted the Navidrome user and its Discord mapping."
        else:
            message = "Removed the Discord mapping. The Navidrome user was kept."
        accounts.pop(str(self.member.id), None)
        await group.accounts.set(accounts)
        await interaction.edit_original_response(
            content=message,
            embed=account_embed(self.member, None, None),
            view=UserActionView(self.cog, interaction.user, self.member, None, None),
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="No changes made.",
            embed=account_embed(self.member, self.account, self.remote),
            view=UserActionView(
                self.cog, interaction.user, self.member, self.account, self.remote
            ),
        )


class UserActionView(discord.ui.View):
    def __init__(self, cog, author, member, account, remote):
        super().__init__(timeout=600)
        self.cog, self.author, self.member, self.account, self.remote = (
            cog, author, member, account, remote
        )
        if not account:
            for item in self.children:
                if getattr(item, "custom_id", None) not in {"navidrome:user:create", "navidrome:user:back"}:
                    item.disabled = True
        else:
            create = next(
                item for item in self.children
                if getattr(item, "custom_id", None) == "navidrome:user:create"
            )
            create.disabled = True
            if not remote:
                for item in self.children:
                    if getattr(item, "custom_id", None) in {
                        "navidrome:user:edit", "navidrome:user:password", "navidrome:user:delete"
                    }:
                        item.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_check(interaction, self.author)

    @discord.ui.button(
        label="Create", style=discord.ButtonStyle.success, custom_id="navidrome:user:create"
    )
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            AccountCreateModal(self.cog, interaction.user, self.member)
        )

    @discord.ui.button(
        label="Edit", style=discord.ButtonStyle.primary, custom_id="navidrome:user:edit"
    )
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            AccountEditModal(
                self.cog, interaction.user, self.member, self.account, self.remote
            )
        )

    @discord.ui.button(
        label="Reset password", style=discord.ButtonStyle.secondary,
        custom_id="navidrome:user:password",
    )
    async def password(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.member.send(
                f"A Navidrome password reset was requested for **{interaction.guild.name}**. "
                "Your temporary password will follow in another message."
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                "I cannot DM that member, so the password was not changed.", ephemeral=True
            )
            return
        await interaction.response.defer()
        try:
            _, client = await self.cog._guild_client(interaction.guild)
            remote = await client.user_by_username(str(self.account.get("username") or ""))
            if not remote:
                await interaction.followup.send("The linked Navidrome user no longer exists.", ephemeral=True)
                return
            password = secrets.token_urlsafe(18)
            remote = await client.update_user(remote, password=password)
            await self.member.send(
                f"New Navidrome temporary password: `{password}`\n"
                "Sign in and change it as soon as possible."
            )
        except NavidromeError as exc:
            await interaction.followup.send(f"Password reset failed: {exc}", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "The password changed, but the final DM failed. Reset it again.", ephemeral=True
            )
            return
        await interaction.edit_original_response(
            content="Password reset and sent to the member by DM.",
            embed=account_embed(self.member, self.account, remote),
            view=UserActionView(
                self.cog, interaction.user, self.member, self.account, remote
            ),
        )

    @discord.ui.button(
        label="Unlink", style=discord.ButtonStyle.secondary, custom_id="navidrome:user:unlink"
    )
    async def unlink(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Remove only the Discord mapping and keep the Navidrome user?",
            embed=None,
            view=AccountConfirmView(
                self.cog, interaction.user, self.member, self.account, self.remote, "unlink"
            ),
        )

    @discord.ui.button(
        label="Delete", style=discord.ButtonStyle.danger, custom_id="navidrome:user:delete"
    )
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Permanently delete this Navidrome user and its Discord mapping?",
            embed=None,
            view=AccountConfirmView(
                self.cog, interaction.user, self.member, self.account, self.remote, "delete"
            ),
        )

    @discord.ui.button(
        label="Back", style=discord.ButtonStyle.secondary, row=1,
        custom_id="navidrome:user:back",
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=None,
            embed=discord.Embed(
                title="Navidrome account manager",
                description="Choose a Discord member to manage.",
                colour=discord.Colour.blurple(),
            ),
            view=AccountManagerView(self.cog, interaction.user),
        )


class NavidromeSetupView(discord.ui.View):
    def __init__(self, cog, author: discord.abc.User):
        super().__init__(timeout=600)
        self.cog, self.author = cog, author

    @classmethod
    async def create(cls, cog, author: discord.abc.User, guild: discord.Guild):
        view = cls(cog, author)
        profiles = await cog.config.connections()
        settings = await cog.config.guild(guild).all()
        if profiles:
            view.add_item(ConnectionSelect(view, profiles, settings.get("connection")))
        view.add_item(AnnouncementChannelSelect(view))
        toggle = next(item for item in view.children if getattr(item, "custom_id", None) == "navidrome:toggle")
        enabled = bool(settings.get("announcement_enabled"))
        toggle.label = "Disable announcements" if enabled else "Enable announcements"
        toggle.style = discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success
        connect = next(
            item for item in view.children
            if getattr(item, "custom_id", None) == "navidrome:connect"
        )
        connect.disabled = not await cog.bot.is_owner(author)
        guild_connect = next(
            item for item in view.children
            if getattr(item, "custom_id", None) == "navidrome:guild-connect"
        )
        guild_connect.disabled = not await cog.config.guild_connections_enabled()
        guild_lidarr = next(
            item for item in view.children
            if getattr(item, "custom_id", None) == "navidrome:guild-lidarr"
        )
        guild_lidarr.disabled = settings.get("connection_mode") != "guild_managed"
        manage = next(
            item for item in view.children
            if getattr(item, "custom_id", None) == "navidrome:manage-users"
        )
        manage.disabled = not (
            bool((settings.get("guild_connection") or {}).get("base_url"))
            if settings.get("connection_mode") == "guild_managed"
            else bool(settings.get("connection"))
        )
        return view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_check(interaction, self.author)

    @discord.ui.button(
        label="Connect server", style=discord.ButtonStyle.success, row=2,
        custom_id="navidrome:connect",
    )
    async def connect_server(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "Only the bot owner can save Navidrome server credentials.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ConnectionModal(self.cog))

    @discord.ui.button(
        label="Connect this server", style=discord.ButtonStyle.success, row=3,
        custom_id="navidrome:guild-connect",
    )
    async def connect_guild(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.config.guild_connections_enabled():
            await interaction.response.send_message(
                "The bot owner has disabled guild-managed connections.", ephemeral=True
            )
            return
        await interaction.response.send_modal(GuildConnectionModal(self.cog))

    @discord.ui.button(
        label="Connect Lidarr", style=discord.ButtonStyle.secondary, row=3,
        custom_id="navidrome:guild-lidarr",
    )
    async def connect_lidarr(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuildLidarrModal(self.cog))

    @discord.ui.button(label="Set interval", style=discord.ButtonStyle.secondary, row=2)
    async def interval(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = await self.cog.config.guild(interaction.guild).interval_minutes()
        await interaction.response.send_modal(IntervalModal(self.cog, interaction.user, current))

    @discord.ui.button(
        label="Test connection", style=discord.ButtonStyle.primary, row=2,
        custom_id="navidrome:test",
    )
    async def test_connection(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            name, client = await self.cog._guild_client(interaction.guild)
            await client.ping()
        except NavidromeError as exc:
            await interaction.followup.send(f"Connection test failed: {exc}", ephemeral=True)
            return
        await interaction.followup.send(f"Connection `{name}` is responding.", ephemeral=True)

    @discord.ui.button(label="Preview album", style=discord.ButtonStyle.secondary, row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        ok, message = await self.cog.preview_announcement(interaction.guild)
        await interaction.followup.send(message, ephemeral=not ok)

    @discord.ui.button(
        label="Enable announcements", style=discord.ButtonStyle.success, row=3,
        custom_id="navidrome:toggle",
    )
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        settings = await self.cog.config.guild(interaction.guild).all()
        if settings.get("announcement_enabled"):
            await self.cog.config.guild(interaction.guild).announcement_enabled.set(False)
            message = "Recently added album announcements are disabled."
        else:
            ok, message = await self.cog.enable_announcements(interaction.guild)
            if not ok:
                await interaction.followup.send(message, ephemeral=True)
                return
        await interaction.edit_original_response(
            content=message,
            embed=await self.cog.setup_embed(interaction.guild),
            view=await NavidromeSetupView.create(self.cog, interaction.user, interaction.guild),
        )

    @discord.ui.button(
        label="Manage users", style=discord.ButtonStyle.primary, row=2,
        custom_id="navidrome:manage-users",
    )
    async def manage_users(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=None,
            embed=discord.Embed(
                title="Navidrome account manager",
                description=(
                    "Browse every Navidrome account, or choose a Discord member to create "
                    "and manage a linked account."
                ),
                colour=discord.Colour.blurple(),
            ),
            view=AccountManagerView(self.cog, interaction.user),
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.secondary, row=3)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Navidrome setup closed.",
            embed=await self.cog.setup_embed(interaction.guild),
            view=None,
        )
