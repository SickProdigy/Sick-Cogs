import asyncio
import copy
import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from redbot.core import commands

from botupdates.botupdates import BotUpdates
from botupdates.collector import (
    CommitCollectionError,
    GiteaCommitClient,
    GitHubCommitClient,
    upstream_source,
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
        return copy.deepcopy(self.value)

    async def set(self, value):
        self.value = value


class _Config:
    def __init__(self):
        self.upstream_sources = _Value({})
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

    def all(self):
        return _Transaction(self)


class _Transaction:
    def __init__(self, config):
        self.config = config

    async def __aenter__(self):
        self.state = {key: copy.deepcopy(value.value) for key, value in vars(self.config).items()}
        return self.state

    async def __aexit__(self, exc_type, *args):
        if exc_type is None:
            for key, value in self.state.items():
                getattr(self.config, key).value = value
        return False


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


def raw_commit(sha):
    return {"sha": sha, "html_url": "https://example.org/untrusted", "commit": {"message": "Change"}}


def configured_source(path="example"):
    _, source = upstream_source("example", "owner/repo", "main", path)
    source["last_sha"] = "a" * 40
    return source


class GitHubTests(unittest.IsolatedAsyncioTestCase):
    def test_validation(self):
        name, source = upstream_source("Example", "Owner/Repo", "feature/test", "example/")
        self.assertEqual(name, "example")
        self.assertEqual(source["repository"], "owner/repo")
        self.assertEqual(source["path"], "example")
        for args in [("bad name", "owner/repo", "main", ""),
                     ("ok", "https://github.com/owner/repo", "main", ""),
                     ("ok", "owner/repo", "", ""),
                     ("ok", "owner/repo", "main", "../etc"),
                     ("ok", "owner/repo", "main", "/etc"),
                     ("ok", "owner/repo", "main", "bad\npath")]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                upstream_source(*args)

    async def test_filter_and_canonical_urls(self):
        session = _Session([_Response([raw_commit("b" * 40), raw_commit("a" * 40)])])
        changes = await GitHubCommitClient(session, configured_source()).since("a" * 40)
        self.assertEqual(len(changes), 1)
        url, options = session.calls[0]
        self.assertEqual(url, "https://api.github.com/repos/owner/repo/commits")
        self.assertEqual(options["params"], {"sha": "main", "page": 1, "per_page": 50, "path": "example"})
        self.assertFalse(options["allow_redirects"])
        self.assertNotIn("Authorization", options["headers"])
        self.assertEqual(changes[0]["url"], "https://github.com/owner/repo/commit/" + "b" * 40)

    async def test_whole_repository_and_seed(self):
        session = _Session([_Response([raw_commit("a" * 40)])])
        latest = await GitHubCommitClient(session, configured_source("")).latest()
        self.assertEqual(latest["sha"], "a" * 40)
        self.assertNotIn("path", session.calls[0][1]["params"])
        self.assertEqual(session.calls[0][1]["params"]["per_page"], 1)

    async def test_fail_closed_on_http_empty_and_malformed_history(self):
        for response in [_Response([], 301), _Response([], 403), _Response([], 429),
                         _Response([]), _Response({}), _Response([{}]),
                         _Response([raw_commit("bad")])]:
            with self.subTest(response=response), self.assertRaises(CommitCollectionError):
                await GitHubCommitClient(_Session([response]), configured_source()).latest()

    async def test_two_pages_and_bounded_missing_cursor(self):
        first = [raw_commit(f"{i:040x}") for i in range(100, 50, -1)]
        second = [raw_commit(f"{i:040x}") for i in range(50, 0, -1)]
        session = _Session([_Response(first), _Response(second)])
        changes = await GitHubCommitClient(session, configured_source()).since(f"{2:040x}")
        self.assertEqual(len(changes), 98)
        self.assertEqual(len(session.calls), 2)
        session = _Session([_Response(first), _Response(second)])
        with self.assertRaises(CommitCollectionError):
            await GitHubCommitClient(session, configured_source()).since("f" * 40)
        self.assertEqual(len(session.calls), 2)


class UpstreamDraftTests(unittest.IsolatedAsyncioTestCase):
    make_cog = ScheduledDraftTests.make_cog

    def setup_upstream(self):
        config = _Config()
        config.upstream_sources.value = {"example": configured_source()}
        cog, channel, project = self.make_cog(config)
        upstream = SimpleNamespace(since=AsyncMock(return_value=[commit("b" * 40, "Upstream fix")]))
        cog._upstream_client = AsyncMock(return_value=upstream)
        return config, cog, channel, project, upstream

    async def test_combined_draft_and_independent_cursors(self):
        config, cog, channel, project, upstream = self.setup_upstream()
        status, draft = await cog.collector_once(force=True)
        self.assertEqual(status, "created")
        self.assertEqual([source["kind"] for source in draft["sources"]].count("upstream"), 1)
        self.assertIn("Upstream: example", draft["text"])
        self.assertIn("not confirmed installations", draft["text"])
        self.assertEqual(draft["status"], "draft")
        upstream.since.assert_awaited_once_with("a" * 40)
        self.assertEqual(config.upstream_sources.value["example"]["last_sha"], "b" * 40)
        self.assertEqual(config.collector_last_sha.value, "new")
        self.assertEqual((await cog.collector_once(force=True))[0], "draft-active")
        self.assertEqual(upstream.since.await_count, 1)

    async def test_failure_after_successful_source_preserves_every_cursor(self):
        config, cog, _, _, upstream = self.setup_upstream()
        config.upstream_sources.value["second"] = configured_source("other")
        upstream.since.side_effect = [[commit("b" * 40, "First")], CommitCollectionError("missing cursor")]
        status, detail = await cog.collector_once(force=True)
        self.assertEqual(status, "error")
        self.assertIn("second", detail)
        self.assertEqual(config.drafts.value, {})
        self.assertEqual(config.collector_last_sha.value, "old")
        self.assertEqual(config.collector_last_slot.value, "2026-02-15")
        self.assertTrue(all(source["last_sha"] == "a" * 40 for source in config.upstream_sources.value.values()))
        upstream.since.side_effect = None
        status, _ = await cog.collector_once(force=True)
        self.assertEqual(status, "created")
        self.assertEqual(config.collector_last_error.value, "")

    async def test_pause_skips_requests_and_resume_keeps_cursor(self):
        config, cog, _, project, upstream = self.setup_upstream()
        ctx = SimpleNamespace(send=AsyncMock())
        await BotUpdates.upstream_enabled.callback(cog, ctx, "EXAMPLE", False)
        project.since.return_value = []
        self.assertEqual((await cog.collector_once(force=True))[0], "no-changes")
        upstream.since.assert_not_awaited()
        self.assertEqual(config.upstream_sources.value["example"]["last_sha"], "a" * 40)
        await BotUpdates.upstream_enabled.callback(cog, ctx, "example", True)
        self.assertEqual((await cog.collector_once(force=True))[0], "created")
        upstream.since.assert_awaited_once_with("a" * 40)

    async def test_announced_and_overlapping_sources_are_not_duplicated(self):
        config, cog, _, project, upstream = self.setup_upstream()
        config.upstream_sources.value["overlap"] = configured_source("overlap")
        _, draft = await cog.collector_once(force=True)
        self.assertEqual(len(draft["sources"]), 3)
        config.announced_source_keys.value = [source["key"] for source in draft["sources"]]
        config.drafts.value["1"]["status"] = "published"
        status, _ = await cog.collector_once(force=True)
        self.assertEqual(status, "already-announced")
        self.assertEqual(config.next_draft_id.value, 2)

    async def test_add_seeds_without_draft_and_rejects_duplicates(self):
        config = _Config()
        cog, _, _ = self.make_cog(config)
        client = SimpleNamespace(latest=AsyncMock(return_value=commit("a" * 40, "Baseline")))
        cog._upstream_client = AsyncMock(return_value=client)
        ctx = SimpleNamespace(send=AsyncMock())
        await BotUpdates.upstream_add.callback(cog, ctx, "example", "owner/repo", "main", path="example")
        self.assertEqual(config.upstream_sources.value["example"]["last_sha"], "a" * 40)
        self.assertEqual(config.drafts.value, {})
        for name in ("example", "other"):
            with self.assertRaises(commands.UserFeedbackCheckFailure):
                await BotUpdates.upstream_add.callback(cog, ctx, name, "owner/repo", "main", path="example")
        client.latest.assert_awaited_once()
        await BotUpdates.upstream.callback(cog, ctx)
        self.assertIn("example", ctx.send.call_args.args[0])
        await BotUpdates.upstream_remove.callback(cog, ctx, "example")
        self.assertEqual(config.upstream_sources.value, {})

    async def test_add_outage_and_limit_do_not_change_sources(self):
        config, cog, _, _, upstream = self.setup_upstream()
        upstream.latest = AsyncMock(side_effect=CommitCollectionError("HTTP 429"))
        ctx = SimpleNamespace(send=AsyncMock())
        with self.assertRaises(commands.UserFeedbackCheckFailure):
            await BotUpdates.upstream_add.callback(cog, ctx, "new", "owner/repo", "main", path="new")
        self.assertEqual(len(config.upstream_sources.value), 1)
        config.upstream_sources.value = {f"source{i}": configured_source(str(i)) for i in range(10)}
        with self.assertRaises(commands.UserFeedbackCheckFailure):
            await BotUpdates.upstream_add.callback(cog, ctx, "new", "owner/repo", "main", path="new")
        self.assertEqual(upstream.latest.await_count, 1)

    async def test_manual_create_waits_for_collector_and_cannot_replace_it(self):
        config, cog, _, project, _ = self.setup_upstream()
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(cursor):
            started.set()
            await release.wait()
            return [commit("new", "Change")]
        project.since.side_effect = delayed
        cog._require_publisher = AsyncMock()
        ctx = SimpleNamespace(send=AsyncMock(), author=SimpleNamespace(id=5))
        collection = asyncio.create_task(cog.collector_once(force=True))
        await started.wait()
        manual = asyncio.create_task(BotUpdates.draft_create.callback(cog, ctx, "digest", content="Title | Body"))
        await asyncio.sleep(0)
        self.assertFalse(manual.done())
        release.set()
        self.assertEqual((await collection)[0], "created")
        with self.assertRaises(commands.UserFeedbackCheckFailure):
            await manual
        self.assertEqual(len(config.drafts.value), 1)

    async def test_cancelled_collection_does_not_consume_cursors(self):
        config, cog, _, _, upstream = self.setup_upstream()
        upstream.since.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await cog.collector_once(force=True)
        self.assertEqual(config.drafts.value, {})
        self.assertEqual(config.collector_last_sha.value, "old")
        self.assertEqual(config.upstream_sources.value["example"]["last_sha"], "a" * 40)

    async def test_storage_failure_leaves_no_partial_draft_or_cursors(self):
        class FailingTransaction(_Transaction):
            async def __aexit__(self, *args):
                raise OSError("simulated write failure")
        class FailingConfig(_Config):
            def all(self):
                return FailingTransaction(self)
        config = FailingConfig()
        cog, channel, _ = self.make_cog(config)
        with self.assertRaises(OSError):
            await cog.collector_once(force=True)
        self.assertEqual(config.drafts.value, {})
        self.assertEqual(config.collector_last_sha.value, "old")
        channel.send.assert_not_awaited()

    async def test_invalid_source_reference_does_not_advance_cursor(self):
        config, cog, _, project, _ = self.setup_upstream()
        project.since.return_value = [dict(commit("new", "Bad URL"), url="https://")]
        self.assertEqual((await cog.collector_once(force=True))[0], "error")
        self.assertEqual(config.collector_last_sha.value, "old")
        self.assertEqual(config.drafts.value, {})

    async def test_all_review_references_are_pageable(self):
        config, cog, _, project, _ = self.setup_upstream()
        project.since.return_value = [commit(str(i), f"Change {i}") for i in range(12)]
        await cog.collector_once(force=True)
        cog._require_editor = AsyncMock()
        ctx = SimpleNamespace(send=AsyncMock())
        viewed = []
        for page in range(1, 4):
            await BotUpdates.draft_sources.callback(cog, ctx, page)
            embed = ctx.send.call_args.kwargs["embed"]
            self.assertLessEqual(len(embed.fields), 5)
            viewed.extend(field.name for field in embed.fields)
        self.assertEqual(len(viewed), 13)
        with self.assertRaises(commands.UserFeedbackCheckFailure):
            await BotUpdates.draft_sources.callback(cog, ctx, 4)

    async def test_restart_and_stopped_collector_do_not_query_sources(self):
        config, cog, _, project, upstream = self.setup_upstream()
        now = datetime.datetime.now(datetime.timezone.utc)
        config.collector_last_slot.value = digest_slot(now)
        self.assertEqual((await cog.collector_once(now=now))[0], "not-due")
        project.since.assert_not_awaited()
        upstream.since.assert_not_awaited()
        config.collector_enabled.value = False
        config.collector_last_slot.value = "2020-01-01"
        self.assertEqual((await cog.collector_once(now=now))[0], "disabled")
        project.since.assert_not_awaited()
        upstream.since.assert_not_awaited()
        self.assertEqual(config.upstream_sources.value["example"]["last_sha"], "a" * 40)

    async def test_manual_draft_creation_saves_consistent_state(self):
        config = _Config()
        cog, _, _ = self.make_cog(config)
        cog._require_publisher = AsyncMock()
        ctx = SimpleNamespace(send=AsyncMock(), author=SimpleNamespace(id=5))
        await BotUpdates.draft_create.callback(cog, ctx, "digest", content="Title | Body")
        self.assertEqual(config.active_draft_id.value, 1)
        self.assertEqual(config.next_draft_id.value, 2)
        self.assertEqual(config.drafts.value["1"]["title"], "Title")
        self.assertEqual((await cog.collector_once(force=True))[0], "draft-active")


if __name__ == "__main__":
    unittest.main()
