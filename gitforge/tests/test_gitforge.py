import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gitforge.gitforge import GitForge


class GitForgeTests(unittest.IsolatedAsyncioTestCase):
    def test_default_policy_is_admin_only(self):
        admin = SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True))
        member = SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=False))
        self.assertTrue(GitForge.can_create(admin, {}))
        self.assertFalse(GitForge.can_create(member, {}))
        self.assertTrue(GitForge.can_create(member, {"policy": "members"}))

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
