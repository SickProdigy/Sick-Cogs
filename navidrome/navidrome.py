import asyncio
import datetime
import io
import logging
import random
import re
import secrets
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils import can_user_send_messages_in
from redbot.core.utils.chat_formatting import pagify

from .client import NavidromeClient, NavidromeError, validate_base_url
from .lidarr import LidarrClient, LidarrError
from .setup import NavidromeSetupView, account_credentials_message


log = logging.getLogger("red.sick-cogs.Navidrome")
CONFIG_IDENTIFIER = 9172048261
TOKEN_PREFIX = "navidrome_"
USER_AGENT = "Sick-Cogs-Navidrome/1.2.0"
GuildMessageable = Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


async def navidrome_config_permission(ctx: commands.Context) -> bool:
    """Allow administrators or a guild's explicitly delegated Navidrome manager role."""
    if await ctx.bot.is_owner(ctx.author) or ctx.author.guild_permissions.manage_guild:
        return True
    role_id = await ctx.cog.config.guild(ctx.guild).manager_role_id()
    if role_id and any(role.id == role_id for role in ctx.author.roles):
        return True
    raise commands.CheckFailure(
        "Manage Server or the configured Navidrome manager role is required."
    )


def normalized_music_name(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode().casefold()
    return "".join(character for character in ascii_value if character.isalnum())


def exact_local_match(media_type: str, query: str, matches) -> Optional[Dict[str, Any]]:
    wanted = normalized_music_name(query)
    for item in matches:
        title = item.get("name") or item.get("album") or item.get("title") or ""
        candidates = [title]
        if media_type == "album":
            artist = item.get("artist") or item.get("artistName") or ""
            candidates.extend((f"{artist} {title}", f"{title} {artist}"))
        if any(normalized_music_name(candidate) == wanted for candidate in candidates):
            return item
    return None


def lidarr_requester_tag(username: str) -> str:
    normalized = unicodedata.normalize("NFKD", username).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")
    return f"discord-user-{(slug or 'user')[:40]}"


class LidarrConfirmView(discord.ui.View):
    def __init__(self, cog, author_id: int, media_type: str, candidate: Dict[str, Any]):
        super().__init__(timeout=120)
        self.cog = cog
        self.author_id = author_id
        self.media_type = media_type
        self.candidate = candidate

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the member who made this request can confirm it.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm Lidarr request", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        await interaction.response.edit_message(view=self)
        message = await self.cog.execute_lidarr_request(
            interaction.guild, interaction.user, self.media_type, self.candidate
        )
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Lidarr request cancelled.", view=self)


def lidarr_candidate_text(media_type: str, candidate: Dict[str, Any]) -> Tuple[str, str]:
    if media_type == "artist":
        label = str(candidate.get("artistName") or "Unknown artist")
        detail = str(candidate.get("disambiguation") or "Artist").replace("\n", " ")
    else:
        label = str(candidate.get("title") or "Unknown release")
        artist = str((candidate.get("artist") or {}).get("artistName") or "Unknown artist")
        date = str(candidate.get("releaseDate") or "")[:4]
        detail = f"{artist}{f' · {date}' if date else ''}"
    return label[:100], detail[:100]


def musicbrainz_url(media_type: str, candidate: Dict[str, Any]) -> Optional[str]:
    foreign_id = str(
        candidate.get("foreignArtistId") if media_type == "artist"
        else candidate.get("foreignAlbumId")
        or ""
    )
    if not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        foreign_id,
    ):
        return None
    entity = "artist" if media_type == "artist" else "release-group"
    return f"https://musicbrainz.org/{entity}/{foreign_id.lower()}"


def lidarr_item_url(
    base_url: str, media_type: str, candidate: Dict[str, Any]
) -> Optional[str]:
    mb_url = musicbrainz_url(media_type, candidate)
    if not mb_url or not base_url:
        return None
    foreign_id = mb_url.rsplit("/", 1)[-1]
    entity = "artist" if media_type == "artist" else "album"
    return f"{base_url.rstrip('/')}/{entity}/{foreign_id}"


def lidarr_confirmation_embed(
    member: discord.Member, account: Optional[Dict[str, Any]], media_type: str,
    candidate: Dict[str, Any],
) -> discord.Embed:
    title, detail = lidarr_candidate_text(media_type, candidate)
    embed = discord.Embed(
        title=f"Confirm Lidarr {'artist' if media_type == 'artist' else 'release'} request",
        description=f"**{title}**\n{detail}", colour=discord.Colour.orange(),
    )
    mb_url = musicbrainz_url(media_type, candidate)
    if mb_url:
        embed.add_field(
            name="MusicBrainz", value=f"[Open the selected result]({mb_url})", inline=False
        )
    embed.add_field(
        name="Attribution tags",
        value=f"`discord`, `{lidarr_requester_tag(member.name)}`", inline=False,
    )
    embed.add_field(
        name="Identity",
        value=(f"Linked Navidrome account: `{account.get('username')}`" if account
               else "Discord-only identity"), inline=False,
    )
    embed.set_footer(text="Nothing will be changed in Lidarr until you confirm.")
    return embed


class LidarrResultSelect(discord.ui.Select):
    def __init__(self, parent):
        options = []
        for index, candidate in enumerate(parent.candidates[:25]):
            label, detail = lidarr_candidate_text(parent.media_type, candidate)
            options.append(discord.SelectOption(
                label=label, value=str(index), description=detail,
            ))
        super().__init__(placeholder="Choose the correct Lidarr result", options=options)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        candidate = self.parent_view.candidates[int(self.values[0])]
        await interaction.response.edit_message(
            content=None,
            embed=lidarr_confirmation_embed(
                interaction.user, self.parent_view.account,
                self.parent_view.media_type, candidate,
            ),
            view=LidarrConfirmView(
                self.parent_view.cog, interaction.user.id,
                self.parent_view.media_type, candidate,
            ),
        )


class LidarrResultView(discord.ui.View):
    def __init__(self, cog, author_id: int, media_type: str, candidates, account):
        super().__init__(timeout=120)
        self.cog, self.author_id = cog, author_id
        self.media_type, self.candidates, self.account = media_type, candidates, account
        self.add_item(LidarrResultSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the member who started this search can choose a result.", ephemeral=True
            )
            return False
        return True


class LidarrRequestModal(discord.ui.Modal):
    def __init__(
        self, cog, author_id: int, media_type: str,
        source_message: Optional[discord.Message] = None,
    ):
        label = "Artist" if media_type == "artist" else "Release, album, single, or song"
        title = "Search Lidarr artists" if media_type == "artist" else "Search Lidarr releases"
        super().__init__(title=title)
        self.cog, self.author_id, self.media_type = cog, author_id, media_type
        self.source_message = source_message
        self.query = discord.ui.TextInput(label=label, min_length=2, max_length=200)
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if self.source_message is not None:
            try:
                await self.source_message.delete()
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
        content, embed, view = await self.cog.prepare_lidarr_request(
            interaction.guild, interaction.user, self.media_type, str(self.query.value)
        )
        if embed is None and view is None:
            await interaction.followup.send(content, ephemeral=True)
        else:
            kwargs = {"ephemeral": True, "view": view}
            if content is not None:
                kwargs["content"] = content
            if embed is not None:
                kwargs["embed"] = embed
            await interaction.followup.send(**kwargs)


class LidarrRequestTypeView(discord.ui.View):
    def __init__(self, cog, author_id: int, only_type: Optional[str] = None):
        super().__init__(timeout=120)
        self.cog, self.author_id = cog, author_id
        if only_type:
            keep = "Artist" if only_type == "artist" else "Release"
            for item in list(self.children):
                if getattr(item, "label", None) != keep:
                    self.remove_item(item)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the member who opened this request menu can use it.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Artist", style=discord.ButtonStyle.primary)
    async def artist(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            LidarrRequestModal(
                self.cog, interaction.user.id, "artist", interaction.message
            )
        )

    @discord.ui.button(label="Release", style=discord.ButtonStyle.primary)
    async def release(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            LidarrRequestModal(
                self.cog, interaction.user.id, "album", interaction.message
            )
        )


