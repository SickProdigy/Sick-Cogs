import unittest

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


if __name__ == "__main__":
    unittest.main()
