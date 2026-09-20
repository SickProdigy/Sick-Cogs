import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nsfw.core import Core, MIN_AUTOPOST_MINUTES, SEEN_LIMIT
from nsfw.nsfw import Nsfw


class FakeValue:
    def __init__(self, value):
        self.value = value

    async def __call__(self):
        return self.value

    async def set(self, value):
        self.value = value

    async def clear(self):
        self.value = {}


class FakeConfig:
    def __init__(self, feed):
        self.value = FakeValue(feed)

    def guild(self, guild):
        return SimpleNamespace(autopost=self.value)


class NsfwAutopostTests(unittest.IsolatedAsyncioTestCase):
    def test_new_feed_is_persistent_single_record_and_delayed(self):
        with patch("nsfw.core.time.time", return_value=1000):
            action, feed = Core._reconcile_autopost({}, 42, "Boobs", 60)
        self.assertEqual(action, "added")
        self.assertEqual(feed["channel_id"], 42)
        self.assertEqual(feed["source"], "boobs")
        self.assertEqual(feed["interval"], 3600)
        self.assertEqual(feed["provider"], "configured-reddit")
        self.assertEqual(feed["next_post"], 4600)
        self.assertTrue(feed["enabled"])

    def test_random_category_persists_mixed_provider(self):
        with patch("nsfw.core.time.time", return_value=1000):
            _, feed = Core._reconcile_autopost({}, 42, "random", 60)
        self.assertEqual(feed["provider"], "mixed")

    async def test_random_post_names_category_and_copyable_command(self):
        async def get_imgs(categories):
            return "https://example.com/image.jpg", "example"

        async def use_reddit_api():
            return False

        fake = SimpleNamespace(
            autopost_sources=lambda: {"boobs": ["example"]},
            _get_imgs=get_imgs,
            _safe_url=Core._safe_url,
            config=SimpleNamespace(use_reddit_api=use_reddit_api),
        )
        with patch("nsfw.core.choice", return_value="boobs"):
            url, embed = await Core._fetch_autopost(fake, "random", set(), prefix="!")
        self.assertEqual(url, "https://example.com/image.jpg")
        self.assertIn("boobs", embed.title)
        self.assertIn("`!boobs`", embed.description)

    async def test_incompatible_gif_host_uses_direct_link_payload(self):
        async def get_imgs(categories):
            return "https://redgifs.com/watch/example", "example"

        async def use_reddit_api():
            return False

        fake = SimpleNamespace(
            autopost_sources=lambda: {"boobs": ["example"]},
            _get_imgs=get_imgs,
            _safe_url=Core._safe_url,
            config=SimpleNamespace(use_reddit_api=use_reddit_api),
        )
        url, payload = await Core._fetch_autopost(fake, "boobs", set(), prefix="!")
        self.assertEqual(url, "https://redgifs.com/watch/example")
        self.assertIsInstance(payload, str)
        self.assertIn("https://redgifs.com/watch/example", payload)
        self.assertIn("`!boobs`", payload)

    def test_nekobot_category_persists_its_provider(self):
        with patch("nsfw.core.time.time", return_value=1000):
            _, feed = Core._reconcile_autopost({}, 42, "hentai", 60)
        self.assertEqual(feed["provider"], "nekobot")

    def test_reconfiguration_replaces_the_only_feed(self):
        old = {"channel_id": 1, "source": "ass", "interval": 3600, "enabled": True}
        with patch("nsfw.core.time.time", return_value=2000):
            action, feed = Core._reconcile_autopost(old, 2, "gonewild", 120)
        self.assertEqual(action, "updated")
        self.assertEqual((feed["channel_id"], feed["source"]), (2, "gonewild"))
        self.assertNotIn("ass", feed.values())

    def test_identical_configuration_does_not_reschedule(self):
        feed = {"channel_id": 1, "source": "ass", "interval": 3600,
                "next_post": 9999, "enabled": True}
        action, result = Core._reconcile_autopost(feed, 1, "ASS", 60)
        self.assertEqual(action, "unchanged")
        self.assertIs(result, feed)
        self.assertEqual(result["next_post"], 9999)

    def test_destination_must_be_nsfw(self):
        class FakeTextChannel:
            mention = "#general"
            def is_nsfw(self): return False
        with patch("nsfw.core.discord.TextChannel", FakeTextChannel):
            error = Core._autopost_destination_error(SimpleNamespace(me=object()), FakeTextChannel())
        self.assertIn("age-restricted", error)

    def test_destination_requires_all_send_permissions(self):
        class FakeTextChannel:
            mention = "#adult"
            def is_nsfw(self): return True
            def permissions_for(self, member):
                return SimpleNamespace(view_channel=True, send_messages=False, embed_links=False)
        with patch("nsfw.core.discord.TextChannel", FakeTextChannel):
            error = Core._autopost_destination_error(SimpleNamespace(me=object()), FakeTextChannel())
        self.assertIn("send messages", error)
        self.assertIn("embed links", error)

    def test_recent_history_is_bounded_and_suppresses_known_url(self):
        seen = [f"https://example.com/{i}" for i in range(SEEN_LIMIT)]
        result = Core._remember_seen(seen, "https://example.com/new")
        self.assertEqual(len(result), SEEN_LIMIT)
        self.assertNotIn("https://example.com/0", result)
        self.assertIn("https://example.com/new", result)

    def test_retry_uses_quarter_interval_with_five_minute_floor(self):
        self.assertEqual(Core._retry_at(1000, 3600), 1900)
        self.assertEqual(Core._retry_at(1000, 600), 1300)

    def test_display_prefix_ignores_mention_prefixes(self):
        prefixes = ["<@123> ", "<@!123> ", "!"]
        self.assertEqual(Core._display_prefix(prefixes), "!")

    def test_minimum_interval_is_sensible(self):
        self.assertEqual(MIN_AUTOPOST_MINUTES, 30)

    async def test_bare_autopost_opens_help_instead_of_configuring(self):
        shown = []

        async def send_help(command):
            shown.append(command)

        ctx = SimpleNamespace(command=object(), send_help=send_help)
        await Nsfw.nsfwset_autopost.callback(
            SimpleNamespace(), ctx, channel=None, source=None, minutes=None
        )
        self.assertEqual(shown, [ctx.command])

    async def test_status_reports_the_saved_feed(self):
        feed = {"channel_id": 7, "source": "boobs", "interval": 3600,
                "next_post": 5000, "enabled": True, "last_error": ""}
        sent = []
        ctx = SimpleNamespace(
            guild=SimpleNamespace(get_channel=lambda channel_id: object()),
            send=lambda message: _capture(sent, message),
        )
        cog = SimpleNamespace(
            config=FakeConfig(feed),
            _autopost_destination_error=lambda guild, channel: "",
        )
        await Nsfw.nsfwset_autopost_status.callback(cog, ctx)
        self.assertIn("boobs", sent[0])
        self.assertIn("every 60m", sent[0])
        self.assertIn("ON", sent[0])

    async def test_preview_does_not_change_the_saved_feed(self):
        feed = {"channel_id": 7, "source": "random", "interval": 7200,
                "next_post": 9000, "enabled": True}
        config = FakeConfig(feed.copy())
        sent = []

        async def prefixes(guild):
            return ["!"]

        async def fetch(source, seen, prefix):
            return "https://example.com/image.jpg", object()

        async def send(*args, **kwargs):
            sent.append((args, kwargs))

        async def send_autopost(channel, payload):
            await channel.send(embed=payload)

        cog = SimpleNamespace(
            config=config,
            bot=SimpleNamespace(get_valid_prefixes=prefixes),
            _autopost_destination_error=lambda guild, channel: "",
            _fetch_autopost=fetch,
            _display_prefix=Core._display_prefix,
            _send_autopost=send_autopost,
        )
        ctx = SimpleNamespace(guild=object(), channel=object(), send=send)
        await Nsfw.nsfwset_autopost_preview.callback(cog, ctx)
        self.assertEqual(config.value.value, feed)
        self.assertIn("embed", sent[0][1])

    async def test_off_clears_the_single_feed(self):
        config = FakeConfig({"channel_id": 7, "source": "boobs"})
        sent = []
        ctx = SimpleNamespace(guild=object(), send=lambda message: _capture(sent, message))
        await Nsfw.nsfwset_autopost_off.callback(SimpleNamespace(config=config), ctx)
        self.assertEqual(config.value.value, {})
        self.assertIn("off", sent[0])


async def _capture(target, message):
    target.append(message)
