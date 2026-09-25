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
        self.assertIsNotNone(rlcs.get_command("recent"))
        self.assertIsNotNone(rlcs.get_command("results"))
        self.assertIsNotNone(rlcs.get_command("events"))
        self.assertIn("list", rlcs.get_command("events").aliases)
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
        for command in ("leagueadd", "leagues", "leaguerefresh", "leaguedisable", "leagueremove"):
            self.assertIsNotNone(settings.get_command(command))

    def test_tournament_provider_is_detected_from_url(self):
        provider, key = RocketLeague._tournament_source_from_url(
            "https://www.start.gg/tournament/example/details"
        )

        self.assertEqual(provider, "startgg")
        self.assertEqual(key, "tournament/example")

    def test_startgg_league_url_is_strictly_detected(self):
        self.assertEqual(
            RocketLeague._league_source_from_url("https://www.start.gg/league/rlcs-2026/events"),
            "league/rlcs-2026",
        )
        with self.assertRaises(ValueError):
            RocketLeague._league_source_from_url("http://www.start.gg/league/rlcs-2026")
        with self.assertRaises(ValueError):
            RocketLeague._league_source_from_url("https://www.start.gg/tournament/rlcs-2026")

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
        self.assertIsNotNone(rlcs.get_command("recent"))
        self.assertIsNotNone(rlcs.get_command("results"))
        self.assertIsNotNone(rlcs.get_command("events"))
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

    def test_startgg_mixed_phase_card_is_bounded_and_explicit(self):
        source = {
            "provider": "startgg",
            "start_at": 100,
            "end_at": 500,
            "registration_open": True,
            "registration_closes_at": 450,
            "registration_url": "https://www.start.gg/tournament/mixed",
            "cached_at": 200,
            "events": [
                {"name": "Finished qualifier", "state": 3, "start_at": 100, "registered_entrants": 8, "team_size": 3},
                {"name": "Live qualifier", "state": 2, "start_at": 200, "registered_entrants": 12, "team_size": 3},
                {"name": "Open qualifier", "state": 1, "start_at": 400, "registered_entrants": 4, "team_size": 3},
                {"name": "Fourth", "state": 1, "start_at": 450},
                {"name": "Overflow", "state": 1, "start_at": 500},
            ],
        }
        rendered = " ".join(RocketLeague._community_source_details(source, past=False))
        self.assertIn("Registration open", rendered)
        self.assertIn("Register on start.gg", rendered)
        self.assertIn("Mixed lifecycle states", rendered)
        self.assertIn("Active", rendered)
        self.assertIn("Completed", rendered)
        self.assertIn("And 1 more", rendered)
        self.assertIn("Last refreshed", rendered)

    def test_startgg_missing_fields_do_not_fabricate_registration(self):
        source = {
            "provider": "startgg",
            "events": [{"name": "Unknown", "state": None, "start_at": None}],
        }
        rendered = " ".join(RocketLeague._community_source_details(source, past=False))
        self.assertNotIn("Registration open", rendered)
        self.assertNotIn("Register on start.gg", rendered)
        self.assertIn("Status unavailable", rendered)

    def test_cross_provider_match_handles_reordered_region_name_and_phase_dates(self):
        blast = {"name": "RLCS Open 6 APAC 2026", "start_at": 1_776_998_400, "end_at": 1_777_171_200}
        startgg = {"name": "RLCS 2026 - APAC Open 6", "start_at": 1_776_480_000, "end_at": 1_777_171_200}
        unrelated = {"name": "RLCS Open 6 EU 2026", "start_at": 1_776_998_400}
        self.assertTrue(RocketLeague._confident_event_match(blast, startgg))
        self.assertFalse(RocketLeague._confident_event_match(blast, unrelated))

    def test_cross_provider_match_normalizes_region_aliases_without_changing_records(self):
        cases = (
            ("RLCS Open 6 EU 2026", "RLCS 2026 - Europe Open 6"),
            ("RLCS Open 6 SAM 2026", "RLCS 2026 - South America Open 6"),
            ("RLCS Open 6 NA 2026", "RLCS 2026 - North America Open 6"),
            ("RLCS Open 6 OCE 2026", "RLCS 2026 - Oceania Open 6"),
            ("RLCS Open 6 APAC 2026", "RLCS 2026 - Asia-Pacific Open 6"),
        )
        for abbreviated, expanded in cases:
            with self.subTest(region=abbreviated):
                first = {"name": abbreviated, "start_at": 1_800_000_000}
                second = {"name": expanded, "start_at": 1_800_086_400}
                self.assertTrue(RocketLeague._confident_event_match(first, second))
                self.assertEqual(first["name"], abbreviated)
                self.assertEqual(second["name"], expanded)

    def test_placement_summary_handles_tied_semifinalists(self):
        source = {"events": [{"name": "3v3", "standings": [
            {"placement": 1, "entrant_name": "Champions"},
            {"placement": 2, "entrant_name": "Runners-up"},
            {"placement": 3, "entrant_name": "Semi One"},
            {"placement": 3, "entrant_name": "Semi Two"},
        ]}]}
        rendered = " ".join(RocketLeague._placement_summary(source))
        self.assertIn("Champion: **Champions**", rendered)
        self.assertIn("Runner-up: **Runners-up**", rendered)
        self.assertIn("Semifinalists: Semi One, Semi Two", rendered)

    def test_placement_summary_does_not_infer_missing_standings(self):
        self.assertEqual(RocketLeague._placement_summary({"events": [{"name": "3v3"}]}), [])

    def test_results_embed_labels_provider_points_only_by_omission(self):
        source = {"name": "RLCS Test", "url": "https://www.start.gg/tournament/test", "cached_at": 1_800_000_000, "events": [{
            "name": "3v3", "standings": [{"placement": 1, "entrant_name": "Champions", "provider_points": 42, "record": {"wins": 8, "losses": 0}}]
        }]}
        embed = RocketLeague._results_embed(source)
        rendered = " ".join(field.value for field in embed.fields)
        self.assertIn("#1", rendered)
        self.assertNotIn("42", rendered)
        self.assertNotIn("goals", rendered.casefold())
        self.assertIn("match record 8-0", rendered)
        self.assertIn("Updated <t:1800000000:R>", rendered)
        self.assertNotIn("<t:", embed.footer.text)

    def test_failed_league_refresh_preserves_last_good_snapshot(self):
        cached = [{"id": 4, "key": "league/rlcs", "enabled": False, "tournaments": [{"fingerprint": "old"}]}]
        self.assertEqual(RocketLeague._merge_league_refreshes(cached, {}), cached)
        merged = RocketLeague._merge_league_refreshes(
            cached,
            {"league/rlcs": {"key": "league/rlcs", "enabled": True, "tournaments": [{"fingerprint": "new"}]}},
        )
        self.assertEqual(merged[0]["id"], 4)
        self.assertFalse(merged[0]["enabled"])
        self.assertEqual(merged[0]["tournaments"][0]["fingerprint"], "new")

    def test_failed_refresh_preserves_last_good_cached_record(self):
        cached = [{"id": 7, "provider": "startgg", "key": "tournament/example", "name": "Last good"}]
        self.assertEqual(RocketLeague._merge_tournament_refreshes(cached, {}), cached)

        refreshed = {
            ("startgg", "tournament/example"): {
                "provider": "startgg",
                "key": "tournament/example",
                "name": "Updated",
            }
        }
        merged = RocketLeague._merge_tournament_refreshes(cached, refreshed)
        self.assertEqual(merged[0]["name"], "Updated")
        self.assertEqual(merged[0]["id"], 7)

    def test_startgg_lifecycle_labels_cover_upcoming_active_and_completed(self):
        self.assertEqual(RocketLeague._startgg_event_status({"state": 1}, now=100)[0], "Upcoming")
        self.assertEqual(RocketLeague._startgg_event_status({"state": 2}, now=100)[0], "Active")
        self.assertEqual(RocketLeague._startgg_event_status({"state": 3}, now=100)[0], "Completed")
        self.assertEqual(
            RocketLeague._startgg_event_status({"state": None, "start_at": 200}, now=100)[0],
            "Scheduled",
        )

    def test_recent_empty_state_uses_configured_prefix(self):
        async def scenario():
            cog = RocketLeague.__new__(RocketLeague)
            cog.bot = SimpleNamespace()
            cog._recent_blast_tournaments = AsyncMock(return_value=[])
            ctx = SimpleNamespace(clean_prefix="sick!", send=AsyncMock())
            await cog._send_recent_events(ctx)
            return ctx.send.await_args.args[0]

        rendered = asyncio.run(scenario())
        self.assertIn("`sick!rlcs events`", rendered)

    def test_rlcs_card_places_location_directly_below_title(self):
        from rocketleague.blast import BlastTournament

        tournament = BlastTournament(
            "paris-major",
            "RLCS Paris Major",
            100,
            200,
            "Paris La Défense Arena, France",
        )
        embed = RocketLeague._blast_embed([tournament])
        lines = embed.fields[0].value.splitlines()
        self.assertEqual(lines[0], "📍 Paris La Défense Arena, France")
        self.assertTrue(lines[1].startswith("📅 "))
        self.assertIn("View on BLAST", lines[2])

    def test_cached_event_resolution_accepts_slug_and_short_id(self):
        async def scenario():
            from rocketleague.blast import BlastTournament

            tournament = BlastTournament("known-event", "Known", 100, 200, None)
            cog = RocketLeague.__new__(RocketLeague)
            cog._known_blast_tournaments = AsyncMock(return_value=[tournament])
            by_slug = await cog._resolve_cached_blast("known-event")
            by_id = await cog._resolve_cached_blast(cog._blast_short_id(tournament))
            return tournament, by_slug, by_id

        tournament, by_slug, by_id = asyncio.run(scenario())
        self.assertEqual(by_slug, tournament)
        self.assertEqual(by_id, tournament)

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
