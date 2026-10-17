import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from rocketleague.news import (
    ANNOUNCEMENTS_URL,
    extract_image,
    new_items,
    official_url,
    plain_text,
    recent_items,
)
from rocketleague.rocketleague import RocketLeague


class RocketLeagueNewsTests(unittest.TestCase):
    def test_public_updates_commands_are_registered(self):
        updates = RocketLeague.rocketleague.get_command("updates")
        self.assertIsNotNone(updates)
        self.assertIsNotNone(updates.get_command("list"))

    def test_settings_commands_are_registered(self):
        settings = RocketLeague.rocketleagueset
        self.assertIsNotNone(settings.get_command("gameupdates"))
        self.assertIsNotNone(settings.get_command("gameupdateschannel"))

    def test_recent_items_returns_bounded_oldest_to_newest_selection(self):
        items = [
            {"gid": "new", "date": 30},
            {"gid": "old", "date": 10},
            {"gid": "middle", "date": 20},
        ]
        self.assertEqual(
            [item["gid"] for item in recent_items(items, 2)],
            ["middle", "new"],
        )

    def test_new_items_are_unseen_and_oldest_first(self):
        items = [
            {"gid": "new", "date": 30},
            {"gid": "old", "date": 10},
            {"gid": "seen", "date": 20},
        ]
        self.assertEqual(
            [item["gid"] for item in new_items(items, ["seen"])],
            ["old", "new"],
        )

    def test_canonical_steam_post_is_preferred_over_article_links(self):
        item = {
            "gid": "1844751498216898",
            "contents": (
                "[url=\"https://www.rocketleague.com/news/known-issues-in-rocket-league\"]"
                "Known Issues[/url]"
            ),
            "url": "https://steamstore-a.akamaihd.net/news/externalpost/1",
        }
        self.assertEqual(
            official_url(item),
            "https://steamcommunity.com/games/252950/announcements/detail/1844751498216898",
        )

    def test_missing_article_uses_feed_item_then_announcements(self):
        self.assertEqual(official_url({"url": "https://example.com/post"}), "https://example.com/post")
        self.assertEqual(official_url({}), ANNOUNCEMENTS_URL)

    def test_compact_steam_blocks_keep_paragraphs_and_bullets(self):
        source = (
            "[p]Season 24 launches![/p]"
            "[p align=\"start\"]NEW THIS SEASON[/p]"
            "[list][*][p]Honor Duels[/p][/*][*][p]New Mode[/p][/*][/list]"
        )
        self.assertEqual(
            plain_text(source),
            "Season 24 launches!\nNEW THIS SEASON\n• Honor Duels\n• New Mode",
        )

    def test_content_is_cleaned_and_bounded(self):
        text = plain_text("[h1]Heading[/h1][*]One &amp; two " + "x" * 100, limit=40)
        self.assertIn("Heading", text)
        self.assertIn("One & two", text)
        self.assertLessEqual(len(text), 40)
        self.assertTrue(text.endswith("..."))

    def test_steam_clan_image_placeholder_is_normalized(self):
        self.assertEqual(
            extract_image("[img]{STEAM_CLAN_IMAGE}/123/hero.jpg[/img]"),
            "https://clan.steamstatic.com/images/123/hero.jpg",
        )

    def test_poll_posts_unseen_items_oldest_first_and_persists_each(self):
        class Value:
            def __init__(self):
                self.set = AsyncMock()

        values = SimpleNamespace(
            all=AsyncMock(return_value={
                "game_updates_enabled": True,
                "game_updates_channel_id": 10,
                "game_updates_last_poll": 0,
                "game_updates_posted": ["seen"],
            }),
            game_updates_last_poll=Value(),
            game_updates_posted=Value(),
            game_updates_last_success=Value(),
        )
        channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(id=1, get_channel=lambda channel_id: channel)
        client = SimpleNamespace(fetch=AsyncMock(return_value=[
            {"gid": "new", "date": 30, "title": "New"},
            {"gid": "old", "date": 10, "title": "Old"},
            {"gid": "seen", "date": 20, "title": "Seen"},
        ]))
        cog = RocketLeague.__new__(RocketLeague)
        cog.config = SimpleNamespace(guild=lambda selected: values)
        cog._game_update_locks = {}
        cog.get_news_client = AsyncMock(return_value=client)

        posted = asyncio.run(cog._poll_game_updates(guild))

        self.assertEqual(posted, 2)
        self.assertEqual(
            [call.kwargs["embed"].title for call in channel.send.await_args_list],
            ["Old", "New"],
        )
        self.assertEqual(
            values.game_updates_posted.set.await_args_list[-1].args[0],
            ["seen", "old", "new"],
        )
        self.assertEqual(values.game_updates_posted.set.await_count, 2)

    def test_positional_updates_shows_requested_full_card(self):
        class Typing:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        ctx = SimpleNamespace(typing=lambda: Typing(), send=AsyncMock())
        client = SimpleNamespace(fetch=AsyncMock(return_value=[
            {"gid": "new", "date": 30, "title": "Newest"},
            {"gid": "old", "date": 10, "title": "Oldest"},
            {"gid": "middle", "date": 20, "title": "Second"},
        ]))
        cog = RocketLeague.__new__(RocketLeague)
        cog.get_news_client = AsyncMock(return_value=client)

        asyncio.run(RocketLeague.rocketleague_updates.callback(cog, ctx, 2))

        self.assertEqual(ctx.send.await_args.kwargs["embed"].title, "Second")

    def test_start_with_count_posts_recent_then_seeds_and_enables(self):
        class Value:
            def __init__(self):
                self.set = AsyncMock()

        values = SimpleNamespace(
            all=AsyncMock(return_value={
                "game_updates_channel_id": 10,
                "game_updates_posted": [],
            }),
            game_updates_last_poll=Value(),
            game_updates_posted=Value(),
            game_updates_last_success=Value(),
            game_updates_enabled=Value(),
        )
        channel = SimpleNamespace(mention="#updates", send=AsyncMock())
        guild = SimpleNamespace(id=1, get_channel=lambda channel_id: channel)
        client = SimpleNamespace(fetch=AsyncMock(return_value=[
            {"gid": "new", "date": 30, "title": "New"},
            {"gid": "old", "date": 10, "title": "Old"},
            {"gid": "middle", "date": 20, "title": "Middle"},
        ]))

        class Typing:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        ctx = SimpleNamespace(
            guild=guild,
            clean_prefix="!",
            typing=lambda: Typing(),
            send=AsyncMock(),
        )
        cog = RocketLeague.__new__(RocketLeague)
        cog.config = SimpleNamespace(guild=lambda selected: values)
        cog.get_news_client = AsyncMock(return_value=client)

        asyncio.run(
            RocketLeague.rocketleagueset_gameupdates.callback(cog, ctx, "start", 2)
        )

        self.assertEqual(
            [call.kwargs["embed"].title for call in channel.send.await_args_list],
            ["Middle", "New"],
        )
        values.game_updates_enabled.set.assert_awaited_once_with(True)
        self.assertEqual(
            values.game_updates_posted.set.await_args.args[0],
            ["new", "old", "middle"],
        )

    def test_embed_is_static_for_instance_polling(self):
        cog = RocketLeague.__new__(RocketLeague)
        self.assertEqual(cog._game_update_embed({"title": "Update"}).title, "Update")

    def test_embed_uses_official_content(self):
        embed = RocketLeague._game_update_embed(
            {
                "gid": "1",
                "title": "Patch Notes v2.64",
                "date": 1_800_000_000,
                "contents": (
                    "[url=https://www.rocketleague.com/news/patch-notes-v2-64]Details[/url]"
                    "[img]{STEAM_CLAN_IMAGE}/123/hero.jpg[/img]"
                ),
            }
        )
        self.assertEqual(embed.title, "Patch Notes v2.64")
        self.assertEqual(
            embed.url,
            "https://steamcommunity.com/games/252950/announcements/detail/1",
        )
        self.assertEqual(embed.image.url, "https://clan.steamstatic.com/images/123/hero.jpg")
        self.assertIn("Steam Community", embed.footer.text)
        self.assertIn("Official Steam Community", embed.author.name)


if __name__ == "__main__":
    unittest.main()
