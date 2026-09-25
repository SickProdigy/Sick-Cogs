import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from rocketleague.api import StartGGError
from rocketleague.clips import ClipProviders, ClipSourceError

from rocketleague.rocketleague import RocketLeague


class RocketLeagueHelpStructureTests(unittest.TestCase):
    def test_canonical_group_exposes_complete_rlcs_surface(self):
        rocketleague = RocketLeague.rocketleague
        self.assertIn("rl", rocketleague.aliases)
        rlcs = rocketleague.get_command("rlcs")

        self.assertIsNotNone(rlcs)
        self.assertIsNotNone(rlcs.get_command("upcoming"))
        self.assertIsNotNone(rlcs.get_command("event"))
        self.assertIsNotNone(RocketLeague.rocketleague.get_command("clips"))

    def test_public_clips_help_explains_the_status_card(self):
        help_text = RocketLeague.rocketleague_clips.help
        self.assertIn("posting channel", help_text)
        self.assertIn("rocketleagueset clips", help_text)

    def test_settings_group_advertises_its_short_alias(self):
        self.assertIn("rlset", RocketLeague.rocketleagueset.aliases)
        self.assertIn("rlset", RocketLeague.rocketleagueset.help)

    def test_community_tournament_commands_are_registered(self):
        tournaments = RocketLeague.rocketleague.get_command("tournaments")

        self.assertIsNotNone(tournaments)
        self.assertIn("tourney", tournaments.aliases)
        self.assertIn("tourneys", tournaments.aliases)
        self.assertIsNotNone(tournaments.get_command("upcoming"))
        self.assertIsNotNone(tournaments.get_command("recent"))

        settings = RocketLeague.rocketleagueset
        self.assertIsNotNone(settings.get_command("tournamentadd"))
        self.assertIsNotNone(settings.get_command("tournamentremove"))
        self.assertIsNotNone(settings.get_command("tournamentrefresh"))
        self.assertIsNotNone(settings.get_command("tournaments"))
        self.assertIsNone(settings.get_command("startgg"))

    def test_tournament_provider_is_detected_from_url(self):
        provider, key = RocketLeague._tournament_source_from_url(
            "https://www.start.gg/tournament/example/details"
        )

        self.assertEqual(provider, "startgg")
        self.assertEqual(key, "tournament/example")

    def test_challonge_url_is_detected(self):
        provider, key = RocketLeague._tournament_source_from_url(
            "https://challonge.com/example"
        )

        self.assertEqual(provider, "challonge")
        self.assertEqual(key, "example")

    def test_challonge_subdomain_url_uses_provider_identifier_format(self):
        provider, key = RocketLeague._tournament_source_from_url(
            "https://community.challonge.com/weekly"
        )

        self.assertEqual(provider, "challonge")
        self.assertEqual(key, "community-weekly")

    def test_short_rlcs_group_retains_compatibility_surface(self):
        rlcs = RocketLeague.rlcs

        self.assertNotIn("rl", rlcs.aliases)
        self.assertIsNotNone(rlcs.get_command("upcoming"))
        self.assertIsNotNone(rlcs.get_command("event"))

    def test_configuration_help_is_labeled_for_administrators(self):
        self.assertIn("Admin:", RocketLeague.rlcsset.help)

    def test_bare_canonical_group_sends_user_card(self):
        import asyncio
        from unittest.mock import AsyncMock

        cog = RocketLeague.__new__(RocketLeague)
        ctx = type("Context", (), {"clean_prefix": "!", "send": AsyncMock()})()
        asyncio.run(RocketLeague.rocketleague.callback(cog, ctx))

        embed = ctx.send.await_args.kwargs["embed"]
        rendered = " ".join(
            [embed.title, embed.description]
            + [field.name + " " + field.value for field in embed.fields]
        )
        self.assertIn("!rocketleague rlcs", rendered)
        self.assertIn("!rocketleague clips", rendered)
        self.assertNotIn("rlcsset", rendered)

    def test_full_group_help_mentions_administrator_commands(self):
        help_text = RocketLeague.rocketleague.help
        for command in ("rlcsset channel", "rlcsset disable", "rlcsset status", "rlcsset postnow", "rlcsset refresh"):
            self.assertIn(command, help_text)

    def test_community_card_renders_rich_cached_details(self):
        details = RocketLeague._community_source_details(
            {
                "start_at": 1_800_000_000,
                "end_at": 1_800_086_400,
                "team_size": 3,
                "registered_entrants": 4,
                "entrant_capacity": 16,
                "entrant_label": "teams",
                "registration_open": True,
                "location": "Online",
                "prize": "100 credits",
                "description": "A community event.",
            },
            past=False,
        )
        rendered = " ".join(details)
        self.assertIn("3v3", rendered)
        self.assertIn("4/16 teams", rendered)
        self.assertIn("Registration open", rendered)
        self.assertIn("Prize: 100 credits", rendered)
        self.assertIn("Online", rendered)

    def test_community_card_uses_neutral_challonge_entrant_label(self):
        details = RocketLeague._community_source_details(
            {"registered_entrants": 8, "entrant_label": "entrants"},
            past=True,
        )
        self.assertIn("8 entrants", " ".join(details))

    def test_missing_provider_credentials_carry_command_metadata(self):
        async def scenario():
            bot = SimpleNamespace(get_shared_api_tokens=AsyncMock(return_value={}))
            cog = RocketLeague.__new__(RocketLeague)
            cog.bot = bot
            cog._challonge_access_token = None
            cog._challonge_token_expires_at = 0.0
            cog._challonge_token_lock = asyncio.Lock()

            with self.assertRaises(StartGGError) as startgg:
                await cog._fetch_startgg_source("tournament/example")
            with self.assertRaises(StartGGError) as challonge:
                await cog._challonge_access_token_for_request()

            providers = ClipProviders(bot, None)
            with self.assertRaises(ClipSourceError) as twitch:
                await providers._twitch_access_token()
            with self.assertRaises(ClipSourceError) as youtube:
                await providers._youtube_key()

            return [
                startgg.exception.setup_command,
                challonge.exception.setup_command,
                twitch.exception.setup_command,
                youtube.exception.setup_command,
            ]

        commands = asyncio.run(scenario())
        self.assertEqual(
            commands,
            [
                "set api startgg token,YOUR_TOKEN",
                "set api challonge client_id,YOUR_ID client_secret,YOUR_SECRET",
                "set api twitch client_id,YOUR_ID client_secret,YOUR_SECRET",
                "set api youtube api_key,YOUR_API_KEY",
            ],
        )

    def test_provider_guidance_uses_invoking_text_prefix(self):
        async def scenario(prefix):
            cog = RocketLeague.__new__(RocketLeague)
            cog.bot = SimpleNamespace()
            ctx = SimpleNamespace(clean_prefix=prefix)
            error = StartGGError(
                "Credentials are missing.",
                setup_command="set api startgg token,YOUR_TOKEN",
            )
            return await cog._provider_error_message(ctx, error)

        for prefix in ("!", "-", "sick!"):
            with self.subTest(prefix=prefix):
                rendered = asyncio.run(scenario(prefix))
                self.assertIn(f"`{prefix}set api startgg token,YOUR_TOKEN`", rendered)

    def test_mention_invocation_prefers_configured_text_prefix(self):
        async def scenario():
            bot = SimpleNamespace(
                get_valid_prefixes=AsyncMock(return_value=["<@123> ", "!!"])
            )
            cog = RocketLeague.__new__(RocketLeague)
            cog.bot = bot
            ctx = SimpleNamespace(clean_prefix="@RocketBot ", guild=object())
            error = StartGGError(
                "Credentials are missing.",
                setup_command="set api startgg token,YOUR_TOKEN",
            )
            return await cog._provider_error_message(ctx, error)

        rendered = asyncio.run(scenario())
        self.assertIn("`!!set api startgg token,YOUR_TOKEN`", rendered)
        self.assertNotIn("@RocketBot", rendered)


if __name__ == "__main__":
    unittest.main()
