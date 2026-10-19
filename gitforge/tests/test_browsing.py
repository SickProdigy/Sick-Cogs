import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gitforge.providers import ForgeClient, ForgeError
from gitforge.gitforge import GitForge


class Client(ForgeClient):
    def __init__(self, provider, responses):
        super().__init__(provider, "https://forge.example.com", "test")
        self.responses = iter(responses)
        self.calls = []

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


class BrowseTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_matrix(self):
        for provider in ("gitea", "github", "gitlab"):
            with self.subTest(provider=provider):
                lab = provider == "gitlab"
                url = "https://forge.example.com/team/project"
                commit = {"id": "abcdef123", "sha": "abcdef123", "title": "Fix", "web_url": url,
                          "html_url": url, "commit": {"message": "Fix\nDetails"}}
                pull = {"iid": 7, "number": 7, "title": "Change", "web_url": url, "html_url": url}
                release = {"tag_name": "v1", "name": "Version 1", "_links": {"self": url}, "html_url": url}
                client = Client(provider, [{"default_branch": "develop", "html_url": url, "private": True},
                                           [commit] * 12, [pull] * 12, release])
                summary = await client.repository_summary("team/sub", "project")
                self.assertEqual(summary["branch"], "develop")
                self.assertEqual(summary["visibility"], "Private")
                commits = await client.recent_commits("team/sub", "project", "feature/test")
                self.assertEqual(len(commits), 10)
                self.assertEqual(commits[0]["title"], "Fix")
                params = client.calls[1][2]["params"]
                self.assertEqual(params["ref_name" if lab else "sha"], "feature/test")
                self.assertEqual(client.calls[1][1],
                                 "/projects/team%2Fsub%2Fproject/repository/commits" if lab
                                 else "/repos/team%2Fsub/project/commits")
                pulls = await client.open_pulls("team/sub", "project")
                self.assertEqual(len(pulls), 10)
                params = client.calls[2][2]["params"]
                self.assertEqual(params["state"], "opened" if lab else "open")
                if lab:
                    self.assertEqual(params["scope"], "all")
                self.assertEqual((await client.latest_release("team/sub", "project"))["tag"], "v1")
                self.assertTrue(client.calls[-1][1].endswith("/releases/permalink/latest" if lab else "/releases/latest"))

    async def test_empty_lists_and_default_branch(self):
        client = Client("github", [[], []])
        self.assertEqual(await client.recent_commits("o", "r"), [])
        self.assertNotIn("sha", client.calls[0][2]["params"])
        self.assertEqual(await client.open_pulls("o", "r"), [])

    async def test_missing_release_requires_repository_access(self):
        client = Client("gitea", [ForgeError("missing", status=404), {"full_name": "o/r"}])
        self.assertIsNone(await client.latest_release("o", "r"))
        self.assertEqual(len(client.calls), 2)
        client = Client("gitea", [ForgeError("missing", status=404), ForgeError("denied", status=403)])
        with self.assertRaises(ForgeError):
            await client.latest_release("o", "r")

    async def test_errors_and_malformed_responses_are_not_empty_results(self):
        for response in (ForgeError("rate limited", status=429), {}, [None]):
            client = Client("github", [response])
            with self.assertRaises(ForgeError):
                await client.recent_commits("o", "r")
        client = Client("gitlab", [{}])
        with self.assertRaises(ForgeError):
            await client.latest_release("o", "r")
        client = Client("github", [])
        with self.assertRaises(ValueError):
            await client.recent_commits("o", "r", "x" * 201)
        self.assertEqual(client.calls, [])

    def test_links_reject_credentials_foreign_hosts_and_unsafe_ports(self):
        client = Client("gitea", [])
        for url in ("http://forge.example.com/a", "https://evil.example/a", "https://user:secret@forge.example.com/a",
                    "https://forge.example.com:444/a", "https://forge.example.com:bad/a", "javascript:alert(1)"):
            self.assertIsNone(client.browser_url(url))
        self.assertEqual(client.browser_url("https://forge.example.com/a)b"), "https://forge.example.com/a%29b")

    async def test_commands_route_read_only_calls_and_bound_output(self):
        cog = GitForge.__new__(GitForge)
        url = "https://forge.example.com/o/r"
        client = SimpleNamespace(
            repository_summary=AsyncMock(return_value={"name": "x" * 400, "description": "@everyone" * 700,
                "branch": "main", "visibility": "Public", "archived": False, "url": url}),
            recent_commits=AsyncMock(return_value=[{"label": "12345678", "title": "@everyone" * 700, "url": url}] * 10),
            open_pulls=AsyncMock(return_value=[]),
            latest_release=AsyncMock(return_value=None),
        )
        cog.repository_for = AsyncMock(return_value=("project", {"owner": "o", "repo": "r"}, client))
        ctx = SimpleNamespace(guild=object(), defer=AsyncMock(), send=AsyncMock())
        await GitForge.browse_repo.callback(cog, ctx, "project")
        embed = ctx.send.await_args.kwargs["embed"]
        self.assertLessEqual(len(embed), 6000)
        self.assertLessEqual(len(embed.title), 256)
        self.assertFalse(ctx.send.await_args.kwargs["allowed_mentions"].everyone)
        await GitForge.browse_commits.callback(cog, ctx, "project", "feature/test")
        client.recent_commits.assert_awaited_once_with("o", "r", "feature/test")
        self.assertLessEqual(len(ctx.send.await_args.kwargs["embed"].description), 4000)
        await GitForge.browse_pulls.callback(cog, ctx, "project")
        self.assertIn("No open", ctx.send.await_args.kwargs["embed"].description)
        await GitForge.browse_release.callback(cog, ctx, "project")
        self.assertIn("No latest release", ctx.send.await_args.args[0])
        self.assertEqual(ctx.defer.await_count, 4)
