import unittest

from rsspublisher.repositories import (
    RepositoryURLValidationError,
    build_repository_feed,
    repository_metadata,
)


class RepositoryFeedTests(unittest.TestCase):
    def test_github_branch_feed(self):
        result = build_repository_feed(
            "https://github.com/OpenAI/openai-python.git", "release/next"
        )
        self.assertEqual(result.provider, "github")
        self.assertEqual(result.repository, "OpenAI/openai-python")
        self.assertEqual(
            result.feed_url,
            "https://github.com/OpenAI/openai-python/commits/release%2Fnext.atom",
        )

    def test_gitlab_nested_namespace_feed(self):
        result = build_repository_feed(
            "https://gitlab.com/group/subgroup/project", "main"
        )
        self.assertEqual(result.provider, "gitlab")
        self.assertEqual(result.repository, "group/subgroup/project")
        self.assertEqual(
            result.feed_url,
            "https://gitlab.com/group/subgroup/project/-/commits/main?format=atom",
        )

    def test_self_hosted_gitea_feed(self):
        result = build_repository_feed(
            "https://gitea.example.test/owner/project", "develop"
        )
        self.assertEqual(result.provider, "gitea")
        self.assertEqual(
            result.feed_url,
            "https://gitea.example.test/owner/project/rss/branch/develop",
        )

    def test_credentials_queries_fragments_and_http_are_rejected(self):
        invalid_urls = (
            "http://github.com/owner/repo",
            "https://user:pass@github.com/owner/repo",
            "https://github.com/owner/repo?token=secret",
            "https://github.com/owner/repo#readme",
        )
        for value in invalid_urls:
            with self.subTest(value=value):
                with self.assertRaises(RepositoryURLValidationError):
                    build_repository_feed(value, "main")

    def test_provider_path_shapes_are_bounded(self):
        with self.assertRaises(RepositoryURLValidationError):
            build_repository_feed("https://github.com/owner/repo/extra", "main")
        with self.assertRaises(RepositoryURLValidationError):
            build_repository_feed("https://gitea.example.test/group/sub/repo", "main")

    def test_invalid_branch_is_rejected(self):
        for branch in ("", "/main", "main/", "bad\nbranch"):
            with self.subTest(branch=branch):
                with self.assertRaises(RepositoryURLValidationError):
                    build_repository_feed("https://github.com/owner/repo", branch)

    def test_repository_metadata_rejects_partial_legacy_data(self):
        self.assertIsNone(repository_metadata({"repository_provider": "github"}))
        metadata = repository_metadata(
            {
                "repository_provider": "github",
                "repository_name": "owner/repo",
                "repository_branch": "main",
                "repository_url": "https://github.com/owner/repo",
            }
        )
        self.assertEqual(metadata["branch"], "main")


if __name__ == "__main__":
    unittest.main()
