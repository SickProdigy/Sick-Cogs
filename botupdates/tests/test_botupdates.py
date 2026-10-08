import unittest
from types import SimpleNamespace

import discord
from redbot.core import commands

from botupdates.botupdates import BotUpdates


class Value:
    def __init__(self, value):
        self.value = value

    async def __call__(self):
        return self.value

    async def set(self, value):
        self.value = value


class Config:
    def __init__(self, draft, guilds=None):
        self.review_channel_id = Value(0)
        self.active_draft_id = Value(draft["id"])
        self.drafts = Value({str(draft["id"]): draft})
        self.announced_source_keys = Value([])
        self.editor_user_ids = Value([])
        self.editor_role_ids = Value([])
        self.publisher_user_ids = Value([])
        self._guilds = guilds or {}

    async def all_guilds(self):
        return self._guilds


class Channel:
    def __init__(self):
        self.messages = []

    async def send(self, **kwargs):
        self.messages.append(kwargs)


class Guild:
    def __init__(self, guild_id, channel, role=None):
        self.id = guild_id
        self.channel = channel
        self.role = role

    def get_channel(self, channel_id):
        return self.channel if channel_id == 10 else None

    def get_role(self, role_id):
        return self.role if self.role and role_id == self.role.id else None


class Bot:
    def __init__(self, guilds=None, owner=True):
        self.guilds = {guild.id: guild for guild in guilds or []}
        self.owner = owner

    async def is_owner(self, user):
        return self.owner

    def get_guild(self, guild_id):
        return self.guilds.get(guild_id)

    def get_channel(self, channel_id):
        return None


class Context:
    def __init__(self):
        self.author = SimpleNamespace(id=50, roles=[])
        self.messages = []

    async def send(self, content=None, **kwargs):
        self.messages.append((content, kwargs))


def approved_draft(kind="digest", sources=True):
    return {
        "id": 1,
        "kind": kind,
        "title": "What's new",
        "text": "Human-reviewed update.",
        "status": "approved",
        "author_id": 50,
        "created_at": 1,
        "updated_at": 1,
        "sources": (
            [{
                "kind": "project",
                "label": "Sick-Cogs",
                "url": "https://example.com/commit/abc",
                "reference": "abc",
                "key": "project:https://example.com/commit/abc@abc",
            }]
            if sources
            else []
        ),
        "proposals": [],
        "history": [],
        "approved_by": 50,
        "approved_at": 2,
        "delivered_guild_ids": [],
    }


class BotUpdatesCommandTests(unittest.IsolatedAsyncioTestCase):
    def cog(self, draft, guilds=None, owner=True, guild_config=None):
        cog = BotUpdates.__new__(BotUpdates)
        cog.bot = Bot(guilds, owner)
        cog.config = Config(draft, guild_config)
        return cog

    async def test_editor_role_grants_review_access(self):
        draft = approved_draft()
        cog = self.cog(draft, owner=False)
        cog.config.editor_role_ids.value = [99]
        member = SimpleNamespace(id=51, roles=[SimpleNamespace(id=99)])
        self.assertTrue(await cog._is_editor(member))

    async def test_publish_filters_modes_and_records_source(self):
        major_channel = Channel()
        digest_channel = Channel()
        major_guild = Guild(1, major_channel)
        digest_guild = Guild(2, digest_channel)
        configs = {
            1: {
                "enabled": True,
                "channel_id": 10,
                "delivery_mode": "major-only",
                "mention_role_id": 0,
            },
            2: {
                "enabled": True,
                "channel_id": 10,
                "delivery_mode": "twice-monthly",
                "mention_role_id": 0,
            },
        }
        draft = approved_draft("digest")
        cog = self.cog(draft, [major_guild, digest_guild], guild_config=configs)
        ctx = Context()

        await BotUpdates.draft_publish.callback(cog, ctx)

        self.assertEqual(major_channel.messages, [])
        self.assertEqual(len(digest_channel.messages), 1)
        saved = cog.config.drafts.value["1"]
        self.assertEqual(saved["status"], "published")
        self.assertEqual(saved["delivered_guild_ids"], [2])
        self.assertEqual(
            cog.config.announced_source_keys.value,
            ["project:https://example.com/commit/abc@abc"],
        )

    async def test_approve_requires_a_source_reference(self):
        draft = approved_draft(sources=False)
        draft["status"] = "draft"
        cog = self.cog(draft)
        ctx = Context()

        with self.assertRaises(commands.UserFeedbackCheckFailure):
            await BotUpdates.draft_approve.callback(cog, ctx)


if __name__ == "__main__":
    unittest.main()
