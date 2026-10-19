import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

from gitforge.gitforge import MAX_CONCURRENT_POLL_REQUESTS, GitForge
from gitforge.providers import ForgeError


class _Group:
    def __init__(self):
        self.repositories = SimpleNamespace(set=AsyncMock())


class _Config:
    def __init__(self, settings):
        self.settings = settings
        self.groups = {int(guild_id): _Group() for guild_id in settings}

    async def all_guilds(self):
        return self.settings

    def guild(self, guild):
        return self.groups[guild.id]


def _guild(guild_id, channels=None):
    channels = channels or {}
    return SimpleNamespace(
        id=guild_id,
        get_channel=lambda channel_id: channels.get(channel_id),
    )


def _repository(connection, owner, repo, *, issues=0, ci=0):
    return {
        "connection": connection,
        "owner": owner,
        "repo": repo,
        "issue_channel_id": issues,
        "ci_channel_id": ci,
        "seen_issue_ids": [],
        "issues_initialized": False,
        "ci_states": {},
        "ci_initialized": False,
    }


def _client(token="shared"):
    return SimpleNamespace(
        provider="gitea",
        base_url="https://git.example.com",
        token=token,
        list_issues=AsyncMock(return_value=[{
            "id": 10,
            "number": 4,
            "title": "Issue",
            "url": "https://git.example.com/owner/repo/issues/4",
            "author": "user",
        }]),
        list_ci_runs=AsyncMock(return_value=[{
            "id": "8",
            "name": "test",
            "status": "success",
            "url": "https://git.example.com/owner/repo/actions/8",
            "branch": "main",
        }]),
    )


class GitForgeTests(unittest.IsolatedAsyncioTestCase):
    def test_default_policy_is_admin_only(self):
        admin = SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True))
        member = SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=False))
        self.assertTrue(GitForge.can_create(admin, {}))
        self.assertFalse(GitForge.can_create(member, {}))
        self.assertTrue(GitForge.can_create(member, {"policy": "members"}))

    def test_issue_detail_embed_is_bounded_and_suppresses_missing_data(self):
        embed = GitForge.issue_embed("project", {
            "number": 42,
            "title": "External *title*",
            "url": "https://git.example.com/owner/repo/issues/42",
            "body": "x" * 4000,
            "author": "alice",
            "state": "open",
            "labels": ["bug"],
            "comments": 2,
            "updated_at": "2026-10-19T12:00:00Z",
        })
        self.assertEqual(embed.title, "Issue #42 — External \*title\*")
        self.assertEqual(len(embed.description), 3000)
        self.assertEqual(embed.url, "https://git.example.com/owner/repo/issues/42")
        self.assertEqual(embed.fields[3].value, "2")

    def test_issue_command_accepts_optional_detail_number(self):
        self.assertIn("number", GitForge.issue.clean_params)
        self.assertIn("query", GitForge.search.clean_params)

    async def test_issue_poll_seeds_without_sending(self):
        cog = object.__new__(GitForge)
        client = SimpleNamespace(
            list_issues=AsyncMock(return_value=[
                {"id": 10, "number": 4, "title": "Existing", "url": "https://example/4", "author": "user"}
            ])
        )
        guild = SimpleNamespace(get_channel=lambda channel_id: None)
        repository = {"owner": "owner", "repo": "repo", "issue_channel_id": 5, "seen_issue_ids": []}
        changed = await cog._poll_issues(guild, "repo", repository, client)
        self.assertTrue(changed)
        self.assertEqual(repository["seen_issue_ids"], [10])
        self.assertTrue(repository["issues_initialized"])

    async def test_issue_poll_announces_new_issue(self):
        cog = object.__new__(GitForge)
        client = SimpleNamespace(
            list_issues=AsyncMock(return_value=[
                {"id": 11, "number": 5, "title": "New", "url": "https://example/5", "author": "user"},
                {"id": 10, "number": 4, "title": "Old", "url": "https://example/4", "author": "user"},
            ])
        )
        channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(get_channel=lambda channel_id: channel)
        repository = {"owner": "owner", "repo": "repo", "issue_channel_id": 5, "seen_issue_ids": [10], "issues_initialized": True}
        await cog._poll_issues(guild, "repo", repository, client)
        channel.send.assert_awaited_once()
        self.assertIn(11, repository["seen_issue_ids"])

    async def test_ci_seed_does_not_announce_existing_failure(self):
        cog = object.__new__(GitForge)
        client = SimpleNamespace(
            list_ci_runs=AsyncMock(return_value=[
                {"id": "8", "name": "test", "status": "failure", "url": "https://example/8", "branch": "main"}
            ])
        )
        channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(get_channel=lambda channel_id: channel)
        repository = {"owner": "owner", "repo": "repo", "ci_channel_id": 5, "ci_states": {}}
        await cog._poll_ci(guild, "repo", repository, client)
        channel.send.assert_not_awaited()
        self.assertEqual(repository["ci_states"], {"8": "failure"})
        self.assertTrue(repository["ci_initialized"])

    async def test_ci_transition_to_failure_is_announced(self):
        cog = object.__new__(GitForge)
        client = SimpleNamespace(
            list_ci_runs=AsyncMock(return_value=[
                {"id": "8", "name": "test", "status": "failure", "url": "https://example/8", "branch": "main"}
            ])
        )
        channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(get_channel=lambda channel_id: channel)
        repository = {"owner": "owner", "repo": "repo", "ci_channel_id": 5, "ci_states": {"8": "in_progress"}, "ci_initialized": True}
        await cog._poll_ci(guild, "repo", repository, client)
        channel.send.assert_awaited_once()


class PollCycleTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, settings, clients):
        cog = object.__new__(GitForge)
        cog._poll_backoff = {}
        guilds = {int(guild_id): _guild(int(guild_id)) for guild_id in settings}
        cog.bot = SimpleNamespace(get_guild=lambda guild_id: guilds.get(guild_id))
        cog.config = _Config(settings)
        cog.client_for = AsyncMock(
            side_effect=lambda guild, connection: clients[(guild.id, connection)]
        )
        cog._poll_issues = AsyncMock(return_value=True)
        cog._poll_ci = AsyncMock(return_value=True)
        return cog

    async def test_shared_repository_resources_are_fetched_once_and_fanned_out(self):
        client = _client()
        settings = {
            "1": {"repositories": {
                "primary": _repository("forge", "owner", "repo", issues=10, ci=11),
            }},
            "2": {"repositories": {
                "renamed": _repository("forge", "owner", "repo", issues=20, ci=21),
            }},
            "3": {"repositories": {
                "inactive": _repository("forge", "owner", "repo"),
            }},
        }
        clients = {(1, "forge"): client, (2, "forge"): client}
        cog = self.make_cog(settings, clients)

        await cog.poll_once()

        client.list_issues.assert_awaited_once_with("owner", "repo")
        client.list_ci_runs.assert_awaited_once_with("owner", "repo")
        self.assertEqual(cog._poll_issues.await_count, 2)
        self.assertEqual(cog._poll_ci.await_count, 2)
        issue_results = [
            item.kwargs["issues"] for item in cog._poll_issues.await_args_list
        ]
        ci_results = [item.kwargs["runs"] for item in cog._poll_ci.await_args_list]
        self.assertIs(issue_results[0], issue_results[1])
        self.assertIs(ci_results[0], ci_results[1])
        self.assertIsInstance(issue_results[0], tuple)
        self.assertEqual(cog.client_for.await_count, 2)
        cog.config.groups[1].repositories.set.assert_awaited_once()
        cog.config.groups[2].repositories.set.assert_awaited_once()
        cog.config.groups[3].repositories.set.assert_not_awaited()

    async def test_different_credentials_do_not_share_provider_results(self):
        first = _client("first-token")
        second = _client("second-token")
        settings = {
            "1": {"repositories": {
                "repo": _repository("forge", "owner", "repo", issues=10),
            }},
            "2": {"repositories": {
                "repo": _repository("forge", "owner", "repo", issues=20),
            }},
        }
        cog = self.make_cog(
            settings,
            {(1, "forge"): first, (2, "forge"): second},
        )

        await cog.poll_once()

        first.list_issues.assert_awaited_once()
        second.list_issues.assert_awaited_once()

    async def test_provider_concurrency_is_bounded(self):
        client = _client()
        repositories = {
            f"repo-{index}": _repository(
                "forge", "owner", f"repo-{index}", issues=index + 1
            )
            for index in range(MAX_CONCURRENT_POLL_REQUESTS + 3)
        }
        settings = {"1": {"repositories": repositories}}
        cog = self.make_cog(settings, {(1, "forge"): client})
        active = 0
        maximum = 0

        async def tracked_fetch(_owner, _repo):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return []

        client.list_issues.side_effect = tracked_fetch

        await cog.poll_once()

        self.assertEqual(client.list_issues.await_count, len(repositories))
        self.assertLessEqual(maximum, MAX_CONCURRENT_POLL_REQUESTS)
        self.assertGreater(maximum, 1)
        self.assertEqual(cog.client_for.await_count, 1)
        called_guild, called_connection = cog.client_for.await_args.args
        self.assertEqual(called_guild.id, 1)
        self.assertEqual(called_connection, "forge")

    async def test_failed_job_backs_off_without_blocking_other_jobs(self):
        client = _client()
        settings = {"1": {"repositories": {
            "bad": _repository("forge", "owner", "bad", issues=10),
            "good": _repository("forge", "owner", "good", issues=11),
        }}}
        cog = self.make_cog(settings, {(1, "forge"): client})

        async def fetch(_owner, repo):
            if repo == "bad":
                raise ForgeError("temporarily unavailable")
            return []

        client.list_issues.side_effect = fetch

        await cog.poll_once()
        await cog.poll_once()

        self.assertEqual(
            client.list_issues.await_args_list,
            [
                call("owner", "bad"),
                call("owner", "good"),
                call("owner", "good"),
            ],
        )
        self.assertEqual(cog._poll_issues.await_count, 2)
        self.assertEqual(len(cog._poll_backoff), 1)

    async def test_delivery_failure_does_not_block_other_consumers(self):
        client = _client()
        settings = {
            "1": {"repositories": {
                "repo": _repository("forge", "owner", "repo", issues=10),
            }},
            "2": {"repositories": {
                "repo": _repository("forge", "owner", "repo", issues=20),
            }},
        }
        cog = self.make_cog(
            settings,
            {(1, "forge"): client, (2, "forge"): client},
        )
        cog._poll_issues.side_effect = [RuntimeError("Discord failed"), True]

        await cog.poll_once()

        self.assertEqual(cog._poll_issues.await_count, 2)
        cog.config.groups[1].repositories.set.assert_not_awaited()
        cog.config.groups[2].repositories.set.assert_awaited_once()


class SharedResultDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_issues_preserve_independent_seen_histories(self):
        first_channel = SimpleNamespace(send=AsyncMock())
        second_channel = SimpleNamespace(send=AsyncMock())
        first = _repository("forge", "owner", "repo", issues=10)
        first.update({"issues_initialized": True, "seen_issue_ids": [10]})
        second = _repository("forge", "owner", "repo", issues=20)
        second.update({"issues_initialized": True, "seen_issue_ids": [10, 11]})
        issues = (
            {"id": 11, "number": 5, "title": "New", "url": "https://example/5", "author": "user"},
            {"id": 10, "number": 4, "title": "Old", "url": "https://example/4", "author": "user"},
        )
        client = _client()
        cog = object.__new__(GitForge)

        await cog._poll_issues(
            _guild(1, {10: first_channel}),
            "first",
            first,
            client,
            issues=issues,
        )
        await cog._poll_issues(
            _guild(2, {20: second_channel}),
            "second",
            second,
            client,
            issues=issues,
        )

        first_channel.send.assert_awaited_once()
        second_channel.send.assert_not_awaited()
        client.list_issues.assert_not_awaited()
        self.assertEqual(first["seen_issue_ids"][:2], [11, 10])
        self.assertEqual(second["seen_issue_ids"][:2], [11, 10])
