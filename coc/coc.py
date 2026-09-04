import aiohttp
import discord
from discord.ext import tasks
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from red_commons.logging import getLogger
from redbot.core import Config, commands, checks
from redbot.core.i18n import Translator

_ = Translator("Coc", __file__)
log = getLogger("red.Sick-Cogs.Coc")
COC_API_BASE = "https://api.clashofclans.com/v1"
COC_DEVELOPER_URL = "https://developer.clashofclans.com/"
CLAN_BANNER_PATH = Path(__file__).parent / "data" / "images" / "clan-banner.png"
WAR_BANNER_PATH = Path(__file__).parent / "data" / "images" / "war-banner.png"
WAR_NOTIFICATION_EVENTS = {
    "prep": "Preparation Started",
    "prepsoon": "Preparation Ending Soon",
    "battle": "Battle Day Started",
    "attacklog": "Attack Log Updates",
    "endsoon": "War Ending Soon",
    "ended": "War Ended",
}
WAR_NOTIFICATION_SETTING_NAMES = {
    "prep": "WAR_NOTIFY_PREP",
    "prepsoon": "WAR_NOTIFY_PREP_SOON",
    "battle": "WAR_NOTIFY_BATTLE",
    "attacklog": "WAR_NOTIFY_ATTACK_LOG",
    "endsoon": "WAR_NOTIFY_END_SOON",
    "ended": "WAR_NOTIFY_ENDED",
}
WAR_NOTIFICATION_MENTION_SETTING_NAMES = {
    "prep": "WAR_NOTIFY_PREP_MENTION",
    "prepsoon": "WAR_NOTIFY_PREP_SOON_MENTION",
    "battle": "WAR_NOTIFY_BATTLE_MENTION",
    "attacklog": "WAR_NOTIFY_ATTACK_LOG_MENTION",
    "endsoon": "WAR_NOTIFY_END_SOON_MENTION",
    "ended": "WAR_NOTIFY_ENDED_MENTION",
}
WAR_NOTIFICATION_EVENT_ALIASES = {
    "preparation": "prep",
    "preparationstarted": "prep",
    "prepstarted": "prep",
    "preparationsoon": "prepsoon",
    "preparationending": "prepsoon",
    "preparationendingsoon": "prepsoon",
    "warstartingsoon": "prepsoon",
    "battleday": "battle",
    "battlestarted": "battle",
    "battledaystarted": "battle",
    "attacks": "attacklog",
    "attacklogs": "attacklog",
    "warending": "endsoon",
    "warendingsoon": "endsoon",
    "end": "ended",
    "warended": "ended",
}


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
            "COC_TIMEZONE": "America/New_York",
            "COC_WAR_MENTION_ROLE": None,
            "WAR_NOTIFY_PREP": True,
            "WAR_NOTIFY_PREP_MENTION": True,
            "WAR_NOTIFY_PREP_SOON": True,
            "WAR_NOTIFY_PREP_SOON_MENTION": True,
            "WAR_PREP_SOON_MINUTES": 5,
            "WAR_NOTIFY_BATTLE": True,
            "WAR_NOTIFY_BATTLE_MENTION": True,
            "WAR_NOTIFY_ATTACK_LOG": True,
            "WAR_NOTIFY_ATTACK_LOG_MENTION": True,
            "WAR_ATTACK_LOG_STYLE": "card",
            "WAR_NOTIFY_END_SOON": True,
            "WAR_NOTIFY_END_SOON_MENTION": True,
            "WAR_END_SOON_MINUTES": 60,
            "WAR_NOTIFY_ENDED": True,
            "WAR_NOTIFY_ENDED_MENTION": True,
            "LAST_WAR_ID": None,
            "WAR_NOTIFICATION_EVENTS": {},
            "WAR_START_TIME": None,
            "WAR_END_TIME": None,
            "WAR_PRE_HOURS_END": 1,
            "LAST_NOTIFICATION_TIMESTAMP": None,
            "LAST_NOTIFICATION_STATE": None,
            "LAST_WAR_ATTACKS": [],
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
            war["_sickgaming_war_type"] = "cwl"

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
                    return cwl_data, ""
                return {}, f"This clan is not currently in a regular war or CWL war. {cwl_error}"

            if response.status == 403:
                cwl_data, cwl_error = await self._fetch_cwl_war(clan_tag, headers)
                if cwl_data:
                    return cwl_data, ""
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
    def _war_id(war_data: dict) -> str:
        clan = war_data.get("clan", {})
        opponent = war_data.get("opponent", {})
        return "|".join(
            str(part)
            for part in (
                war_data.get("preparationStartTime"),
                war_data.get("startTime"),
                war_data.get("endTime"),
                clan.get("tag"),
                opponent.get("tag"),
            )
        )

    @staticmethod
    def _parse_coc_time(raw_time: str) -> datetime | None:
        try:
            return datetime.strptime(raw_time, "%Y%m%dT%H%M%S.%fZ")
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _notification_event_key(event: str) -> str:
        normalized = event.lower().replace("-", "").replace("_", "").replace(" ", "")
        return WAR_NOTIFICATION_EVENT_ALIASES.get(normalized, normalized)

    @staticmethod
    def _event_enabled(settings: dict, event: str) -> bool:
        return bool(settings.get(WAR_NOTIFICATION_SETTING_NAMES[event], True))

    @staticmethod
    def _event_mention_enabled(settings: dict, event: str) -> bool:
        return bool(settings.get(WAR_NOTIFICATION_MENTION_SETTING_NAMES[event], False))

    @staticmethod
    def _mention_for_event(guild: discord.Guild, settings: dict, event: str) -> tuple[str | None, discord.AllowedMentions | None]:
        role_id = settings.get("COC_WAR_MENTION_ROLE")
        if not role_id or not Coc._event_mention_enabled(settings, event):
            return None, None

        try:
            role = guild.get_role(int(role_id))
        except (TypeError, ValueError):
            return None, None

        if role is None:
            return None, None

        return role.mention, discord.AllowedMentions(roles=[role])

    @staticmethod
    def _positive_int(value, default: int) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return default

    @classmethod
    def _configured_war_sides(cls, war_data: dict, clan_tag: str) -> tuple[dict, dict]:
        configured_tag = cls._normalize_tag(clan_tag)
        clan = war_data.get("clan", {})
        opponent = war_data.get("opponent", {})
        if opponent.get("tag") == configured_tag:
            clan, opponent = opponent, clan
        return clan, opponent

    @staticmethod
    def _attack_key(war_data: dict, attacker_tag: str, attack: dict) -> str:
        return "|".join(
            str(part)
            for part in (
                war_data.get("preparationStartTime"),
                war_data.get("startTime"),
                war_data.get("endTime"),
                attacker_tag,
                attack.get("defenderTag"),
                attack.get("order"),
                attack.get("stars"),
                attack.get("destructionPercentage"),
                attack.get("duration"),
            )
        )

    @classmethod
    def _iter_war_attacks(cls, war_data: dict, clan_tag: str):
        clan, opponent = cls._configured_war_sides(war_data, clan_tag)

        sides = ((clan, opponent, True), (opponent, clan, False))
        for attacking_side, defending_side, is_friendly in sides:
            defending_members = {
                member.get("tag"): member.get("name", "Unknown")
                for member in defending_side.get("members", [])
            }
            side_name = attacking_side.get("name", "Unknown Clan")
            defending_side_name = defending_side.get("name", "Unknown Clan")
            for member in attacking_side.get("members", []):
                attacker_tag = member.get("tag")
                attacker_name = member.get("name", "Unknown")
                for attack in member.get("attacks", []):
                    defender_tag = attack.get("defenderTag")
                    yield {
                        "key": cls._attack_key(war_data, attacker_tag, attack),
                        "side_name": side_name,
                        "defending_side_name": defending_side_name,
                        "is_friendly": is_friendly,
                        "attacker_name": attacker_name,
                        "defender_name": defending_members.get(defender_tag, defender_tag or "Unknown"),
                        "stars": attack.get("stars", 0),
                        "destruction": attack.get("destructionPercentage", 0),
                        "order": attack.get("order", 0),
                    }

    @classmethod
    def _current_attack_keys(cls, war_data: dict, clan_tag: str) -> list[str]:
        return [attack["key"] for attack in cls._iter_war_attacks(war_data, clan_tag)]

    @classmethod
    def _new_war_attacks(cls, war_data: dict, clan_tag: str, previous_attack_keys: list[str]) -> list[dict]:
        previous = set(previous_attack_keys or [])
        if not previous:
            return []

        new_attacks = [
            attack
            for attack in cls._iter_war_attacks(war_data, clan_tag)
            if attack["key"] not in previous
        ]
        new_attacks.sort(key=lambda attack: attack["order"])
        return new_attacks

    @classmethod
    def _new_attack_summaries(
        cls, war_data: dict, clan_tag: str, previous_attack_keys: list[str]
    ) -> list[str]:
        new_attacks = cls._new_war_attacks(war_data, clan_tag, previous_attack_keys)
        lines = []
        for attack in new_attacks[:6]:
            stars = attack["stars"]
            star_word = "star" if stars == 1 else "stars"
            attack_type = "Friendly" if attack["is_friendly"] else "Enemy"
            lines.append(
                f"**{attack_type}: {attack['side_name']}**\n"
                f"**{attack['attacker_name']}** attacked **{attack['defender_name']}**\n"
                f"Result: **{stars} {star_word}**, **{cls._format_percent(attack['destruction'])}** "
                "destruction."
            )

        remaining = len(new_attacks) - len(lines)
        if remaining > 0:
            lines.append(f"...and {remaining} more new attack{'s' if remaining != 1 else ''}.")

        while len("\n".join(lines)) > 1024 and lines:
            lines.pop()
            hidden_count = len(new_attacks) - len(lines)
            lines.append(f"...and {hidden_count} more new attack{'s' if hidden_count != 1 else ''}.")

        return lines

    @staticmethod
    def _trim_embed_lines(lines: list[str], max_chars: int = 1024) -> str:
        if not lines:
            return "None."

        visible = []
        for line in lines:
            candidate = "\n".join(visible + [line])
            if len(candidate) <= max_chars:
                visible.append(line)
                continue
            break

        hidden_count = len(lines) - len(visible)
        if hidden_count > 0:
            suffix = f"...and {hidden_count} more."
            while visible and len("\n".join(visible + [suffix])) > max_chars:
                visible.pop()
                hidden_count = len(lines) - len(visible)
                suffix = f"...and {hidden_count} more."
            visible.append(suffix)

        return "\n".join(visible) if visible else "Too many entries to show."

    @staticmethod
    def _war_result_label(clan: dict, opponent: dict) -> str:
        clan_stars = int(clan.get("stars", 0) or 0)
        opponent_stars = int(opponent.get("stars", 0) or 0)
        clan_destruction = float(clan.get("destructionPercentage", 0) or 0)
        opponent_destruction = float(opponent.get("destructionPercentage", 0) or 0)

        if clan_stars > opponent_stars:
            return "Victory"
        if clan_stars < opponent_stars:
            return "Defeat"
        if clan_destruction > opponent_destruction:
            return "Victory by destruction"
        if clan_destruction < opponent_destruction:
            return "Defeat by destruction"
        return "Tie"

    @staticmethod
    def _max_war_stars(war_data: dict) -> int | str:
        team_size = war_data.get("teamSize")
        return team_size * 3 if isinstance(team_size, int) else "?"

    @classmethod
    def _format_war_stars(cls, stars, war_data: dict) -> str:
        return f"{int(stars or 0)}/{cls._max_war_stars(war_data)}"

    @classmethod
    def _war_member_attack_stats(cls, war_data: dict, clan_tag: str) -> list[dict]:
        clan, _ = cls._configured_war_sides(war_data, clan_tag)
        attacks_per_member = cls._positive_int(war_data.get("attacksPerMember"), 2)
        stats = []

        for member in clan.get("members", []):
            attacks = member.get("attacks", [])
            used = len(attacks)
            stars = sum(int(attack.get("stars", 0) or 0) for attack in attacks)
            destruction = sum(float(attack.get("destructionPercentage", 0) or 0) for attack in attacks)
            stats.append(
                {
                    "name": member.get("name", "Unknown"),
                    "map_position": member.get("mapPosition", 0),
                    "used": used,
                    "remaining": max(0, attacks_per_member - used),
                    "stars": stars,
                    "destruction": destruction,
                    "zero_star_attacks": sum(1 for attack in attacks if int(attack.get("stars", 0) or 0) == 0),
                }
            )

        return stats

    @staticmethod
    def _format_coc_time(raw_time: str, timezone_name: str = "America/New_York") -> str:
        war_time = Coc._parse_coc_time(raw_time)
        if war_time is None:
            return "Unknown"
        try:
            local_zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            local_zone = ZoneInfo("America/New_York")
        local_time = war_time.replace(tzinfo=timezone.utc).astimezone(local_zone)
        return local_time.strftime("%b %d, %Y at %I:%M %p %Z")

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

    @staticmethod
    def _war_type_label(war_data: dict) -> str:
        if war_data.get("_sickgaming_war_type") == "cwl":
            return "Clan War League (CWL)"
        return "Clan War"

    def _build_war_embed(
        self,
        war_data: dict,
        clan_tag: str,
        battle_log: list[str] | None = None,
        title: str | None = None,
        intro: str | None = None,
        schedule_keys: tuple[tuple[str, str], ...] | None = None,
        timezone_name: str = "America/New_York",
    ) -> discord.Embed:
        clan, opponent = self._configured_war_sides(war_data, clan_tag)

        state = war_data.get("state", "unknown")
        team_size = war_data.get("teamSize", "Unknown")
        attacks_per_member = war_data.get("attacksPerMember", 2)
        total_attacks = team_size * attacks_per_member if isinstance(team_size, int) else "Unknown"
        color = {
            "preparation": 0xF1C40F,
            "inWar": 0xE67E22,
        }.get(state, 0x992D22)

        clan_name = clan.get("name", "Your Clan")
        opponent_name = opponent.get("name", "Opponent")
        clan_attacks = clan.get("attacks", 0)
        opponent_attacks = opponent.get("attacks", 0)

        embed = discord.Embed(
            title=title or f"{clan_name} vs {opponent_name}",
            description=(
                f"{intro}\n\n" if intro else ""
            ) + (
                f"**{self._war_type_label(war_data)}**\n"
                f"Status: **{self._war_state_label(state)}**\n"
                f"Team size: **{team_size}v{team_size}**"
            ),
            color=color,
            timestamp=None,
        )

        badge_url = clan.get("badgeUrls", {}).get("large")
        if badge_url:
            embed.set_thumbnail(url=badge_url)

        if battle_log:
            embed.add_field(name="War Battle Log", value="\n".join(battle_log), inline=False)

        attack_total = total_attacks if isinstance(total_attacks, int) else "?"
        embed.add_field(
            name=clan_name,
            value=(
                f"Stars: **{self._format_war_stars(clan.get('stars', 0), war_data)}**\n"
                f"Destruction: **{self._format_percent(clan.get('destructionPercentage', 0))}**\n"
                f"Attacks: **{clan_attacks}/{attack_total}**"
            ),
            inline=True,
        )
        embed.add_field(
            name=opponent_name,
            value=(
                f"Stars: **{self._format_war_stars(opponent.get('stars', 0), war_data)}**\n"
                f"Destruction: **{self._format_percent(opponent.get('destructionPercentage', 0))}**\n"
                f"Attacks: **{opponent_attacks}/{attack_total}**"
            ),
            inline=True,
        )

        schedule = schedule_keys or (
            [("War Ends", "endTime")]
            if state == "inWar"
            else [
                ("Prep", "preparationStartTime"),
                ("Start", "startTime"),
                ("End", "endTime"),
            ]
        )
        schedule_lines = [
            f"{label}: **{self._format_coc_time(war_data[key], timezone_name)}**"
            for label, key in schedule
            if war_data.get(key)
        ]
        if schedule_lines:
            embed.add_field(name="Schedule", value="\n".join(schedule_lines), inline=False)

        if state == "notInWar":
            embed.add_field(name="Status", value="This clan is not currently in a war.", inline=False)

        embed.set_footer(text="Brought to you by SickGaming.net")
        return embed

    def _build_war_attack_update_embed(
        self, war_data: dict, clan_tag: str, new_attacks: list[dict], *, style: str = "card", timezone_name: str = "America/New_York"
    ) -> discord.Embed:
        clan, opponent = self._configured_war_sides(war_data, clan_tag)
        enemy_attack = any(not attack["is_friendly"] for attack in new_attacks)
        embed = discord.Embed(
            title="War Log Update",
            description=(
                f"**{self._war_type_label(war_data)}**\n"
                f"{clan.get('name', 'Your Clan')} vs {opponent.get('name', 'Opponent')}"
            ),
            color=0x992D22 if enemy_attack else 0x2ECC71,
            timestamp=None,
        )

        if style == "compact":
            lines = []
            for attack in new_attacks[:10]:
                direction_marker = "🔵" if attack["is_friendly"] else "🔴"
                attacker_marker = "🔵" if attack["is_friendly"] else "🟠"
                defender_marker = "🟠" if attack["is_friendly"] else "🔵"
                attack_type = "Friendly Attack" if attack["is_friendly"] else "Enemy Attack"
                stars = "⭐" * int(attack["stars"] or 0) or "No stars"
                lines.append(
                    f"{direction_marker} **{attack_type}** · {attacker_marker} **{attack['attacker_name']}** → "
                    f"{defender_marker} **{attack['defender_name']}** | {stars} | "
                    f"**{self._format_percent(attack['destruction'])}**"
                )
            remaining = len(new_attacks) - len(lines)
            if remaining > 0:
                lines.append(f"…and {remaining} more attack{'s' if remaining != 1 else ''}.")
            if enemy_attack and any(attack["is_friendly"] for attack in new_attacks):
                embed.color = 0x5865F2
            embed.description += "\n\n" + "\n".join(lines)
            embed.set_footer(text="Brought to you by SickGaming.net")
            return embed

        badge_url = opponent.get("badgeUrls", {}).get("large") if enemy_attack else clan.get("badgeUrls", {}).get("large")
        if badge_url:
            embed.set_thumbnail(url=badge_url)

        for attack in new_attacks[:6]:
            stars = attack["stars"]
            star_word = "star" if stars == 1 else "stars"
            attack_type = "Friendly Attack" if attack["is_friendly"] else "Enemy Attack"
            field_name = attack_type
            embed.add_field(
                name=field_name,
                value=(
                    f"**{attack['attacker_name']}** attacked **{attack['defender_name']}**\n"
                    f"Result: **{stars} {star_word}**, "
                    f"**{self._format_percent(attack['destruction'])}** destruction"
                ),
                inline=False,
            )

        remaining = len(new_attacks) - 6
        if remaining > 0:
            embed.add_field(name="More Attacks", value=f"...and {remaining} more.", inline=False)

        attack_total = war_data.get("teamSize", "Unknown")
        attacks_per_member = self._positive_int(war_data.get("attacksPerMember"), 2)
        if isinstance(attack_total, int):
            attack_total *= attacks_per_member
        else:
            attack_total = "?"

        embed.add_field(
            name=clan.get("name", "Your Clan"),
            value=(
                f"Stars: **{self._format_war_stars(clan.get('stars', 0), war_data)}**\n"
                f"Destruction: **{self._format_percent(clan.get('destructionPercentage', 0))}**\n"
                f"Attacks: **{clan.get('attacks', 0)}/{attack_total}**"
            ),
            inline=True,
        )
        embed.add_field(
            name=opponent.get("name", "Opponent"),
            value=(
                f"Stars: **{self._format_war_stars(opponent.get('stars', 0), war_data)}**\n"
                f"Destruction: **{self._format_percent(opponent.get('destructionPercentage', 0))}**\n"
                f"Attacks: **{opponent.get('attacks', 0)}/{attack_total}**"
            ),
            inline=True,
        )

        if war_data.get("endTime"):
            embed.add_field(
                name="War Ends",
                value=f"**{self._format_coc_time(war_data['endTime'], timezone_name)}**",
                inline=False,
            )

        embed.set_footer(text="Brought to you by SickGaming.net")
        return embed

    def _build_war_roundup_embed(self, war_data: dict, clan_tag: str, timezone_name: str = "America/New_York") -> discord.Embed:
        clan, opponent = self._configured_war_sides(war_data, clan_tag)
        clan_name = clan.get("name", "Your Clan")
        opponent_name = opponent.get("name", "Opponent")
        attack_total = war_data.get("teamSize", len(clan.get("members", [])))
        attacks_per_member = self._positive_int(war_data.get("attacksPerMember"), 2)
        if isinstance(attack_total, int):
            attack_total *= attacks_per_member
        else:
            attack_total = len(clan.get("members", [])) * attacks_per_member

        member_stats = self._war_member_attack_stats(war_data, clan_tag)
        used_attacks = sum(member["used"] for member in member_stats)
        unused_attacks = sum(member["remaining"] for member in member_stats)
        result = self._war_result_label(clan, opponent)
        result_marker = "🟢" if result.startswith("Victory") else "🔴" if result.startswith("Defeat") else "⚪"

        embed = discord.Embed(
            title="War Roundup",
            description=(
                f"**{self._war_type_label(war_data)}**\n"
                f"{clan_name} vs {opponent_name}\n"
                f"Result: {result_marker} **{result}**\n"
                f"Attacks used: **{used_attacks}/{attack_total}**"
            ),
            color=0x3498DB,
            timestamp=None,
        )

        badge_url = clan.get("badgeUrls", {}).get("large")
        if badge_url:
            embed.set_thumbnail(url=badge_url)

        embed.add_field(
            name=clan_name,
            value=(
                f"Stars: **{self._format_war_stars(clan.get('stars', 0), war_data)}**\n"
                f"Destruction: **{self._format_percent(clan.get('destructionPercentage', 0))}**\n"
                f"Attacks: **{clan.get('attacks', 0)}/{attack_total}**"
            ),
            inline=True,
        )
        embed.add_field(
            name=opponent_name,
            value=(
                f"Stars: **{self._format_war_stars(opponent.get('stars', 0), war_data)}**\n"
                f"Destruction: **{self._format_percent(opponent.get('destructionPercentage', 0))}**\n"
                f"Attacks: **{opponent.get('attacks', 0)}/{attack_total}**"
            ),
            inline=True,
        )

        member_lines = [
            (
                f"**{member['name']}** - {member['stars']} stars, "
                f"{member['used']}/{attacks_per_member} attacks"
            )
            for member in sorted(member_stats, key=lambda member: (member["map_position"], member["name"].lower()))
        ]
        embed.add_field(name="Member Results", value=self._trim_embed_lines(member_lines), inline=False)

        top_attackers = sorted(
            [member for member in member_stats if member["used"] > 0],
            key=lambda member: (-member["stars"], -member["destruction"], member["name"].lower()),
        )
        top_lines = [
            (
                f"**{member['name']}** - {member['stars']} stars in {member['used']} "
                f"attack{'s' if member['used'] != 1 else ''}"
            )
            for member in top_attackers[:8]
        ]
        embed.add_field(name="Top Attackers", value=self._trim_embed_lines(top_lines), inline=False)

        unused_members = sorted(
            [member for member in member_stats if member["remaining"] > 0],
            key=lambda member: (-member["remaining"], member["map_position"], member["name"].lower()),
        )
        unused_lines = [
            f"**{member['name']}** - {member['remaining']} attack{'s' if member['remaining'] != 1 else ''} unused"
            for member in unused_members
        ]
        embed.add_field(
            name=f"Unused Attacks ({unused_attacks})",
            value=self._trim_embed_lines(unused_lines),
            inline=False,
        )

        zero_star_lines = [
            f"**{member['name']}** - {member['zero_star_attacks']} zero-star attack{'s' if member['zero_star_attacks'] != 1 else ''}"
            for member in member_stats
            if member["zero_star_attacks"] > 0
        ]
        if zero_star_lines:
            embed.add_field(name="Zero-Star Attacks", value=self._trim_embed_lines(zero_star_lines), inline=False)

        if war_data.get("endTime"):
            embed.add_field(
                name="War Ended",
                value=f"**{self._format_coc_time(war_data['endTime'], timezone_name)}**",
                inline=False,
            )

        embed.set_footer(text="Brought to you by SickGaming.net")
        return embed

    def _build_war_attacks_embed(self, war_data: dict, clan_tag: str, timezone_name: str = "America/New_York") -> discord.Embed:
        clan, opponent = self._configured_war_sides(war_data, clan_tag)
        clan_name = clan.get("name", "Your Clan")
        opponent_name = opponent.get("name", "Opponent")
        state = war_data.get("state", "unknown")
        attacks_per_member = self._positive_int(war_data.get("attacksPerMember"), 2)
        member_stats = self._war_member_attack_stats(war_data, clan_tag)
        total_attacks = len(member_stats) * attacks_per_member
        used_attacks = sum(member["used"] for member in member_stats)
        unused_attacks = sum(member["remaining"] for member in member_stats)

        state_notes = {
            "preparation": "Battle day has not started yet.",
            "inWar": "Battle day is active.",
            "warEnded": "War has ended.",
        }
        embed = discord.Embed(
            title="War Attack Status",
            description=(
                f"**{self._war_type_label(war_data)}**\n"
                f"{clan_name} vs {opponent_name}\n"
                f"Status: **{self._war_state_label(state)}**\n"
                f"{state_notes.get(state, 'Current war status is available.')}"
            ),
            color=0x2ECC71 if state == "preparation" else 0x992D22,
            timestamp=None,
        )

        badge_url = clan.get("badgeUrls", {}).get("large")
        if badge_url:
            embed.set_thumbnail(url=badge_url)

        embed.add_field(
            name="Attack Summary",
            value=(
                f"Used: **{used_attacks}/{total_attacks}**\n"
                f"Remaining: **{unused_attacks}**\n"
                f"Stars: **{self._format_war_stars(clan.get('stars', 0), war_data)}**"
            ),
            inline=False,
        )

        member_lines = []
        for member in sorted(member_stats, key=lambda item: (item["map_position"], item["name"].lower())):
            status = ":green_square:" if member["remaining"] == 0 else ":red_square:"
            remaining = f", {member['remaining']} left" if member["remaining"] else ""
            member_lines.append(
                f"{status} **{member['name']}** - {member['used']}/{attacks_per_member} used"
                f"{remaining}, {member['stars']} stars"
            )
        embed.add_field(name="Member Results", value=self._trim_embed_lines(member_lines), inline=False)

        if war_data.get("endTime") and state in {"inWar", "warEnded"}:
            label = "War Ended" if state == "warEnded" else "War Ends"
            embed.add_field(
                name=label,
                value=f"**{self._format_coc_time(war_data['endTime'], timezone_name)}**",
                inline=False,
            )

        embed.set_footer(text="Brought to you by SickGaming.net")
        return embed

    def _build_war_event_embed(self, war_data: dict, clan_tag: str, event: str, timezone_name: str = "America/New_York") -> discord.Embed:
        if event == "ended":
            return self._build_war_roundup_embed(war_data, clan_tag, timezone_name)

        titles = {
            "prep": "War Preparation Started",
            "prepsoon": "War Starting Soon",
            "battle": "Battle Day Started",
            "endsoon": "War Ending Soon",
            "ended": "War Ended",
        }
        intros = {
            "prep": "Preparation day is live. Fill clan castles, check bases, and plan targets.",
            "prepsoon": "Preparations are almost over. Battle day is about to begin.",
            "battle": "Battle day has started. Attacks are open.",
            "endsoon": "War is almost over. Use remaining attacks before time runs out.",
            "ended": "War has ended. Final score is below.",
        }
        schedule_keys = None
        if event in {"battle", "endsoon", "ended"}:
            schedule_keys = (("War Ends", "endTime"),)
        return self._build_war_embed(
            war_data,
            clan_tag,
            title=titles[event],
            intro=intros[event],
            schedule_keys=schedule_keys,
            timezone_name=timezone_name,
        )

    @staticmethod
    async def _send_embed_with_optional_image(
        destination,
        embed: discord.Embed,
        image_path: Path,
        filename: str,
        content: str | None = None,
        allowed_mentions: discord.AllowedMentions | None = None,
    ) -> None:
        if image_path.exists():
            embed.set_image(url=f"attachment://{filename}")
            await destination.send(
                content=content,
                embed=embed,
                file=discord.File(str(image_path), filename=filename),
                allowed_mentions=allowed_mentions,
            )
        else:
            await destination.send(content=content, embed=embed, allowed_mentions=allowed_mentions)
    
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

            try:
                channel = guild.get_channel(int(channel_id))
            except (TypeError, ValueError):
                channel = None
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
            war_id = self._war_id(war_data)
            if war_id != settings.get("LAST_WAR_ID"):
                await guild_config.LAST_WAR_ID.set(war_id)
                await guild_config.WAR_NOTIFICATION_EVENTS.set({})
                await guild_config.LAST_NOTIFICATION_STATE.set(None)
                await guild_config.LAST_WAR_ATTACKS.set([])
                settings["LAST_WAR_ID"] = war_id
                settings["WAR_NOTIFICATION_EVENTS"] = {}
                settings["LAST_NOTIFICATION_STATE"] = None
                settings["LAST_WAR_ATTACKS"] = []

            sent_events = settings.get("WAR_NOTIFICATION_EVENTS") or {}
            previous_attack_keys = settings.get("LAST_WAR_ATTACKS") or []
            current_attack_keys = self._current_attack_keys(war_data, clan_tag)
            state = war_data.get("state")
            now = datetime.utcnow()
            start_time = self._parse_coc_time(war_data.get("startTime"))
            end_time = self._parse_coc_time(war_data.get("endTime"))
            events_to_send = []
            sent_any_notification = False
            prep_soon_minutes = self._positive_int(settings.get("WAR_PREP_SOON_MINUTES"), 5)
            prep_soon_due = (
                state == "preparation"
                and start_time is not None
                and now >= start_time - timedelta(minutes=prep_soon_minutes)
            )
            end_soon_minutes = self._positive_int(settings.get("WAR_END_SOON_MINUTES"), 60)
            end_soon_due = (
                state == "inWar"
                and end_time is not None
                and now >= end_time - timedelta(minutes=end_soon_minutes)
            )
            should_send_prep_soon = (
                prep_soon_due
                and self._event_enabled(settings, "prepsoon")
                and not sent_events.get("prepsoon")
            )
            should_send_end_soon = (
                end_soon_due
                and self._event_enabled(settings, "endsoon")
                and not sent_events.get("endsoon")
            )

            if (
                state == "preparation"
                and not should_send_prep_soon
                and self._event_enabled(settings, "prep")
                and not sent_events.get("prep")
            ):
                events_to_send.append("prep")

            if should_send_prep_soon:
                events_to_send.append("prepsoon")

            if (
                state == "inWar"
                and not should_send_end_soon
                and self._event_enabled(settings, "battle")
                and not sent_events.get("battle")
            ):
                events_to_send.append("battle")

            if should_send_end_soon:
                events_to_send.append("endsoon")

            if (
                state == "warEnded"
                and self._event_enabled(settings, "ended")
                and not sent_events.get("ended")
            ):
                events_to_send.append("ended")

            for event in events_to_send:
                try:
                    if notice:
                        await channel.send(notice)
                        notice = None
                    content, allowed_mentions = self._mention_for_event(guild, settings, event)
                    embed = self._build_war_event_embed(
                        war_data, clan_tag, event, str(settings.get("COC_TIMEZONE") or "America/New_York")
                    )
                    await self._send_embed_with_optional_image(
                        channel,
                        embed,
                        WAR_BANNER_PATH,
                        "war-banner.png",
                        content=content,
                        allowed_mentions=allowed_mentions,
                    )
                except discord.HTTPException:
                    log.exception(
                        "Could not send CoC %s notification to channel %s in guild %s.",
                        event,
                        channel_id,
                        guild_id,
                    )
                    continue
                sent_events[event] = (datetime.now() - timedelta(hours=5)).isoformat()
                sent_any_notification = True

            new_attacks = self._new_war_attacks(war_data, clan_tag, previous_attack_keys)
            if (
                new_attacks
                and self._event_enabled(settings, "attacklog")
                and fingerprint != settings.get("LAST_NOTIFICATION_STATE")
            ):
                try:
                    if notice:
                        await channel.send(notice)
                        notice = None
                    content, allowed_mentions = self._mention_for_event(guild, settings, "attacklog")
                    embed = self._build_war_attack_update_embed(
                        war_data,
                        clan_tag,
                        new_attacks,
                        style=str(settings.get("WAR_ATTACK_LOG_STYLE") or "card"),
                        timezone_name=str(settings.get("COC_TIMEZONE") or "America/New_York"),
                    )
                    await self._send_embed_with_optional_image(
                        channel,
                        embed,
                        WAR_BANNER_PATH,
                        "war-banner.png",
                        content=content,
                        allowed_mentions=allowed_mentions,
                    )
                except discord.HTTPException:
                    log.exception("Could not send CoC war notification to channel %s in guild %s.", channel_id, guild_id)
                    continue
                sent_any_notification = True
            await guild_config.WAR_START_TIME.set(war_data.get("startTime"))
            await guild_config.WAR_END_TIME.set(war_data.get("endTime"))
            await guild_config.LAST_API_PULL.set((datetime.now() - timedelta(hours=5)).isoformat())
            if sent_any_notification:
                await guild_config.LAST_NOTIFICATION_TIMESTAMP.set((datetime.now() - timedelta(hours=5)).isoformat())
            await guild_config.LAST_NOTIFICATION_STATE.set(fingerprint)
            await guild_config.LAST_WAR_ATTACKS.set(current_attack_keys)
            await guild_config.WAR_NOTIFICATION_EVENTS.set(sent_events)

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

        guild_config = self.config.guild(ctx.guild)
        timezone_name = str(await guild_config.COC_TIMEZONE() or "America/New_York")
        embed = self._build_war_embed(user_json, clan_key, timezone_name=timezone_name)
        await guild_config.WAR_START_TIME.set(user_json.get("startTime"))
        await guild_config.WAR_END_TIME.set(user_json.get("endTime"))
        await guild_config.LAST_API_PULL.set((datetime.now() - timedelta(hours=5)).isoformat())
        await guild_config.LAST_WAR_ID.set(self._war_id(user_json))
        await guild_config.LAST_WAR_ATTACKS.set(self._current_attack_keys(user_json, clan_key))
        await self._send_embed_with_optional_image(ctx, embed, WAR_BANNER_PATH, "war-banner.png")

    @command_coc.command(name="attacks", aliases=["attack", "attackstatus", "hits"])
    async def command_coc_attacks(self, ctx):
        """Show who has and has not attacked in the current war."""

        api_key = await self.config.COC_API_KEY()
        if not api_key:
            return await ctx.send(self._missing_api_key_message(ctx))

        clan_key = await self._get_clan_tag(ctx)
        if not clan_key:
            return await ctx.send(self._missing_clan_key_message(ctx))

        headers = self._api_headers(api_key)
        try:
            war_data, notice = await self._fetch_current_war(clan_key, headers)
        except aiohttp.ClientConnectionError as e:
            await ctx.send(f"Oops! Couldn't return results from COC api due to a connection error: {e}")
            return
        except Exception as e:
            await ctx.send(f"An unexpected error occurred: {e}")
            return

        if not war_data:
            return await ctx.send(notice)
        if notice:
            await ctx.send(notice)

        timezone_name = str(await self.config.guild(ctx.guild).COC_TIMEZONE() or "America/New_York")
        embed = self._build_war_attacks_embed(war_data, clan_key, timezone_name)
        await self._send_embed_with_optional_image(ctx, embed, WAR_BANNER_PATH, "war-banner.png")
        
    @commands.guild_only()
    @checks.mod_or_permissions(manage_channels=True)
    @command_coc.command(name="timezone", aliases=["settimezone", "tz"])
    async def command_coc_timezone(self, ctx, timezone_name: str = None):
        """Set the timezone used for this server's war schedules."""

        guild_config = self.config.guild(ctx.guild)
        if timezone_name is None:
            current = str(await guild_config.COC_TIMEZONE() or "America/New_York")
            return await ctx.send(
                f"War schedule timezone: **{current}**\n"
                f"Change it with `{ctx.clean_prefix}coc timezone America/New_York`."
            )

        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            return await ctx.send(
                "That timezone was not recognized. Use an IANA timezone such as "
                "`America/New_York`, `America/Chicago`, `Europe/London`, or `UTC`."
            )

        await guild_config.COC_TIMEZONE.set(timezone_name)
        await ctx.send(f"War schedules will now use **{timezone_name}** for this server.")

    @staticmethod
    def _valid_notification_events_message() -> str:
        return ", ".join(f"`{event}`" for event in WAR_NOTIFICATION_EVENTS)

    @staticmethod
    async def _reset_war_notification_state(guild_config) -> None:
        await guild_config.LAST_NOTIFICATION_STATE.set(None)
        await guild_config.LAST_WAR_ATTACKS.set([])
        await guild_config.LAST_WAR_ID.set(None)
        await guild_config.WAR_NOTIFICATION_EVENTS.set({})

    @checks.mod_or_permissions(manage_channels=True)
    @command_coc.group(
        name="notifications",
        aliases=["notify", "warnotification"],
        invoke_without_command=True,
    )
    async def command_coc_warnotification(self, ctx):
        """Toggle Clash of Clans war notifications for this server."""

        guild_config = self.config.guild(ctx.guild)
        enabled = await guild_config.COC_WAR_NOTIFICATIONS()
        if enabled:
            await guild_config.COC_WAR_NOTIFICATIONS.set(False)
            return await ctx.send(
                "Clash of Clans war notifications are now **OFF** for this server. "
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
        await self._reset_war_notification_state(guild_config)
        try:
            channel = ctx.guild.get_channel(int(coc_war_channel))
        except (TypeError, ValueError):
            channel = None
        channel_name = channel.mention if channel else f"`{coc_war_channel}`"
        await ctx.send(
            "Clash of Clans war notifications are now **ON**.\n"
            f"Clan: `{self._normalize_tag(clan_key)}`\n"
            f"Channel: {channel_name}\n"
            "I will check for war updates about every 5 minutes."
        )

    @checks.mod_or_permissions(manage_channels=True)
    @command_coc_warnotification.command(name="status")
    async def command_coc_warnotification_status(self, ctx):
        """Show Clash of Clans war notification settings."""

        settings = await self.config.guild(ctx.guild).all()
        channel_id = settings.get("COC_WAR_CHANNEL")
        role_id = settings.get("COC_WAR_MENTION_ROLE")
        try:
            channel = ctx.guild.get_channel(int(channel_id)) if channel_id else None
        except (TypeError, ValueError):
            channel = None
        try:
            role = ctx.guild.get_role(int(role_id)) if role_id else None
        except (TypeError, ValueError):
            role = None
        lines = [
            f"Global notifications: **{'ON' if settings.get('COC_WAR_NOTIFICATIONS') else 'OFF'}**",
            f"Channel: {channel.mention if channel else ('`' + str(channel_id) + '`' if channel_id else '**not set**')}",
            f"Mention role: {role.mention if role else ('`' + str(role_id) + '`' if role_id else '**not set**')}",
            f"Timezone: **{settings.get('COC_TIMEZONE') or 'America/New_York'}**",
            f"Preparation ending soon: **{settings.get('WAR_PREP_SOON_MINUTES', 5)} minutes** before battle day",
            f"War ending soon: **{settings.get('WAR_END_SOON_MINUTES', 60)} minutes** before war end",
            f"Attack log format: **{str(settings.get('WAR_ATTACK_LOG_STYLE') or 'card').upper()}**",
            "",
            "Events:",
        ]
        for event, label in WAR_NOTIFICATION_EVENTS.items():
            enabled = "ON" if self._event_enabled(settings, event) else "OFF"
            mention = "mention" if self._event_mention_enabled(settings, event) else "no mention"
            lines.append(f"- `{event}` {label}: **{enabled}**, {mention}")

        await ctx.send("\n".join(lines))

    @checks.mod_or_permissions(manage_channels=True)
    @command_coc_warnotification.command(
        name="attackstyle", aliases=["attack", "attackformat", "logstyle"]
    )
    async def command_coc_attackstyle(self, ctx, style: str = None):
        """Choose compact one-line attack updates or detailed cards."""

        guild_config = self.config.guild(ctx.guild)
        if style is None:
            current = str(await guild_config.WAR_ATTACK_LOG_STYLE() or "card").upper()
            return await ctx.send(f"War attack log format is **{current}**.")
        normalized = style.strip().lower()
        aliases = {
            "card": "card",
            "cards": "card",
            "compact": "compact",
            "line": "compact",
            "lines": "compact",
            "oneline": "compact",
        }
        selected = aliases.get(normalized)
        if selected is None:
            return await ctx.send("Choose `card` or `compact`.")
        await guild_config.WAR_ATTACK_LOG_STYLE.set(selected)
        await ctx.send(f"War attack log format is now **{selected.upper()}**.")

    @checks.mod_or_permissions(manage_channels=True)
    @command_coc_warnotification.command(name="role")
    async def command_coc_warnotification_role(self, ctx, role: discord.Role):
        """Set the role used by war notification mentions."""

        await self.config.guild(ctx.guild).COC_WAR_MENTION_ROLE.set(role.id)
        await ctx.send(f"War notification mention role set to {role.mention}.")

    @checks.mod_or_permissions(manage_channels=True)
    @command_coc_warnotification.command(name="clearrole")
    async def command_coc_warnotification_clearrole(self, ctx):
        """Clear the war notification mention role."""

        await self.config.guild(ctx.guild).COC_WAR_MENTION_ROLE.set(None)
        await ctx.send("War notification mention role cleared.")

    @checks.mod_or_permissions(manage_channels=True)
    @command_coc_warnotification.command(name="event")
    async def command_coc_warnotification_event(self, ctx, event: str, enabled: bool):
        """Toggle one war notification event."""

        event_key = self._notification_event_key(event)
        if event_key not in WAR_NOTIFICATION_EVENTS:
            return await ctx.send(
                "Unknown war notification event. Valid events: "
                f"{self._valid_notification_events_message()}."
            )

        guild_config = self.config.guild(ctx.guild)
        await getattr(guild_config, WAR_NOTIFICATION_SETTING_NAMES[event_key]).set(enabled)
        await self._reset_war_notification_state(guild_config)
        await ctx.send(
            f"{WAR_NOTIFICATION_EVENTS[event_key]} notifications are now "
            f"**{'ON' if enabled else 'OFF'}**."
        )

    @checks.mod_or_permissions(manage_channels=True)
    @command_coc_warnotification.command(name="mention")
    async def command_coc_warnotification_mention(self, ctx, event: str, enabled: bool):
        """Toggle role mentions for one war notification event."""

        event_key = self._notification_event_key(event)
        if event_key not in WAR_NOTIFICATION_EVENTS:
            return await ctx.send(
                "Unknown war notification event. Valid events: "
                f"{self._valid_notification_events_message()}."
            )

        guild_config = self.config.guild(ctx.guild)
        await getattr(guild_config, WAR_NOTIFICATION_MENTION_SETTING_NAMES[event_key]).set(enabled)
        await ctx.send(
            f"{WAR_NOTIFICATION_EVENTS[event_key]} role mentions are now "
            f"**{'ON' if enabled else 'OFF'}**."
        )

    @checks.mod_or_permissions(manage_channels=True)
    @command_coc_warnotification.command(name="prepsoonminutes")
    async def command_coc_warnotification_prepsoonminutes(self, ctx, minutes: int):
        """Set how soon before battle day the preparation warning sends."""

        if minutes < 1:
            return await ctx.send("Preparation ending soon must be at least 1 minute.")

        guild_config = self.config.guild(ctx.guild)
        await guild_config.WAR_PREP_SOON_MINUTES.set(minutes)
        await self._reset_war_notification_state(guild_config)
        await ctx.send(f"Preparation ending soon notifications will send {minutes} minutes before battle day.")

    @checks.mod_or_permissions(manage_channels=True)
    @command_coc_warnotification.command(name="endsoonminutes")
    async def command_coc_warnotification_endsoonminutes(self, ctx, minutes: int):
        """Set how soon before war end the ending warning sends."""

        if minutes < 1:
            return await ctx.send("War ending soon must be at least 1 minute.")

        guild_config = self.config.guild(ctx.guild)
        await guild_config.WAR_END_SOON_MINUTES.set(minutes)
        await self._reset_war_notification_state(guild_config)
        await ctx.send(f"War ending soon notifications will send {minutes} minutes before war end.")

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
            await self._reset_war_notification_state(guild_config)
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
        await self._reset_war_notification_state(guild_config)
        await ctx.send(f"War updates will be sent to {channel.mention}. Notifications are now on.")
