import aiohttp
import discord
from discord.ext import tasks
from datetime import datetime, timedelta
from pathlib import Path

from red_commons.logging import getLogger
from redbot.core import Config, commands, checks
from redbot.core.i18n import Translator

_ = Translator("Coc", __file__)
log = getLogger("red.Sick-Cogs.Coc")
COC_API_BASE = "https://api.clashofclans.com/v1"
COC_DEVELOPER_URL = "https://developer.clashofclans.com/"
CLAN_BANNER_PATH = Path(__file__).parent / "data" / "images" / "clan-banner.png"
WAR_BANNER_PATH = Path(__file__).parent / "data" / "images" / "war-banner.png"


class Coc(commands.Cog):
    """Clash of Clans War Updates"""

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete."""
        return

    def __init__(self, bot):
        self.bot = bot
        default_global = {"COC_API_KEY": None}
        default_guild = {
            "COC_CLAN_KEY": None,
            "COC_WAR_CHANNEL": None,
            "COC_WAR_NOTIFICATIONS": False,
            "WAR_START_TIME": None,
            "WAR_END_TIME": None,
            "WAR_PRE_HOURS_END": 1,
            "LAST_NOTIFICATION_TIMESTAMP": None,
            "LAST_NOTIFICATION_STATE": None,
            "LAST_API_PULL": None,
        }
        self.config = Config.get_conf(self, identifier=5218831554, force_registration=True)
        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)
        self.war_notification.start()

    def cog_unload(self):
        self.war_notification.cancel()

    @staticmethod
    def _clean_clan_tag(clan_tag: str) -> str:
        return "%23" + clan_tag.replace("#", "")

    @staticmethod
    def _normalize_tag(tag: str) -> str:
        return "#" + tag.replace("#", "").upper()

    @staticmethod
    def _api_headers(api_key: str) -> dict:
        return {
            "Accept": "application/json",
            "authorization": "Bearer " + api_key,
        }

    async def _get_clan_tag(self, ctx: commands.Context) -> str:
        return await self.config.guild(ctx.guild).COC_CLAN_KEY()

    async def _get_war_channel_id(self, ctx: commands.Context) -> str:
        return await self.config.guild(ctx.guild).COC_WAR_CHANNEL()

    @staticmethod
    def _missing_api_key_message(ctx: commands.Context) -> str:
        return (
            "No API key set for Clash of Clans. Bot owners can get one at "
            f"{COC_DEVELOPER_URL} and set it with "
            f"`{ctx.clean_prefix}coc setapi <api_key>`."
        )

    @staticmethod
    def _missing_clan_key_message(ctx: commands.Context) -> str:
        return (
            "No clan tag set for Clash of Clans. Copy the clan tag from the "
            f"clan profile, then use `{ctx.clean_prefix}coc setclan <clan_tag>`."
        )

    @staticmethod
    async def _response_detail(response: aiohttp.ClientResponse) -> str:
        try:
            payload = await response.json(content_type=None)
            return payload.get("reason") or payload.get("message") or ""
        except Exception:
            return (await response.text()).strip()

    @classmethod
    async def _send_api_error(
        cls, ctx: commands.Context, response: aiohttp.ClientResponse, *, current_war: bool = False
    ) -> None:
        detail = await cls._response_detail(response)

        if detail:
            detail = f": {detail[:300]}"

        if current_war and response.status == 403:
            await ctx.send(
                f"Oops! Clash of Clans API returned HTTP {response.status}{detail}. "
                "The clan profile can be read, but current war data is blocked. "
                "Check that the clan war log is public."
            )
            return

        await ctx.send(
            f"Oops! Clash of Clans API returned HTTP {response.status}{detail}. "
            "If this is 403, check the API key and allowed IP address. "
            "If this is 404, check the clan tag."
        )

    async def _fetch_cwl_war(self, clan_tag: str, headers: dict) -> tuple[dict, str]:
        normalized_clan_tag = self._normalize_tag(clan_tag)
        league_group_url = f"{COC_API_BASE}/clans/{self._clean_clan_tag(clan_tag)}/currentwar/leaguegroup"

        async with aiohttp.request("GET", league_group_url, headers=headers) as response:
            if response.status != 200:
                detail = await self._response_detail(response)
                if detail:
                    detail = f": {detail[:300]}"
                return {}, f"CWL league group returned HTTP {response.status}{detail}."
            league_group = await response.json()

        if league_group.get("state") == "notInWar":
            return {}, "The clan is not currently in Clan War League."

        matching_wars = []
        war_tags = [
            war_tag
            for round_data in league_group.get("rounds", [])
            for war_tag in round_data.get("warTags", [])
            if war_tag and war_tag != "#0"
        ]

        for war_tag in war_tags:
            war_url = f"{COC_API_BASE}/clanwarleagues/wars/{self._clean_clan_tag(war_tag)}"
            async with aiohttp.request("GET", war_url, headers=headers) as response:
                if response.status != 200:
                    continue
                war = await response.json()

            clan = war.get("clan", {})
            opponent = war.get("opponent", {})
            if normalized_clan_tag in {clan.get("tag"), opponent.get("tag")}:
                matching_wars.append(war)

        for state in ("inWar", "preparation"):
            for war in matching_wars:
                if war.get("state") == state:
                    return war, ""

        if matching_wars:
            return matching_wars[-1], ""
        return {}, "CWL league group was found, but no matching war was available for this clan."

    async def _fetch_current_war(self, clan_tag: str, headers: dict) -> tuple[dict, str]:
        clan_war_url = f"{COC_API_BASE}/clans/{self._clean_clan_tag(clan_tag)}/currentwar"

        async with aiohttp.request("GET", clan_war_url, headers=headers) as response:
            if response.status == 200:
                war_data = await response.json()
                if war_data.get("state") != "notInWar":
                    return war_data, ""

                cwl_data, cwl_error = await self._fetch_cwl_war(clan_tag, headers)
                if cwl_data:
                    return cwl_data, "No regular war is active, but I found a current CWL war."
                return {}, f"This clan is not currently in a regular war or CWL war. {cwl_error}"

            if response.status == 403:
                cwl_data, cwl_error = await self._fetch_cwl_war(clan_tag, headers)
                if cwl_data:
                    return cwl_data, "Regular war data is blocked, but I found a current CWL war."
                return {}, (
                    "Regular war data is blocked, and CWL fallback did not return war data. "
                    f"{cwl_error}"
                )

            detail = await self._response_detail(response)
            if detail:
                detail = f": {detail[:300]}"
            return {}, f"Clash of Clans API returned HTTP {response.status}{detail}."

    @staticmethod
    def _war_fingerprint(war_data: dict) -> str:
        clan = war_data.get("clan", {})
        opponent = war_data.get("opponent", {})
        return "|".join(
            str(part)
            for part in (
                war_data.get("state"),
                war_data.get("preparationStartTime"),
                war_data.get("startTime"),
                war_data.get("endTime"),
                clan.get("tag"),
                clan.get("attacks"),
                clan.get("stars"),
                clan.get("destructionPercentage"),
                opponent.get("tag"),
                opponent.get("attacks"),
                opponent.get("stars"),
                opponent.get("destructionPercentage"),
            )
        )

    @staticmethod
    def _format_coc_time(raw_time: str) -> str:
        try:
            war_time = datetime.strptime(raw_time, "%Y%m%dT%H%M%S.%fZ")
        except (TypeError, ValueError):
            return "Unknown"
        return (war_time - timedelta(hours=5)).strftime("%b %d, %Y at %I:%M %p")

    @staticmethod
    def _format_percent(value) -> str:
        return f"{float(value):.2f}%"

    @staticmethod
    def _war_state_label(state: str) -> str:
        return {
            "preparation": "Preparation",
            "inWar": "Battle Day",
            "warEnded": "Ended",
            "notInWar": "Not in War",
        }.get(state, state)

    def _build_war_embed(self, war_data: dict, clan_tag: str) -> discord.Embed:
        configured_tag = self._normalize_tag(clan_tag)
        clan = war_data.get("clan", {})
        opponent = war_data.get("opponent", {})
        if opponent.get("tag") == configured_tag:
            clan, opponent = opponent, clan

        state = war_data.get("state", "unknown")
        team_size = war_data.get("teamSize", "Unknown")
        attacks_per_member = war_data.get("attacksPerMember", 2)
        total_attacks = team_size * attacks_per_member if isinstance(team_size, int) else "Unknown"
        color = 0x2ecc71 if state == "preparation" else 0x992d22

        clan_name = clan.get("name", "Your Clan")
        opponent_name = opponent.get("name", "Opponent")
        clan_attacks = clan.get("attacks", 0)
        opponent_attacks = opponent.get("attacks", 0)

        embed = discord.Embed(
            title=f"{clan_name} vs {opponent_name}",
            description=(
                f"Status: **{self._war_state_label(state)}**\n"
                f"Team size: **{team_size}v{team_size}**"
            ),
            color=color,
            timestamp=None,
        )

        badge_url = clan.get("badgeUrls", {}).get("large")
        if badge_url:
            embed.set_thumbnail(url=badge_url)

        attack_total = total_attacks if isinstance(total_attacks, int) else "?"
        embed.add_field(
            name=clan_name,
            value=(
                f"Stars: **{clan.get('stars', 0)}**\n"
                f"Destruction: **{self._format_percent(clan.get('destructionPercentage', 0))}**\n"
                f"Attacks: **{clan_attacks}/{attack_total}**"
            ),
            inline=True,
        )
        embed.add_field(
            name=opponent_name,
            value=(
                f"Stars: **{opponent.get('stars', 0)}**\n"
                f"Destruction: **{self._format_percent(opponent.get('destructionPercentage', 0))}**\n"
                f"Attacks: **{opponent_attacks}/{attack_total}**"
            ),
            inline=True,
        )

        schedule = [
            ("Prep", "preparationStartTime"),
            ("Start", "startTime"),
            ("End", "endTime"),
        ]
        schedule_lines = [
            f"{label}: **{self._format_coc_time(war_data[key])} EST**"
            for label, key in schedule
            if war_data.get(key)
        ]
        if schedule_lines:
            embed.add_field(name="Schedule", value="\n".join(schedule_lines), inline=False)

        if state == "notInWar":
            embed.add_field(name="Status", value="This clan is not currently in a war.", inline=False)

        embed.set_footer(text="Brought to you by SickGaming.net")
        return embed

    @staticmethod
    async def _send_embed_with_optional_image(
        destination, embed: discord.Embed, image_path: Path, filename: str
    ) -> None:
        if image_path.exists():
            embed.set_image(url=f"attachment://{filename}")
            await destination.send(embed=embed, file=discord.File(str(image_path), filename=filename))
        else:
            await destination.send(embed=embed)
    
    @tasks.loop(minutes=5)
    async def war_notification(self) -> None:
        api_key = await self.config.COC_API_KEY()
        if not api_key:
            return

        headers = self._api_headers(api_key)
        all_guilds = await self.config.all_guilds()
        for guild_id, settings in all_guilds.items():
            if not settings.get("COC_WAR_NOTIFICATIONS"):
                continue

            guild_id = int(guild_id)
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue

            clan_tag = settings.get("COC_CLAN_KEY")
            channel_id = settings.get("COC_WAR_CHANNEL")
            if not clan_tag or not channel_id:
                continue

            channel = guild.get_channel(int(channel_id))
            if channel is None:
                log.warning("Configured CoC war channel %s was not found in guild %s.", channel_id, guild_id)
                continue

            try:
                war_data, notice = await self._fetch_current_war(clan_tag, headers)
            except aiohttp.ClientConnectionError as exc:
                log.warning("Could not fetch CoC war data for guild %s: %s", guild_id, exc)
                continue
            except Exception:
                log.exception("Unexpected error while checking CoC war notifications for guild %s", guild_id)
                continue

            if not war_data:
                log.debug("No CoC war notification sent for guild %s: %s", guild_id, notice)
                continue

            fingerprint = self._war_fingerprint(war_data)
            guild_config = self.config.guild(guild)
            if fingerprint == await guild_config.LAST_NOTIFICATION_STATE():
                continue

            try:
                if notice:
                    await channel.send(notice)
                embed = self._build_war_embed(war_data, clan_tag)
                await self._send_embed_with_optional_image(channel, embed, WAR_BANNER_PATH, "war-banner.png")
            except discord.HTTPException:
                log.exception("Could not send CoC war notification to channel %s in guild %s.", channel_id, guild_id)
                continue
            await guild_config.WAR_START_TIME.set(war_data.get("startTime"))
            await guild_config.WAR_END_TIME.set(war_data.get("endTime"))
            await guild_config.LAST_API_PULL.set((datetime.now() - timedelta(hours=5)).isoformat())
            await guild_config.LAST_NOTIFICATION_TIMESTAMP.set((datetime.now() - timedelta(hours=5)).isoformat())
            await guild_config.LAST_NOTIFICATION_STATE.set(fingerprint)

    @war_notification.before_loop
    async def before_war_notification(self):
        await self.bot.wait_until_red_ready()

    @commands.guild_only()
    @commands.group(invoke_without_command=True, aliases=['clashofclans'], name='coc')
    async def command_coc(self, ctx):
        """
        Show Clash of Clans clan information and war results.

        Setup:
        [p]coc setapi <api_key>
        [p]coc setclan <clan_tag>
        [p]coc setwarchannel [channel]  (also turns notifications on)

        Examples:
        [p]coc
        [p]coc war
        [p]coc notifications
        [p]coc setwarchannel
        """

        api_key = await self.config.COC_API_KEY()
        if not api_key:
            return await ctx.send(self._missing_api_key_message(ctx))
        clan_key = await self._get_clan_tag(ctx)
        if not clan_key:
            return await ctx.send(self._missing_clan_key_message(ctx))
        clan_tag_encoded = self._clean_clan_tag(clan_key)
        headers = self._api_headers(api_key)

        try:
            async with aiohttp.request(
                "GET", f"{COC_API_BASE}/clans/{clan_tag_encoded}", headers=headers
            ) as response:
                if response.status != 200:
                    return await self._send_api_error(ctx, response)
                user_json = await response.json()
        except aiohttp.ClientConnectionError as e:
            await ctx.send(f"Oops! Couldn't return results from COC api due to a connection error: {e}")
            return
        except Exception as e:
            await ctx.send(f"An unexpected error occurred: {e}")
            return
        
        clan_name = str(user_json.get("name", "Clash of Clans"))
        clan_tag = user_json.get("tag", "Unknown")
        clan_description = user_json.get("description") or "No clan description set."
        members_count = user_json.get("members", "Unknown")
        war_frequency = user_json.get("warFrequency", "Unknown")
        
        embed = discord.Embed(
            description=clan_description,
            color=0x2ecc71,
            timestamp=None
        )
        badge_url = user_json.get("badgeUrls", {}).get("large")
        footer_icon_url = "https://i.imgur.com/TFTXZvP.png"
        if badge_url:
            embed.set_author(name=clan_name, icon_url=badge_url)
            embed.set_thumbnail(url=badge_url)
        
        embed.add_field(name="Join Tag", value=clan_tag)
        embed.add_field(name="Member Count", value=members_count)
        embed.add_field(name="War Frequency", value=war_frequency)
        embed.set_footer(text="Brought to you by SickGaming.net", icon_url=footer_icon_url)
        
        await self._send_embed_with_optional_image(ctx, embed, CLAN_BANNER_PATH, "clan-banner.png")
        
    @command_coc.command(name="war")
    async def command_coc_war(self, ctx):
        """Show a quick Clash of Clans war update."""

        api_key = await self.config.COC_API_KEY()
        if not api_key:
            return await ctx.send(self._missing_api_key_message(ctx))
        
        clan_key = await self._get_clan_tag(ctx)
        if not clan_key:
            return await ctx.send(self._missing_clan_key_message(ctx))
        headers = self._api_headers(api_key)
        try:
            user_json, notice = await self._fetch_current_war(clan_key, headers)
        except aiohttp.ClientConnectionError as e:
            await ctx.send(f"Oops! Couldn't return results from COC api due to a connection error: {e}")
            return
        except Exception as e:
            await ctx.send(f"An unexpected error occurred: {e}")
            return

        if not user_json:
            return await ctx.send(notice)
        if notice:
            await ctx.send(notice)

        embed = self._build_war_embed(user_json, clan_key)
        guild_config = self.config.guild(ctx.guild)
        await guild_config.WAR_START_TIME.set(user_json.get("startTime"))
        await guild_config.WAR_END_TIME.set(user_json.get("endTime"))
        await guild_config.LAST_API_PULL.set((datetime.now() - timedelta(hours=5)).isoformat())
        await self._send_embed_with_optional_image(ctx, embed, WAR_BANNER_PATH, "war-banner.png")
        
    @command_coc.command(name="notifications", aliases=["notify", "warnotification"])
    async def command_coc_warnotification(self, ctx):
        """Toggle Clash of Clans war notifications for this server."""

        guild_config = self.config.guild(ctx.guild)
        enabled = await guild_config.COC_WAR_NOTIFICATIONS()
        if enabled:
            await guild_config.COC_WAR_NOTIFICATIONS.set(False)
            return await ctx.send(
                "Clash of Clans war notifications are now off for this server. "
                f"Turn them back on with `{ctx.clean_prefix}coc notifications`."
            )

        api_key = await self.config.COC_API_KEY()
        if not api_key:
            return await ctx.send(self._missing_api_key_message(ctx))
        
        clan_key = await self._get_clan_tag(ctx)
        if not clan_key:
            return await ctx.send(self._missing_clan_key_message(ctx))
        
        coc_war_channel = await self._get_war_channel_id(ctx)
        if not coc_war_channel:
            return await ctx.send(
                "No war update channel is set yet. Use "
                f"`{ctx.clean_prefix}coc setwarchannel` to use this channel, or "
                f"`{ctx.clean_prefix}coc setwarchannel #channel` to choose one."
            )
        
        await guild_config.COC_WAR_NOTIFICATIONS.set(True)
        await guild_config.LAST_NOTIFICATION_STATE.set(None)
        channel = ctx.guild.get_channel(int(coc_war_channel))
        channel_name = channel.mention if channel else f"`{coc_war_channel}`"
        await ctx.send(
            "Clash of Clans war notifications are now on.\n"
            f"Clan: `{self._normalize_tag(clan_key)}`\n"
            f"Channel: {channel_name}\n"
            "I will check for war updates about every 5 minutes and post when the war status or score changes."
        )

    @checks.is_owner()
    @command_coc.command(name="setapi", aliases=["setcocapi", "setcoc"])
    async def command_coc_setcocapi(self, ctx, key: str):
        """Set the global Clash of Clans API key."""

        if key:
            await self.config.COC_API_KEY.set(key)
            await ctx.send("Key set.")

    @commands.guild_only()
    @checks.mod_or_permissions(manage_channels=True)
    @command_coc.command(name="setclan", aliases=["setcocclankey", "setcocclan"])
    async def command_coc_setcocclankey(self, ctx, key: str):
        """Set this server's Clash of Clans clan tag."""

        if key:
            guild_config = self.config.guild(ctx.guild)
            await guild_config.COC_CLAN_KEY.set(key)
            await guild_config.LAST_NOTIFICATION_STATE.set(None)
            await ctx.send("Key set.")

    @commands.guild_only()
    @checks.mod_or_permissions(manage_channels=True)
    @command_coc.command(name="setwarchannel", aliases=["setcocwarchannel"])
    async def command_coc_setcocwarchannel(self, ctx, channel: discord.TextChannel = None):
        """
        Set this server's Clash of Clans war update channel.
        
        Defaults to the current channel and turns notifications on.
        """

        channel = channel or ctx.channel
        guild_config = self.config.guild(ctx.guild)
        await guild_config.COC_WAR_CHANNEL.set(channel.id)
        await guild_config.COC_WAR_NOTIFICATIONS.set(True)
        await guild_config.LAST_NOTIFICATION_STATE.set(None)
        await ctx.send(f"War updates will be sent to {channel.mention}. Notifications are now on.")