def safe_profile_name(value: str) -> str:
    name = value.strip().lower()
    if not name or len(name) > 32 or not all(char.isalnum() or char in "_-" for char in name):
        raise ValueError("Connection names may contain only letters, numbers, underscores, and hyphens.")
    return name


class Navidrome(commands.Cog):
    """Connect each Discord server to its own approved Navidrome library."""

    __author__ = ["SickProdigy"]
    __version__ = "1.2.0"

    default_global = {"connections": {}, "guild_connections_enabled": False}
    default_guild = {
        "connection": None,
        "connection_mode": "owner_managed",
        "guild_connection": None,
        "announcement_enabled": False,
        "announcement_channel_id": None,
        "interval_minutes": 60,
        "next_check_at": None,
        "announced_album_ids": [],
        "last_success_at": None,
        "accounts": {},
        "manager_role_id": None,
        "lidarr_identity_policy": "linked_required",
        "lidarr_requester_role_id": None,
        "lidarr_request_channel_id": None,
        "lidarr_cooldown_seconds": 300,
        "lidarr_daily_limit": 5,
        "lidarr_audit": [],
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_global(**self.default_global)
        self.config.register_guild(**self.default_guild)
        self.session: Optional[aiohttp.ClientSession] = None
        self._poll_semaphore = asyncio.Semaphore(4)
        self._connection_failures: Dict[str, int] = {}
        self._connection_test_times: Dict[int, float] = {}
        self._lidarr_cooldowns: Dict[Tuple[int, int], float] = {}
        self.poll_loop.start()

    async def red_delete_data_for_user(self, **kwargs):
        """Remove Discord-to-Navidrome mappings without deleting remote accounts."""
        user_id = str(kwargs.get("user_id") or "")
        if not user_id:
            return
        for guild_id, settings in (await self.config.all_guilds()).items():
            accounts = dict(settings.get("accounts", {}))
            if accounts.pop(user_id, None) is not None:
                await self.config.guild_from_id(guild_id).accounts.set(accounts)
            audit = list(settings.get("lidarr_audit", []))
            changed = False
            for entry in audit:
                if str(entry.get("discord_user_id")) == user_id:
                    entry["discord_user_id"] = None
                    entry["discord_username"] = "deleted-user"
                    entry["navidrome_user_id"] = None
                    entry["navidrome_username"] = None
                    changed = True
            if changed:
                await self.config.guild_from_id(guild_id).lidarr_audit.set(audit)

    def cog_unload(self):
        self.poll_loop.cancel()
        if self.session and not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20), headers={"User-Agent": USER_AGENT}
            )
        return self.session

    def claim_connection_test(self, guild_id: int) -> bool:
        """Allow one guild-owned credential test per minute per guild."""
        now = time.monotonic()
        previous = self._connection_test_times.get(int(guild_id), 0.0)
        if now - previous < 60:
            return False
        self._connection_test_times[int(guild_id)] = now
        return True

    async def _profile(self, name: str) -> Optional[Dict[str, Any]]:
        return (await self.config.connections()).get(name)

    async def _client(self, name: str) -> NavidromeClient:
        profile = await self._profile(name)
        if not profile:
            raise NavidromeError("This server's Navidrome connection is no longer approved.")
        tokens = await self.bot.get_shared_api_tokens(f"{TOKEN_PREFIX}{name}")
        username = str(tokens.get("username") or "").strip()
        password = str(tokens.get("password") or "")
        if not username or not password:
            raise NavidromeError("The approved connection is missing its credentials.")
        return NavidromeClient(await self.get_session(), profile["base_url"], username, password)

    @staticmethod
    def guild_token_namespace(guild_id: int) -> str:
        return f"{TOKEN_PREFIX}guild_{int(guild_id)}"

    async def _owner_lidarr_tokens(self, name: str) -> Tuple[str, Dict[str, str]]:
        profiles = await self.config.connections()
        namespaces = ["lidarr", f"{TOKEN_PREFIX}{name}"] if len(profiles) == 1 else [f"{TOKEN_PREFIX}{name}"]
        last_tokens: Dict[str, str] = {}
        for namespace in namespaces:
            tokens = await self.bot.get_shared_api_tokens(namespace)
            last_tokens = tokens
            if tokens.get("lidarr_url") and tokens.get("lidarr_api_key"):
                return namespace, tokens
        return namespaces[0], last_tokens

    async def _lidarr_client(
        self, guild: discord.Guild
    ) -> Tuple[str, LidarrClient, Dict[str, Any]]:
        settings = await self.config.guild(guild).all()
        if settings.get("connection_mode") == "guild_managed":
            name = "Guild managed"
            profile = settings.get("guild_connection") or {}
            namespace = self.guild_token_namespace(guild.id)
            public_only = True
            allow_http = False
        else:
            selected = settings.get("connection")
            profile = await self._profile(selected) if selected else None
            if not profile:
                raise LidarrError("This server has no approved Navidrome connection.")
            name = str(selected)
            namespace, tokens = await self._owner_lidarr_tokens(str(selected))
            public_only = False
            allow_http = bool(profile.get("lidarr_allow_http"))
        lidarr = dict((profile or {}).get("lidarr") or {})
        if settings.get("connection_mode") == "guild_managed":
            tokens = await self.bot.get_shared_api_tokens(namespace)
        base_url = str(tokens.get("lidarr_url") or "").strip()
        api_key = str(tokens.get("lidarr_api_key") or "")
        if not lidarr.get("enabled") or not base_url or not api_key:
            raise LidarrError("Lidarr requests are not configured for this connection.")
        client = LidarrClient(
            await self.get_session(), base_url, api_key,
            public_only=public_only, allow_http=allow_http,
        )
        return name, client, lidarr

    async def _request_identity(
        self, guild: discord.Guild, member: discord.Member
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        settings = await self.config.guild(guild).all()
        role_id = settings.get("lidarr_requester_role_id")
        if role_id and not member.guild_permissions.manage_guild and not any(
            role.id == role_id for role in member.roles
        ):
            return None, "You do not have the configured Lidarr requester role."
        account = dict((settings.get("accounts") or {}).get(str(member.id)) or {})
        policy = settings.get("lidarr_identity_policy", "linked_required")
        if policy == "linked_required" and not account:
            return None, "A linked Navidrome account is required to make requests here."
        if account:
            try:
                _, navidrome = await self._guild_client(guild)
                remote = next(
                    (item for item in await navidrome.users()
                     if str(item.get("id")) == str(account.get("id"))), None
                )
            except NavidromeError as exc:
                return None, f"Could not revalidate your linked Navidrome account: {exc}"
            if remote is None:
                return None, "Your linked Navidrome account is no longer valid; ask an administrator to relink it."
        return account or None, None

    async def _record_lidarr_audit(
        self, guild: discord.Guild, member: discord.Member, media_type: str,
        candidate: Dict[str, Any], outcome: str, account: Optional[Dict[str, Any]],
    ) -> None:
        group = self.config.guild(guild)
        audit = await group.lidarr_audit()
        foreign_id = candidate.get("foreignArtistId") or candidate.get("foreignAlbumId")
        title = candidate.get("artistName") or candidate.get("title") or "Unknown"
        audit.append({
            "timestamp": utc_now().isoformat(), "discord_user_id": member.id,
            "discord_username": str(member), "guild_id": guild.id,
            "navidrome_user_id": account.get("id") if account else None,
            "navidrome_username": account.get("username") if account else None,
            "media_type": media_type, "title": str(title)[:200],
            "lidarr_id": str(foreign_id or "")[:100], "outcome": outcome,
        })
        await group.lidarr_audit.set(audit[-500:])

    async def _notify_lidarr_request(
        self, guild: discord.Guild, member: discord.Member, media_type: str,
        candidate: Dict[str, Any], outcome: str, lidarr_base_url: str,
    ) -> None:
        channel_id = await self.config.guild(guild).lidarr_request_channel_id()
        if not channel_id:
            return
        channel = await self._channel(guild, int(channel_id))
        if not channel:
            return
        title, detail = lidarr_candidate_text(media_type, candidate)
        embed = discord.Embed(
            title="New Lidarr request",
            description=f"**{title}**\n{detail}",
            colour=discord.Colour.green(),
        )
        embed.add_field(name="Requested by", value=member.mention, inline=True)
        embed.add_field(
            name="Type", value="Artist" if media_type == "artist" else "Release", inline=True
        )
        embed.add_field(name="Result", value=outcome.replace("-", " ").title(), inline=True)
        links = []
        mb_url = musicbrainz_url(media_type, candidate)
        lidarr_url = lidarr_item_url(lidarr_base_url, media_type, candidate)
        if mb_url:
            links.append(f"[MusicBrainz]({mb_url})")
        if lidarr_url:
            links.append(f"[Open in Lidarr]({lidarr_url})")
        if links:
            embed.add_field(name="Links", value=" · ".join(links), inline=False)
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.warning("Could not send Lidarr request notification for guild %s", guild.id)

    async def execute_lidarr_request(
        self, guild: discord.Guild, member: discord.Member, media_type: str,
        candidate: Dict[str, Any],
    ) -> str:
        account, error = await self._request_identity(guild, member)
        if error:
            return error
        settings = await self.config.guild(guild).all()
        now = time.monotonic()
        cooldown = int(settings.get("lidarr_cooldown_seconds", 300))
        previous = self._lidarr_cooldowns.get((guild.id, member.id), 0.0)
        if now - previous < cooldown:
            return f"Please wait {int(cooldown - (now - previous)) + 1} seconds before another request."
        today = utc_now().date().isoformat()
        used = sum(
            1 for item in settings.get("lidarr_audit", [])
            if item.get("discord_user_id") == member.id
            and str(item.get("timestamp", "")).startswith(today)
            and item.get("outcome") in {"added", "already-managed", "tagged-existing"}
        )
        if used >= int(settings.get("lidarr_daily_limit", 5)):
            return "You have reached this server's daily Lidarr request limit."
        try:
            _, client, lidarr_settings = await self._lidarr_client(guild)
            source_tag = await client.ensure_tag("discord")
            requester_tag = await client.ensure_tag(lidarr_requester_tag(member.name))
            tag_ids = [source_tag, requester_tag]
            if media_type == "artist":
                foreign_id = str(candidate.get("foreignArtistId") or "")
                existing = next(iter(await client.artists(mb_id=foreign_id)), None)
                if existing:
                    before = set(existing.get("tags") or [])
                    await client.update_artist_tags(existing, tag_ids)
                    outcome = "already-managed" if set(tag_ids).issubset(before) else "tagged-existing"
                else:
                    await client.add_artist(candidate, lidarr_settings, tag_ids)
                    outcome = "added"
            else:
                foreign_id = str(candidate.get("foreignAlbumId") or "")
                existing = (await client.albums(foreign_album_id=foreign_id))
                if existing:
                    artist_foreign_id = str((candidate.get("artist") or {}).get("foreignArtistId") or "")
                    managed_artist = next(
                        iter(await client.artists(mb_id=artist_foreign_id)), None
                    )
                    if managed_artist:
                        before = set(managed_artist.get("tags") or [])
                        await client.update_artist_tags(managed_artist, tag_ids)
                        outcome = (
                            "already-managed" if set(tag_ids).issubset(before)
                            else "tagged-existing"
                        )
                    else:
                        outcome = "already-managed"
                else:
                    await client.add_album(candidate, lidarr_settings, tag_ids)
                    outcome = "added"
        except LidarrError as exc:
            await self._record_lidarr_audit(guild, member, media_type, candidate, "failed", account)
            return f"Lidarr request failed: {exc}"
        self._lidarr_cooldowns[(guild.id, member.id)] = now
        await self._record_lidarr_audit(guild, member, media_type, candidate, outcome, account)
        await self._notify_lidarr_request(
            guild, member, media_type, candidate, outcome, getattr(client, "base_url", "")
        )
        labels = "discord, " + lidarr_requester_tag(member.name)
        return f"Lidarr request {outcome.replace('-', ' ')}. Attribution tags: `{labels}`."

    async def _guild_client(self, guild: discord.Guild) -> Tuple[str, NavidromeClient]:
        settings = await self.config.guild(guild).all()
        if settings.get("connection_mode") == "guild_managed":
            profile = settings.get("guild_connection") or {}
            base_url = str(profile.get("base_url") or "")
            if not base_url:
                raise NavidromeError("This server's guild-managed Navidrome connection is missing.")
            tokens = await self.bot.get_shared_api_tokens(self.guild_token_namespace(guild.id))
            username = str(tokens.get("username") or "").strip()
            password = str(tokens.get("password") or "")
            if not username or not password:
                raise NavidromeError("This server's guild-managed connection is missing credentials.")
            client = NavidromeClient(
                await self.get_session(), base_url, username, password, public_only=True
            )
            return "Guild managed", client
        name = settings.get("connection")
        if not name:
            raise NavidromeError("This server has not selected a Navidrome connection.")
        return name, await self._client(name)

    async def _reset_connection_state(self, guild: discord.Guild, *, clear_accounts: bool):
        group = self.config.guild(guild)
        await group.announcement_enabled.set(False)
        await group.announced_album_ids.set([])
        await group.last_success_at.set(None)
        await group.next_check_at.set(None)
        if clear_accounts:
            await group.accounts.set({})

    async def _channel(self, guild: discord.Guild, channel_id: int) -> Optional[GuildMessageable]:
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return None
        if getattr(channel, "guild", None) != guild or not can_user_send_messages_in(guild.me, channel):
            return None
        return channel

    @staticmethod
    def album_embed(album: Dict[str, Any]) -> discord.Embed:
        title = str(album.get("name") or album.get("album") or "Untitled album")
        artist = str(album.get("artist") or "Unknown artist")
        embed = discord.Embed(title=title, description=artist, colour=discord.Colour.blurple())
        details = []
        if album.get("year"):
            details.append(str(album["year"]))
        if album.get("genre"):
            details.append(str(album["genre"]))
        if details:
            embed.add_field(name="Details", value=" • ".join(details), inline=False)
        if album.get("songCount") is not None:
            embed.add_field(name="Tracks", value=str(album["songCount"]), inline=True)
        duration = int(album.get("duration") or 0)
        if duration:
            hours, remainder = divmod(duration, 3600)
            minutes = remainder // 60
            embed.add_field(name="Duration", value=f"{hours}h {minutes}m" if hours else f"{minutes}m", inline=True)
        embed.set_footer(text="Recently added to Navidrome")
        return embed

    async def send_album(
        self,
        channel: GuildMessageable,
        album: Dict[str, Any],
        client: NavidromeClient,
        *,
        content: Optional[str] = None,
    ):
        embed = self.album_embed(album)
        artwork = await client.cover_art(album.get("coverArt"))
        file = None
        if artwork:
            file = discord.File(io.BytesIO(artwork), filename="cover.jpg")
            embed.set_thumbnail(url="attachment://cover.jpg")
        await channel.send(
            content=content,
            embed=embed,
            file=file,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @staticmethod
    def _due(settings: Dict[str, Any]) -> bool:
        value = settings.get("next_check_at")
        if not value:
            return True
        try:
            parsed = datetime.datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed <= utc_now()
        except (TypeError, ValueError):
            return True

    async def _set_next_check(self, guild: discord.Guild, minutes: int):
        value = (utc_now() + datetime.timedelta(minutes=max(15, minutes))).isoformat()
        await self.config.guild(guild).next_check_at.set(value)

    async def setup_embed(self, guild: discord.Guild) -> discord.Embed:
        settings = await self.config.guild(guild).all()
        profiles = await self.config.connections()
        guild_connections_enabled = await self.config.guild_connections_enabled()
        channel = (
            guild.get_channel_or_thread(settings["announcement_channel_id"])
            if settings.get("announcement_channel_id")
            else None
        )
        mode = settings.get("connection_mode", "owner_managed")
        selected = settings.get("connection")
        guild_profile = settings.get("guild_connection") or {}
        active_name = "Guild managed" if mode == "guild_managed" else selected
        ready = bool(guild_profile.get("base_url")) if mode == "guild_managed" else selected in profiles
        active_profile = guild_profile if mode == "guild_managed" else (profiles.get(selected) or {})
        lidarr_ready = bool((active_profile.get("lidarr") or {}).get("enabled"))
        embed = discord.Embed(
            title="Navidrome setup",
            description=(
                "Connect a Navidrome server with an administrator account, then manage users. Album "
                "notifications are optional."
            ),
            colour=discord.Colour.blurple(),
        )
        embed.add_field(
            name="Connection",
            value=active_name or ("Not selected" if profiles else "No owner-approved connections"),
            inline=True,
        )
        embed.add_field(
            name="Connection mode",
            value="Guild managed" if mode == "guild_managed" else "Bot-owner managed",
            inline=True,
        )
        embed.add_field(
            name="Connection status",
            value="Ready to test" if ready else "Setup required",
            inline=True,
        )
        embed.add_field(
            name="Lidarr requests",
            value=(
                f"Enabled · `{settings.get('lidarr_identity_policy', 'linked_required')}`"
                if lidarr_ready else "Not configured"
            ),
            inline=True,
        )
        embed.add_field(
            name="Announcements",
            value="Enabled" if settings.get("announcement_enabled") else "Disabled",
            inline=True,
        )
        embed.add_field(name="Channel", value=channel.mention if channel else "Not set", inline=True)
        embed.add_field(
            name="Interval", value=f"{settings.get('interval_minutes', 60)} minutes", inline=True
        )
        embed.add_field(
            name="Last successful check",
            value=settings.get("last_success_at") or "Never",
            inline=False,
        )
        if not profiles and mode != "guild_managed":
            embed.add_field(
                name="Owner setup needed",
                value=(
                    "The bot owner can choose **Connect server** below to test and save "
                    "the Navidrome administrator connection."
                ),
                inline=False,
            )
        if guild_connections_enabled:
            embed.add_field(
                name="Guild-managed setup",
                value="Available to members with Manage Server. Public HTTPS endpoints only.",
                inline=False,
            )
        return embed

    async def enable_announcements(self, guild: discord.Guild) -> Tuple[bool, str]:
        settings = await self.config.guild(guild).all()
        if not settings.get("announcement_channel_id"):
            return False, "Select a connection and announcement channel first."
        channel = await self._channel(guild, int(settings["announcement_channel_id"]))
        if not channel:
            return False, "I cannot send messages in the configured channel."
        try:
            _, client = await self._guild_client(guild)
            albums = await client.newest_albums(25)
        except NavidromeError as exc:
            return False, f"Connection test failed: {exc}"
        baseline = [str(album["id"]) for album in albums if album.get("id")]
        group = self.config.guild(guild)
        await group.announced_album_ids.set(baseline[-500:])
        await group.announcement_enabled.set(True)
        await group.last_success_at.set(utc_now().isoformat())
        await self._set_next_check(guild, int(settings.get("interval_minutes", 60)))
        return True, (
            "Recently added album announcements are enabled. Current albums were recorded "
            "as the baseline and will not flood the channel."
        )

    async def preview_announcement(self, guild: discord.Guild) -> Tuple[bool, str]:
        settings = await self.config.guild(guild).all()
        channel_id = settings.get("announcement_channel_id")
        if not channel_id:
            return False, "Select a connection and announcement channel first."
        channel = await self._channel(guild, int(channel_id))
        if not channel:
            return False, "I cannot send messages in the configured channel."
        try:
            _, client = await self._guild_client(guild)
            albums = await client.newest_albums(1)
        except NavidromeError as exc:
            return False, str(exc)
        if not albums:
            return False, "Navidrome returned no albums to preview."
        await self.send_album(channel, albums[0], client, content="Navidrome announcement preview")
        return True, f"Sent a preview to {channel.mention}. Announcement history was not changed."

    async def _check_guild(self, guild: discord.Guild, albums: List[Dict[str, Any]], client: NavidromeClient):
        settings = await self.config.guild(guild).all()
        channel_id = settings.get("announcement_channel_id")
        if not settings.get("announcement_enabled") or not channel_id or not self._due(settings):
            return
        channel = await self._channel(guild, int(channel_id))
        if not channel:
            log.warning("Navidrome announcement channel unavailable for guild %s", guild.id)
            await self._set_next_check(guild, int(settings.get("interval_minutes", 60)))
            return
        seen = list(settings.get("announced_album_ids", []))
        seen_set = set(seen)
        unseen = [
            album
            for album in reversed(albums)
            if album.get("id") and str(album["id"]) not in seen_set
        ]
        original_seen_count = len(seen)
        try:
            for album in unseen[:5]:
                await self.send_album(channel, album, client)
                seen.append(str(album["id"]))
        finally:
            # Persist every successfully delivered ID even if a later Discord send fails.
            if len(seen) != original_seen_count:
                await self.config.guild(guild).announced_album_ids.set(seen[-500:])
        await self.config.guild(guild).last_success_at.set(utc_now().isoformat())
        await self._set_next_check(guild, int(settings.get("interval_minutes", 60)))

    @tasks.loop(minutes=5)
    async def poll_loop(self):
        await self.bot.wait_until_red_ready()
        due: Dict[str, List[discord.Guild]] = {}
        for guild in list(self.bot.guilds):
            if await self.bot.cog_disabled_in_guild(self, guild):
                continue
            settings = await self.config.guild(guild).all()
            configured = (
                bool((settings.get("guild_connection") or {}).get("base_url"))
                if settings.get("connection_mode") == "guild_managed"
                else bool(settings.get("connection"))
            )
            if settings.get("announcement_enabled") and configured and self._due(settings):
                key = (
                    f"guild:{guild.id}"
                    if settings.get("connection_mode") == "guild_managed"
                    else f"owner:{settings['connection']}"
                )
                due.setdefault(key, []).append(guild)
        if due:
            await asyncio.gather(
                *(self._poll_connection(name, guilds) for name, guilds in due.items())
            )

    async def _poll_connection(self, key: str, guilds: List[discord.Guild]):
        async with self._poll_semaphore:
            try:
                if key.startswith("guild:"):
                    _, client = await self._guild_client(guilds[0])
                else:
                    name = key.split(":", 1)[1] if key.startswith("owner:") else key
                    client = await self._client(name)
                albums = await client.newest_albums(25)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures = self._connection_failures.get(key, 0) + 1
                self._connection_failures[key] = failures
                delay = min(240, 15 * (2 ** min(failures - 1, 4))) + random.randint(0, 5)
                for guild in guilds:
                    if failures >= 3:
                        await self.config.guild(guild).announcement_enabled.set(False)
                        await self.config.guild(guild).next_check_at.set(None)
                    else:
                        await self._set_next_check(guild, delay)
                if failures >= 3:
                    log.error(
                        "Navidrome announcements disabled after repeated connection failures for %s (%s)",
                        key, type(exc).__name__,
                    )
                else:
                    log.warning(
                        "Navidrome poll failed for connection %s (%s); retry in about %s minutes",
                        key, type(exc).__name__, delay,
                    )
                return
            self._connection_failures.pop(key, None)
            for guild in guilds:
                try:
                    await self._check_guild(guild, albums, client)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Navidrome delivery failed for guild %s", guild.id)

    @poll_loop.before_loop
    async def before_poll_loop(self):
        await self.bot.wait_until_red_ready()

    @commands.group(name="navidrome", aliases=("navi",), invoke_without_command=True)
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def navidrome(self, ctx: commands.Context):
        """Show this server's Navidrome library information."""
        await self._send_info(ctx)

    async def _send_info(self, ctx: commands.Context):
        try:
            name, client = await self._guild_client(ctx.guild)
            summary = await client.library_summary()
        except NavidromeError as exc:
            return await ctx.send(str(exc))
        embed = discord.Embed(title=f"{summary['type']} library", colour=discord.Colour.blurple())
        embed.add_field(name="Connection", value=name, inline=True)
        embed.add_field(name="Server version", value=summary["server_version"], inline=True)
        embed.add_field(name="API version", value=summary["api_version"], inline=True)
        embed.add_field(name="Artists", value=f"{summary['artists']:,}", inline=True)
        embed.add_field(name="Libraries", value=f"{summary['music_folders']:,}", inline=True)
        embed.add_field(name="OpenSubsonic", value="Yes" if summary["open_subsonic"] else "No", inline=True)
        await ctx.send(embed=embed)

    @navidrome.command(name="info", aliases=("stats",))
    @commands.bot_has_permissions(embed_links=True)
    async def navidrome_info(self, ctx: commands.Context):
        """Show non-sensitive Navidrome server and library information."""
        await self._send_info(ctx)

    @navidrome.command(name="account")
    async def navidrome_account(self, ctx: commands.Context):
        """Show your Discord-linked Navidrome account."""
        account = (await self.config.guild(ctx.guild).accounts()).get(str(ctx.author.id))
        if not account:
            return await ctx.send("You do not have a linked Navidrome account in this server.")
        await ctx.send(
            f"Your linked Navidrome username is `{account.get('username', 'unknown')}`. "
            "Ask a server administrator if you need a password reset."
        )

    @navidrome.command(name="recent")
    @commands.bot_has_permissions(embed_links=True)
    async def navidrome_recent(self, ctx: commands.Context, count: int = 5):
        """Show recently added albums (1-10)."""
        count = max(1, min(count, 10))
        try:
            _, client = await self._guild_client(ctx.guild)
            albums = await client.newest_albums(count)
        except NavidromeError as exc:
            return await ctx.send(str(exc))
        if not albums:
            return await ctx.send("No albums were returned by this Navidrome server.")
        for album in albums:
            await self.send_album(ctx.channel, album, client)

    async def prepare_lidarr_request(
        self, guild: discord.Guild, member: discord.Member, media_type: str, query: str
    ):
        media_type = media_type.casefold()
        media_type = "album" if media_type in {"release", "album", "song", "single"} else media_type
        if media_type not in {"artist", "album"}:
            return "Choose `artist` or `release`.", None, None
        query = query.strip()
        if len(query) < 2 or len(query) > 200:
            return "Enter a search between 2 and 200 characters.", None, None
        account, error = await self._request_identity(guild, member)
        if error:
            return error, None, None
        try:
            _, navidrome = await self._guild_client(guild)
            local = await navidrome.search(
                query, artist_count=5 if media_type == "artist" else 0,
                album_count=5 if media_type == "album" else 0,
            )
            matches = local["artists" if media_type == "artist" else "albums"]
            local_match = exact_local_match(media_type, query, matches)
            if local_match:
                title = local_match.get("name") or local_match.get("album") or query
                return (
                    f"`{title}` already appears in this Navidrome library; "
                    "no Lidarr request was made.", None, None
                )
            _, lidarr, _ = await self._lidarr_client(guild)
            candidates = await lidarr.lookup(media_type, query)
        except (NavidromeError, LidarrError) as exc:
            return str(exc), None, None
        if not candidates:
            return "Lidarr found no matching result. Try including the artist name.", None, None
        if len(candidates) == 1:
            candidate = candidates[0]
            return None, lidarr_confirmation_embed(member, account, media_type, candidate), LidarrConfirmView(
                self, member.id, media_type, candidate
            )
        safe_query = discord.utils.escape_markdown(discord.utils.escape_mentions(query))
        return (
            f'Lidarr found {len(candidates)} results for "{safe_query}". '
            "Choose the correct one.", None,
            LidarrResultView(self, member.id, media_type, candidates, account),
        )

    @navidrome.command(name="request", aliases=("req",))
    @commands.bot_has_permissions(embed_links=True)
    async def navidrome_request(
        self, ctx: commands.Context, media_type: Optional[str] = None, *, query: str = ""
    ):
        """Search Navidrome, then request an artist or release through Lidarr.

        Use `artist` for an artist, or `release` for an album, EP, or single.
        `album`, `single`, and `song` are accepted as release aliases.

        Examples:
        `!navi req artist Willie Nelson`
        `!navi req release Willie Nelson Red Headed Stranger`
        `!navi req song Artist Name Song Name`

        Run `!navi req` to choose Artist or Release with buttons. Lidarr acquires
        releases rather than individual tracks, so song searches resolve to releases.
        """
        if media_type is None:
            return await ctx.send(
                "What would you like to request? Releases include albums, EPs, and singles.",
                view=LidarrRequestTypeView(self, ctx.author.id),
            )
        normalized = media_type.casefold()
        if not query and normalized in {"artist", "release", "album", "song", "single"}:
            chosen = "artist" if normalized == "artist" else "album"
            label = "Artist" if chosen == "artist" else "Release"
            return await ctx.send(
                f"Click **{label}** to enter your search.",
                view=LidarrRequestTypeView(self, ctx.author.id, chosen),
            )
        content, embed, view = await self.prepare_lidarr_request(
            ctx.guild, ctx.author, normalized, query
        )
        if embed is None and view is None:
            await ctx.send(content)
        else:
            await ctx.send(content=content, embed=embed, view=view)

    @commands.group(name="navidromeowner", invoke_without_command=True)
    @checks.is_owner()
    async def navidromeowner(self, ctx: commands.Context):
        """Manage bot-owner-approved Navidrome connections."""
        await ctx.send_help()

    @navidromeowner.command(name="guildconnections")
    async def navidromeowner_guildconnections(self, ctx: commands.Context, enabled: bool):
        """Allow or deny guild administrators from storing their own public-HTTPS connection."""
        await self.config.guild_connections_enabled.set(enabled)
        await ctx.send(
            f"Guild-managed Navidrome connections are now {'enabled' if enabled else 'disabled'}."
        )

    @navidromeowner.group(name="connection", invoke_without_command=True)
    async def owner_connection(self, ctx: commands.Context):
        """Manage approved connection profiles."""
        await ctx.send_help()

    @owner_connection.command(name="add")
    async def owner_connection_add(self, ctx: commands.Context, name: str, base_url: str, allow_http: bool = False):
        """Approve a URL after setting navidrome_NAME username/password API tokens."""
        try:
            name = safe_profile_name(name)
            base_url = validate_base_url(base_url, allow_http=allow_http)
        except ValueError as exc:
            return await ctx.send(str(exc))
        tokens = await self.bot.get_shared_api_tokens(f"{TOKEN_PREFIX}{name}")
        if not tokens.get("username") or not tokens.get("password"):
            return await ctx.send(
                f"Set credentials first with `{ctx.clean_prefix}set api {TOKEN_PREFIX}{name} username,YOUR_USER password,YOUR_PASSWORD` in a private channel."
            )
        profiles = await self.config.connections()
        previous = profiles.get(name)
        profiles[name] = {"base_url": base_url, "allow_http": bool(allow_http)}
        await self.config.connections.set(profiles)
        try:
            await (await self._client(name)).ping()
        except NavidromeError as exc:
            if previous is None:
                profiles.pop(name, None)
            else:
                profiles[name] = previous
            await self.config.connections.set(profiles)
            return await ctx.send(f"Connection was not saved: {exc}")
        await ctx.send(f"Approved Navidrome connection `{name}`.")

    @owner_connection.command(name="remove", aliases=("delete",))
    async def owner_connection_remove(self, ctx: commands.Context, name: str):
        """Remove an approved connection profile. Credentials are not deleted."""
        try:
            name = safe_profile_name(name)
        except ValueError as exc:
            return await ctx.send(str(exc))
        profiles = await self.config.connections()
        if name not in profiles:
            return await ctx.send("That connection is not registered.")
        profiles.pop(name)
        await self.config.connections.set(profiles)
        await ctx.send(f"Removed approved connection `{name}`. Guilds using it will stop polling.")

    @owner_connection.command(name="list", aliases=("show",))
    async def owner_connection_list(self, ctx: commands.Context):
        """List approved connection names and redacted endpoints."""
        profiles = await self.config.connections()
        if not profiles:
            return await ctx.send("No Navidrome connections are approved.")
        lines = [f"`{name}` — {profile['base_url']}" for name, profile in sorted(profiles.items())]
        await ctx.send("\n".join(lines))

    @owner_connection.command(name="test")
    async def owner_connection_test(self, ctx: commands.Context, name: str):
        """Test an approved connection without exposing credentials."""
        try:
            name = safe_profile_name(name)
            client = await self._client(name)
            result = await client.ping()
            users = await client.users()
        except (ValueError, NavidromeError) as exc:
            return await ctx.send(f"Connection test failed: {exc}")
        server = str(result.get("type") or "Navidrome")
        version = str(result.get("serverVersion") or "unknown version")
        await ctx.send(
            f"Connection `{name}` is responding as {server} {version}. "
            f"Native user management is available ({len(users)} users visible)."
        )

    @navidromeowner.group(name="lidarr", invoke_without_command=True)
    async def navidromeowner_lidarr(self, ctx: commands.Context):
        """Configure Lidarr for an owner-managed Navidrome connection."""
        await ctx.send_help()

    @navidromeowner_lidarr.command(name="configure", aliases=("set",))
    async def owner_lidarr_configure(
        self, ctx: commands.Context, name: Optional[str] = None,
        root_folder_path: Optional[str] = None, quality_profile_id: Optional[str] = None,
        metadata_profile_id: Optional[str] = None, allow_http: Optional[bool] = None,
    ):
        """Discover defaults, validate, and enable Lidarr for a Navidrome profile."""
        profiles = await self.config.connections()
        if name is None:
            if len(profiles) == 1:
                name = next(iter(profiles))
            elif not profiles:
                return await ctx.send("No Navidrome connections are registered.")
            else:
                choices = ", ".join(f"`{item}`" for item in sorted(profiles))
                return await ctx.send(f"Choose a Navidrome connection: {choices}")
        try:
            name = safe_profile_name(name)
        except ValueError as exc:
            return await ctx.send(str(exc))
        profile = profiles.get(name)
        if not profile:
            return await ctx.send("That Navidrome connection is not registered.")
        namespace, tokens = await self._owner_lidarr_tokens(name)
        base_url = str(tokens.get("lidarr_url") or "").strip()
        api_key = str(tokens.get("lidarr_api_key") or "")
        if not base_url or not api_key:
            return await ctx.send(
                f"Set `lidarr_url` and `lidarr_api_key` first with Red's shared API token "
                f"command for `{namespace}` in a private channel."
            )
        inferred_http = base_url.casefold().startswith("http://")
        allow_http = inferred_http if allow_http is None else allow_http
        try:
            client = LidarrClient(
                await self.get_session(), base_url, api_key, allow_http=allow_http
            )
            discovered = await client.discover_configuration(
                root_folder_path=root_folder_path, quality_profile_id=quality_profile_id,
                metadata_profile_id=metadata_profile_id,
            )
            status = discovered.pop("status")
            settings = {"enabled": True, **discovered, "monitor": "all"}
        except LidarrError as exc:
            return await ctx.send(f"Lidarr was not enabled: {exc}")
        profile = dict(profile)
        profile["lidarr"] = settings
        profile["lidarr_allow_http"] = bool(allow_http)
        profiles[name] = profile
        await self.config.connections.set(profiles)
        await ctx.send(
            f"Lidarr {status.get('version', 'unknown')} is enabled for `{name}` using "
            f"root `{settings['root_folder_path']}`, quality profile "
            f"`{settings['quality_profile_name']}` (ID {settings['quality_profile_id']}), and "
            f"metadata profile `{settings['metadata_profile_name']}` "
            f"(ID {settings['metadata_profile_id']}). The API key remains hidden."
        )

    @navidromeowner_lidarr.command(name="disable")
    async def owner_lidarr_disable(self, ctx: commands.Context, name: str):
        """Disable requests without displaying or deleting separately stored credentials."""
        try:
            name = safe_profile_name(name)
        except ValueError as exc:
            return await ctx.send(str(exc))
        profiles = await self.config.connections()
        if name not in profiles:
            return await ctx.send("That Navidrome connection is not registered.")
        profile = dict(profiles[name])
        profile.pop("lidarr", None)
        profile.pop("lidarr_allow_http", None)
        profiles[name] = profile
        await self.config.connections.set(profiles)
        await ctx.send(f"Lidarr requests are disabled for `{name}`.")

    @commands.group(name="navidromeset", invoke_without_command=True)
    @commands.guild_only()
    @commands.check(navidrome_config_permission)
    async def navidromeset(self, ctx: commands.Context):
        """Configure this server's Navidrome connection and announcements."""
        await self._send_settings(ctx)

    @navidromeset.command(name="setup", aliases=("interactive",))
    @commands.bot_has_permissions(embed_links=True)
    async def navidromeset_setup(self, ctx: commands.Context):
        """Open the guided Navidrome setup panel."""
        await ctx.send(
            embed=await self.setup_embed(ctx.guild),
            view=await NavidromeSetupView.create(self, ctx.author, ctx.guild),
        )

    @navidromeset.command(name="connection")
    @checks.admin_or_permissions(manage_guild=True)
    async def navidromeset_connection(self, ctx: commands.Context, name: str):
        """Select a bot-owner-approved connection for this server."""
        try:
            name = safe_profile_name(name)
        except ValueError as exc:
            return await ctx.send(str(exc))
        if not await self._profile(name):
            return await ctx.send("That Navidrome connection is not approved by the bot owner.")
        try:
            await (await self._client(name)).ping()
        except NavidromeError as exc:
            return await ctx.send(f"Connection test failed: {exc}")
        group = self.config.guild(ctx.guild)
        previous_mode = await group.connection_mode()
        previous_name = await group.connection()
        changed_server = previous_mode != "owner_managed" or previous_name != name
        if previous_mode == "guild_managed":
            await self.bot.remove_shared_api_tokens(
                self.guild_token_namespace(ctx.guild.id), "username", "password", "lidarr_url", "lidarr_api_key"
            )
            await group.guild_connection.set(None)
        await group.connection_mode.set("owner_managed")
        await group.connection.set(name)
        await self._reset_connection_state(ctx.guild, clear_accounts=changed_server)
        await ctx.send(f"This server now uses Navidrome connection `{name}`. Announcements remain disabled.")

    @navidromeset.command(name="disconnect")
    @checks.admin_or_permissions(manage_guild=True)
    async def navidromeset_disconnect(self, ctx: commands.Context, confirmation: str = ""):
        """Disconnect this server and delete any guild-owned credentials."""
        if confirmation.casefold() != "confirm":
            await ctx.send(
                f"Run `{ctx.clean_prefix}navidromeset disconnect confirm` to disconnect, clear linked-account mappings, and delete this guild's stored connection credentials."
            )
            return
        await self.bot.remove_shared_api_tokens(
            self.guild_token_namespace(ctx.guild.id), "username", "password", "lidarr_url", "lidarr_api_key"
        )
        await self.config.guild(ctx.guild).clear()
        await ctx.send(
            "Disconnected this server from Navidrome, deleted any guild-owned credentials, and cleared its local mappings and announcement history."
        )

    @navidromeset.group(name="lidarr", invoke_without_command=True)
    async def navidromeset_lidarr(self, ctx: commands.Context):
        """Configure this server's Lidarr request policy."""
        settings = await self.config.guild(ctx.guild).all()
        role_id = settings.get("lidarr_requester_role_id")
        channel_id = settings.get("lidarr_request_channel_id")
        request_channel = f"<#{channel_id}>" if channel_id else "Not set"
        await ctx.send(
            f"Identity policy: `{settings.get('lidarr_identity_policy', 'linked_required')}`\n"
            f"Requester role: {f'<@&{role_id}>' if role_id else 'Any member'}\n"
            f"Request channel: {request_channel}\n"
            f"Cooldown: {settings.get('lidarr_cooldown_seconds', 300)} seconds\n"
            f"Daily limit: {settings.get('lidarr_daily_limit', 5)} per member"
        )

    @navidromeset_lidarr.command(name="identity")
    @checks.admin_or_permissions(manage_guild=True)
    async def guild_lidarr_identity(self, ctx: commands.Context, policy: str):
        """Set linked_required, linked_optional, or discord_only request identity."""
        policy = policy.casefold()
        allowed = {"linked_required", "linked_optional", "discord_only"}
        if policy not in allowed:
            return await ctx.send("Choose `linked_required`, `linked_optional`, or `discord_only`.")
        await self.config.guild(ctx.guild).lidarr_identity_policy.set(policy)
        await ctx.send(
            f"New Lidarr requests use `{policy}`. Historical attribution is unchanged."
        )

    @navidromeset_lidarr.command(name="requesterrole")
    @checks.admin_or_permissions(manage_guild=True)
    async def guild_lidarr_requester_role(
        self, ctx: commands.Context, role: Optional[discord.Role] = None
    ):
        """Set or clear the role allowed to submit Lidarr requests."""
        await self.config.guild(ctx.guild).lidarr_requester_role_id.set(
            role.id if role else None
        )
        await ctx.send(
            f"Lidarr requester role set to {role.mention}." if role
            else "The Lidarr requester role restriction is cleared."
        )

    @navidromeset_lidarr.command(name="requestchannel")
    @checks.admin_or_permissions(manage_guild=True)
    async def guild_lidarr_request_channel(
        self, ctx: commands.Context, channel: Optional[GuildMessageable] = None
    ):
        """Set the channel notified after confirmed Lidarr requests."""
        channel = channel or ctx.channel
        await self.config.guild(ctx.guild).lidarr_request_channel_id.set(channel.id)
        await ctx.send(f"Confirmed Lidarr requests will be reported in {channel.mention}.")

    @navidromeset_lidarr.command(name="clearrequestchannel")
    @checks.admin_or_permissions(manage_guild=True)
    async def guild_lidarr_clear_request_channel(self, ctx: commands.Context):
        """Disable confirmed Lidarr request notifications."""
        await self.config.guild(ctx.guild).lidarr_request_channel_id.set(None)
        await ctx.send("Lidarr request channel notifications are disabled.")

    @navidromeset_lidarr.command(name="limits")
    @checks.admin_or_permissions(manage_guild=True)
    async def guild_lidarr_limits(
        self, ctx: commands.Context, cooldown_seconds: int, daily_limit: int
    ):
        """Set the per-member cooldown (30-86400 seconds) and daily limit (1-50)."""
        if not 30 <= cooldown_seconds <= 86400 or not 1 <= daily_limit <= 50:
            return await ctx.send("Cooldown must be 30-86400 seconds and daily limit 1-50.")
        group = self.config.guild(ctx.guild)
        await group.lidarr_cooldown_seconds.set(cooldown_seconds)
        await group.lidarr_daily_limit.set(daily_limit)
        await ctx.send(
            f"Lidarr limits set to {cooldown_seconds} seconds and {daily_limit} requests per day."
        )

    @navidromeset_lidarr.command(name="audit")
    async def guild_lidarr_audit(self, ctx: commands.Context, count: int = 10):
        """Show recent request outcomes without credentials."""
        entries = (await self.config.guild(ctx.guild).lidarr_audit())[-max(1, min(count, 25)):]
        if not entries:
            return await ctx.send("No Lidarr requests have been audited yet.")
        lines = [
            f"{item.get('timestamp', '')[:16]} — {item.get('discord_username', 'unknown')} — "
            f"{item.get('media_type')} `{item.get('title')}` — {item.get('outcome')}"
            for item in reversed(entries)
        ]
        await ctx.send("\n".join(lines))

    @navidromeset.command(name="managerrole")
    @checks.admin_or_permissions(manage_guild=True)
    async def navidromeset_managerrole(
        self, ctx: commands.Context, role: Optional[discord.Role] = None
    ):
        """Set or clear the role allowed to perform non-secret routine configuration."""
        await self.config.guild(ctx.guild).manager_role_id.set(role.id if role else None)
        if role:
            await ctx.send(
                f"{role.mention} may now use routine Navidrome configuration. "
                "Manage Server is still required to create, replace, or disconnect credentials."
            )
        else:
            await ctx.send("The optional Navidrome manager role is cleared.")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        await self.bot.remove_shared_api_tokens(
            self.guild_token_namespace(guild.id), "username", "password", "lidarr_url", "lidarr_api_key"
        )
        await self.config.guild(guild).clear()

    @navidromeset.command(name="channel", aliases=("announcechannel",))
    async def navidromeset_channel(self, ctx: commands.Context, channel: Optional[GuildMessageable] = None):
        """Set the recently-added album channel. Defaults to this channel."""
        channel = channel or ctx.channel
        if not await self._channel(ctx.guild, channel.id):
            return await ctx.send("I cannot send messages in that channel.")
        await self.config.guild(ctx.guild).announcement_channel_id.set(channel.id)
        await ctx.send(f"Recently added albums will be posted in {channel.mention} after announcements are enabled.")

    @navidromeset.command(name="interval")
    async def navidromeset_interval(self, ctx: commands.Context, minutes: int):
        """Set album polling interval in minutes (15-1440)."""
        if not 15 <= minutes <= 1440:
            return await ctx.send("The interval must be between 15 and 1440 minutes.")
        await self.config.guild(ctx.guild).interval_minutes.set(minutes)
        await self._set_next_check(ctx.guild, minutes)
        await ctx.send(f"Navidrome will be checked every {minutes} minutes.")

    @navidromeset.command(name="enable")
    async def navidromeset_enable(self, ctx: commands.Context):
        """Enable album announcements and establish a no-flood baseline."""
        ok, message = await self.enable_announcements(ctx.guild)
        await ctx.send(message)

    @navidromeset.command(name="disable")
    async def navidromeset_disable(self, ctx: commands.Context):
        """Disable scheduled album announcements."""
        await self.config.guild(ctx.guild).announcement_enabled.set(False)
        await ctx.send("Recently added album announcements are disabled.")

    @navidromeset.command(name="preview", aliases=("test",))
    @commands.bot_has_permissions(embed_links=True)
    async def navidromeset_preview(self, ctx: commands.Context):
        """Preview the newest album without changing announcement history."""
        ok, message = await self.preview_announcement(ctx.guild)
        await ctx.send(message)

    @navidromeset.group(name="user", invoke_without_command=True)
    async def navidromeset_user(self, ctx: commands.Context):
        """Manage Discord-linked Navidrome accounts for this server."""
        await ctx.send_help()

    @navidromeset_user.command(name="list")
    async def navidromeset_user_list(self, ctx: commands.Context):
        """List accounts provisioned through this Discord server."""
        accounts = await self.config.guild(ctx.guild).accounts()
        if not accounts:
            return await ctx.send("No Discord-linked Navidrome accounts are recorded.")
        lines = []
        for discord_id, account in sorted(
            accounts.items(), key=lambda item: str(item[1].get("username", "")).casefold()
        ):
            member = ctx.guild.get_member(int(discord_id))
            who = member.mention if member else f"Discord user `{discord_id}`"
            lines.append(f"{who} - `{account.get('username', 'unknown')}`")
        for page in pagify("\n".join(lines), delims=["\n"], page_length=1800):
            await ctx.send(page, allowed_mentions=discord.AllowedMentions.none())

    @navidromeset_user.command(name="info")
    async def navidromeset_user_info(self, ctx: commands.Context, member: discord.Member):
        """Show the current remote record for a linked member."""
        account = (await self.config.guild(ctx.guild).accounts()).get(str(member.id))
        if not account:
            return await ctx.send("That member has no linked Navidrome account in this server.")
        try:
            _, client = await self._guild_client(ctx.guild)
            user = await client.user_by_username(str(account.get("username") or ""))
        except NavidromeError as exc:
            return await ctx.send(f"Could not read the Navidrome user: {exc}")
        if not user:
            return await ctx.send(
                "The linked Navidrome user no longer exists. Use the unlink command to clear "
                "the stale Discord mapping."
            )
        embed = discord.Embed(title="Navidrome account", colour=discord.Colour.blurple())
        embed.add_field(name="Discord member", value=member.mention, inline=False)
        embed.add_field(name="Username", value=str(user.get("userName") or "Unknown"), inline=True)
        embed.add_field(name="Display name", value=str(user.get("name") or "Not set"), inline=True)
        embed.add_field(name="Email", value=str(user.get("email") or "Not set"), inline=True)
        embed.add_field(name="Administrator", value="Yes" if user.get("isAdmin") else "No", inline=True)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @navidromeset_user.command(name="create")
    async def navidromeset_user_create(
        self, ctx: commands.Context, member: discord.Member, username: str, email: str = ""
    ):
        """Create a non-admin Navidrome user and privately send its temporary password."""
        username = username.strip()
        if not 3 <= len(username) <= 64 or not re.fullmatch(r"[A-Za-z0-9._-]+", username):
            return await ctx.send(
                "Usernames must be 3-64 characters using letters, numbers, dots, underscores, or hyphens."
            )
        if email and ("@" not in email or len(email) > 254):
            return await ctx.send("Enter a valid email address or omit it.")
        group = self.config.guild(ctx.guild)
        accounts = await group.accounts()
        if str(member.id) in accounts:
            return await ctx.send("That member already has a linked Navidrome account.")
        try:
            _, client = await self._guild_client(ctx.guild)
            if await client.user_by_username(username):
                return await ctx.send("That Navidrome username already exists.")
            password = secrets.token_urlsafe(18)
            user = await client.create_user(
                username, password, name=member.display_name[:100], email=email
            )
            try:
                await member.send(
                    account_credentials_message(ctx.guild.name, username, password)
                )
            except discord.HTTPException:
                await client.delete_user(str(user.get("id") or ""))
                return await ctx.send(
                    "I could not DM that member, so the newly created Navidrome account was rolled back."
                )
        except NavidromeError as exc:
            return await ctx.send(f"Navidrome account creation failed: {exc}")
        accounts[str(member.id)] = {
            "id": str(user.get("id") or ""),
            "username": str(user.get("userName") or username),
            "created_at": utc_now().isoformat(),
        }
        await group.accounts.set(accounts)
        await ctx.send(
            f"Created Navidrome user `{username}` for {member.mention}; credentials were sent by DM.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @navidromeset_user.command(name="link")
    async def navidromeset_user_link(
        self, ctx: commands.Context, member: discord.Member, *, username: str
    ):
        """Link a Discord member to an existing Navidrome user."""
        group = self.config.guild(ctx.guild)
        accounts = await group.accounts()
        if str(member.id) in accounts:
            return await ctx.send("That member already has a linked Navidrome account.")
        try:
            _, client = await self._guild_client(ctx.guild)
            user = await client.user_by_username(username.strip())
        except NavidromeError as exc:
            return await ctx.send(f"Could not read the Navidrome user: {exc}")
        if not user:
            return await ctx.send("That Navidrome username does not exist.")
        remote_id = str(user.get("id") or "")
        remote_name = str(user.get("userName") or username.strip())
        if any(
            str(account.get("id") or "") == remote_id
            or str(account.get("username") or "").casefold() == remote_name.casefold()
            for account in accounts.values()
        ):
            return await ctx.send(
                "That Navidrome account is already linked to another Discord member."
            )
        accounts[str(member.id)] = {
            "id": remote_id,
            "username": remote_name,
            "created_at": utc_now().isoformat(),
        }
        await group.accounts.set(accounts)
        await ctx.send(
            f"Linked Navidrome user `{remote_name}` to {member.mention}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @navidromeset_user.command(name="name")
    async def navidromeset_user_name(
        self, ctx: commands.Context, member: discord.Member, *, display_name: str
    ):
        """Change a linked user's Navidrome display name."""
        await self._update_linked_user(ctx, member, name=display_name.strip()[:100])

    @navidromeset_user.command(name="email")
    async def navidromeset_user_email(
        self, ctx: commands.Context, member: discord.Member, email: str
    ):
        """Change a linked user's Navidrome email address."""
        if "@" not in email or len(email) > 254:
            return await ctx.send("Enter a valid email address.")
        await self._update_linked_user(ctx, member, email=email)

    @navidromeset_user.command(name="password", aliases=("resetpassword",))
    async def navidromeset_user_password(self, ctx: commands.Context, member: discord.Member):
        """Reset a linked user's password and send it privately."""
        account = (await self.config.guild(ctx.guild).accounts()).get(str(member.id))
        if not account:
            return await ctx.send("That member has no linked Navidrome account in this server.")
        try:
            await member.send(
                f"A Navidrome password reset was requested for **{ctx.guild.name}**. "
                "Your temporary password will follow in a second message."
            )
        except discord.HTTPException:
            return await ctx.send("I cannot DM that member, so their password was not changed.")
        try:
            _, client = await self._guild_client(ctx.guild)
            user = await client.user_by_username(str(account.get("username") or ""))
            if not user:
                return await ctx.send("The linked Navidrome user no longer exists.")
            password = secrets.token_urlsafe(18)
            await client.update_user(user, password=password)
            await member.send(
                f"New Navidrome temporary password: `{password}`\n"
                "Sign in and change it as soon as possible."
            )
        except (NavidromeError, discord.HTTPException) as exc:
            if isinstance(exc, NavidromeError):
                return await ctx.send(f"Navidrome password reset failed: {exc}")
            return await ctx.send(
                "The password changed, but the final DM failed. Run the reset command again."
            )
        await ctx.send(f"Reset {member.mention}'s Navidrome password and sent it by DM.")

    @navidromeset_user.command(name="unlink")
    async def navidromeset_user_unlink(self, ctx: commands.Context, member: discord.Member):
        """Remove only the Discord mapping; keep the Navidrome account."""
        group = self.config.guild(ctx.guild)
        accounts = await group.accounts()
        if accounts.pop(str(member.id), None) is None:
            return await ctx.send("That member has no linked Navidrome account in this server.")
        await group.accounts.set(accounts)
        await ctx.send("Removed the Discord mapping. The Navidrome account was not deleted.")

    @navidromeset_user.command(name="delete")
    async def navidromeset_user_delete(
        self, ctx: commands.Context, member: discord.Member, confirmation: str = ""
    ):
        """Permanently delete a linked Navidrome account; append `confirm`."""
        if confirmation.casefold() != "confirm":
            return await ctx.send(
                f"This permanently deletes the linked Navidrome user. Run "
                f"`{ctx.clean_prefix}navidromeset user delete {member} confirm` to continue."
            )
        group = self.config.guild(ctx.guild)
        accounts = await group.accounts()
        account = accounts.get(str(member.id))
        if not account:
            return await ctx.send("That member has no linked Navidrome account in this server.")
        try:
            _, client = await self._guild_client(ctx.guild)
            await client.delete_user(str(account.get("id") or ""))
        except NavidromeError as exc:
            return await ctx.send(f"Navidrome user deletion failed: {exc}")
        accounts.pop(str(member.id), None)
        await group.accounts.set(accounts)
        await ctx.send("Deleted the Navidrome user and removed its Discord mapping.")

    async def _update_linked_user(
        self, ctx: commands.Context, member: discord.Member, **changes: Any
    ):
        account = (await self.config.guild(ctx.guild).accounts()).get(str(member.id))
        if not account:
            return await ctx.send("That member has no linked Navidrome account in this server.")
        try:
            _, client = await self._guild_client(ctx.guild)
            user = await client.user_by_username(str(account.get("username") or ""))
            if not user:
                return await ctx.send("The linked Navidrome user no longer exists.")
            await client.update_user(user, **changes)
        except NavidromeError as exc:
            return await ctx.send(f"Navidrome user update failed: {exc}")
        await ctx.send(f"Updated {member.mention}'s Navidrome account.")

    @navidromeset.command(name="status", aliases=("settings",))
    @commands.bot_has_permissions(embed_links=True)
    async def navidromeset_status(self, ctx: commands.Context):
        """Show this server's Navidrome settings."""
        await self._send_settings(ctx)

    async def _send_settings(self, ctx: commands.Context):
        await ctx.send(embed=await self.setup_embed(ctx.guild))
