import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from botupdates.botupdates import BotUpdates
from botupdates.collector import (
    CommitCollectionError,
    GiteaCommitClient,
    digest_slot,
    digest_text,
    normalize_commit,
)


def commit(sha, message):
    return {
        "sha": sha,
        "message": message,
        "url": f"https://gitea.rcs1.top/sickprodigy/Sick-Cogs/commit/{sha}",
    }


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, **kwargs):
        return self.payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class CollectorHelperTests(unittest.IsolatedAsyncioTestCase):
    def test_digest_slots_are_first_and_fifteenth_utc(self):
        utc = datetime.timezone.utc
        self.assertEqual(digest_slot(datetime.datetime(2026, 3, 1, tzinfo=utc)), "2026-03-01")
        self.assertEqual(digest_slot(datetime.datetime(2026, 3, 14, tzinfo=utc)), "2026-03-01")
        self.assertEqual(digest_slot(datetime.datetime(2026, 3, 15, tzinfo=utc)), "2026-03-15")

    def test_normalizes_commit_and_builds_chronological_text(self):
        raw = {
            "sha": "new",
            "html_url": "https://example.com/new",
            "commit": {"message": "New change\n\nDetails"},
        }
        self.assertEqual(normalize_commit(raw)["message"], "New change")
        text = digest_text([commit("new", "Newest"), commit("old", "Oldest")])
        self.assertLess(text.index("Oldest"), text.index("Newest"))
        self.assertIsNone(normalize_commit({"sha": "bad"}))

    async def test_client_collects_newest_to_cursor(self):
        payload = [
            {"sha": "new", "html_url": "https://example.com/new", "commit": {"message": "New"}},
            {"sha": "middle", "html_url": "https://example.com/middle", "commit": {"message": "Middle"}},
            {"sha": "old", "html_url": "https://example.com/old", "commit": {"message": "Old"}},
        ]
        session = _Session([_Response(payload)])
        commits = await GiteaCommitClient(session).since("old")
        self.assertEqual([item["sha"] for item in commits], ["new", "middle"])
        self.assertEqual(session.calls[0][1]["params"]["sha"], "main")

    async def test_client_rejects_outages_malformed_data_and_missing_cursor(self):
        with self.assertRaises(CommitCollectionError):
            await GiteaCommitClient(_Session([_Response({}, status=429)])).latest()
        with self.assertRaises(CommitCollectionError):
            await GiteaCommitClient(_Session([_Response({})])).latest()
        payload = [{"sha": "new", "html_url": "https://example.com/new", "commit": {"message": "New"}}]
        with self.assertRaises(CommitCollectionError):
            await GiteaCommitClient(_Session([_Response(payload)])).since("missing")


class _Value:
    def __init__(self, value):
        self.value = value

    async def __call__(self):
        return self.value

    async def set(self, value):
        self.value = value


class _Config:
    def __init__(self):
        self.active_draft_id = _Value(0)
        self.drafts = _Value({})
        self.next_draft_id = _Value(1)
        self.announced_source_keys = _Value([])
        self.collector_enabled = _Value(True)
        self.collector_last_sha = _Value("old")
        self.collector_last_slot = _Value("2026-02-15")
        self.collector_last_check_at = _Value(0)
        self.collector_last_error = _Value("")
        self.review_channel_id = _Value(10)


class ScheduledDraftTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, config):
        cog = BotUpdates.__new__(BotUpdates)
        cog.config = config
        cog._collector_lock = __import__("asyncio").Lock()
        channel = SimpleNamespace(send=AsyncMock())
        cog.bot = SimpleNamespace(get_channel=lambda channel_id: channel)
        client = SimpleNamespace(
            since=AsyncMock(return_value=[commit("new", "New feature"), commit("mid", "Bug fix")])
        )
        cog._commit_client = AsyncMock(return_value=client)
        return cog, channel, client

    async def test_scheduled_collection_creates_review_only_draft_once(self):
        config = _Config()
        cog, channel, client = self.make_cog(config)
        now = datetime.datetime(2026, 3, 1, 1, tzinfo=datetime.timezone.utc)

        status, draft = await cog.collect_project_digest(now=now)

        self.assertEqual(status, "created")
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["kind"], "digest")
        self.assertEqual(len(draft["sources"]), 2)
        self.assertEqual(config.collector_last_sha.value, "new")
        self.assertEqual(config.collector_last_slot.value, "2026-03-01")
        channel.send.assert_awaited_once()

        restarted, _, restarted_client = self.make_cog(config)
        self.assertEqual((await restarted.collect_project_digest(now=now))[0], "not-due")
        restarted_client.since.assert_not_awaited()

    async def test_unfinished_active_draft_defers_collection(self):
        config = _Config()
        config.active_draft_id.value = 7
        config.drafts.value = {"7": {"id": 7, "status": "approved"}}
        cog, channel, client = self.make_cog(config)

        status, _ = await cog.collect_project_digest(
            now=datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc)
        )

        self.assertEqual(status, "draft-active")
        client.since.assert_not_awaited()
        channel.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
