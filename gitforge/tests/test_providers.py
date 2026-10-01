import unittest

from gitforge.providers import ForgeClient, ForgeError, normalize_alias, normalize_base_url


class FakeClient(ForgeClient):
    def __init__(self, provider, responses):
        super().__init__(provider, "https://github.com" if provider == "github" else f"https://{provider}.example.com", "secret")
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, path, *, params=None, payload=None):
        self.calls.append((method, path, params, payload))
        return self.responses.pop(0)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_normalization(self):
        self.assertEqual(normalize_alias(" My-Repo "), "my-repo")
        self.assertEqual(normalize_base_url("https://github.com/", "github"), "https://github.com")
        self.assertEqual(normalize_base_url("https://git.example.com/api/v1", "gitea"), "https://git.example.com")
        with self.assertRaises(ValueError):
            normalize_base_url("http://git.example.com", "gitea")
        with self.assertRaises(ValueError):
            normalize_alias("../bad")

    def test_provider_api_roots_and_paths(self):
        self.assertEqual(ForgeClient("github", "https://github.com", "x").api_url, "https://api.github.com")
        self.assertEqual(ForgeClient("gitlab", "https://gitlab.com", "x").api_url, "https://gitlab.com/api/v4")
        self.assertEqual(
            ForgeClient("gitlab", "https://gitlab.com", "x").repo_path("group/subgroup", "repo"),
            "/projects/group%2Fsubgroup%2Frepo",
        )

    async def test_github_issue_payload(self):
        client = FakeClient("github", [{"number": 4, "title": "Bug", "html_url": "https://example/4"}])
        issue = await client.create_issue("owner", "repo", "Bug", "Details", ["bug"])
        self.assertEqual(issue.number, 4)
        self.assertEqual(client.calls[0][3], {"title": "Bug", "body": "Details", "labels": ["bug"]})

    async def test_gitlab_issue_payload(self):
        client = FakeClient("gitlab", [{"iid": 7, "title": "Bug", "web_url": "https://example/7"}])
        issue = await client.create_issue("owner", "repo", "Bug", "Details", ["bug", "discord"])
        self.assertEqual(issue.number, 7)
        self.assertEqual(client.calls[0][3]["labels"], "bug,discord")

    async def test_gitea_resolves_label_names_to_ids(self):
        client = FakeClient(
            "gitea",
            [[{"id": 9, "name": "bug"}], {"number": 3, "title": "Bug", "html_url": "https://example/3"}],
        )
        await client.create_issue("owner", "repo", "Bug", "Details", ["BUG"])
        self.assertEqual(client.calls[1][3]["labels"], [9])

    async def test_unknown_gitea_label_fails_before_create(self):
        client = FakeClient("gitea", [[{"id": 9, "name": "bug"}]])
        with self.assertRaises(ForgeError):
            await client.create_issue("owner", "repo", "Bug", "Details", ["feature"])
        self.assertEqual(len(client.calls), 1)

    async def test_github_pull_requests_are_not_returned_as_issues(self):
        client = FakeClient(
            "github",
            [[
                {"id": 1, "number": 1, "title": "Issue", "html_url": "https://example/1", "user": {"login": "a"}},
                {"id": 2, "number": 2, "title": "PR", "html_url": "https://example/2", "user": {"login": "b"}, "pull_request": {}},
            ]],
        )
        issues = await client.list_issues("owner", "repo")
        self.assertEqual([item["number"] for item in issues], [1])
