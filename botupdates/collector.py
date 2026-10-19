"""Public project and upstream commit collection for scheduled review drafts."""

import datetime
import re


REPOSITORY_API_URL = "https://gitea.rcs1.top/api/v1/repos/sickprodigy/Sick-Cogs"
REPOSITORY_WEB_URL = "https://gitea.rcs1.top/sickprodigy/Sick-Cogs"
REPOSITORY_BRANCH = "main"
MAX_COLLECTED_COMMITS = 100
MAX_UPSTREAM_SOURCES = 10


class CommitCollectionError(RuntimeError):
    pass


def digest_slot(now):
    """Return the current first/15th UTC digest slot."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    now = now.astimezone(datetime.timezone.utc)
    day = 15 if now.day >= 15 else 1
    return f"{now.year:04d}-{now.month:02d}-{day:02d}"


def normalize_commit(item):
    if not isinstance(item, dict):
        return None
    sha = str(item.get("sha") or "").strip()
    commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
    message_lines = str(commit.get("message") or "").splitlines()
    message = message_lines[0].strip() if message_lines else ""
    url = str(item.get("html_url") or "").strip()
    if not sha or not message or not url.startswith("https://"):
        return None
    return {"sha": sha, "message": message, "url": url}


def digest_text(commits):
    lines = [
        "A scheduled bot change digest is ready for human review.",
        "Upstream entries describe available changes, not confirmed installations.",
        "",
    ]
    for commit in reversed(commits):
        short_sha = commit["sha"][:8]
        label = commit.get("source_label", "Sick-Cogs")
        line = f"• [{label}] `{short_sha}` — {commit['message']}"
        if sum(len(value) + 1 for value in lines) + len(line) > 3700:
            lines.append("• Additional collected commits are attached as sources.")
            break
        lines.append(line)
    return "\n".join(lines)


class GiteaCommitClient:
    def __init__(self, session):
        self.session = session

    async def _page(self, page, per_page=50):
        params = {
            "sha": REPOSITORY_BRANCH,
            "page": int(page),
            "limit": int(per_page),
        }
        try:
            async with self.session.get(
                f"{REPOSITORY_API_URL}/commits", params=params
            ) as response:
                if response.status >= 400:
                    raise CommitCollectionError(
                        f"Gitea returned HTTP {response.status}."
                    )
                payload = await response.json(content_type=None)
        except CommitCollectionError:
            raise
        except Exception as exc:
            raise CommitCollectionError(
                "Could not retrieve Sick-Cogs commits from Gitea."
            ) from exc
        if not isinstance(payload, list):
            raise CommitCollectionError("Gitea returned an invalid commit list.")
        commits = [normalize_commit(item) for item in payload]
        if any(commit is None for commit in commits):
            raise CommitCollectionError("Gitea returned an invalid commit entry.")
        return commits

    async def latest(self):
        commits = await self._page(1, per_page=1)
        if not commits:
            raise CommitCollectionError("The configured source returned no commits.")
        return commits[0]

    async def since(self, last_sha):
        last_sha = str(last_sha or "").strip()
        if not last_sha:
            raise CommitCollectionError("The collector has no baseline commit.")
        collected = []
        for page in range(1, 3):
            commits = await self._page(page, per_page=50)
            for commit in commits:
                if commit["sha"] == last_sha:
                    return collected
                collected.append(commit)
                if len(collected) >= MAX_COLLECTED_COMMITS:
                    raise CommitCollectionError(
                        "The cursor was not reached within 100 commits; review it manually."
                    )
            if len(commits) < 50:
                break
        raise CommitCollectionError(
            "The saved commit cursor was not found in the bounded source history."
        )


def upstream_source(name, repository, branch, path=""):
    """Validate an explicitly configured public GitHub source, never an arbitrary URL."""
    name = name.lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", name):
        raise ValueError("Use a source name of 1–32 letters, digits, underscores or hyphens.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}", repository):
        raise ValueError("Use a public GitHub repository as owner/repository, not a URL.")
    for label, value in (("branch", branch), ("path", path)):
        if len(value) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(f"Invalid {label}: use at most 200 printable characters.")
        if value.startswith("/") or "\\" in value or any(part in {".", ".."} for part in value.split("/")):
            raise ValueError(f"Use a repository-relative {label} without traversal.")
    if not branch.strip():
        raise ValueError("Specify the upstream branch explicitly.")
    return name, {
        "repository": repository.lower(), "branch": branch, "path": path.rstrip("/"),
        "enabled": True, "last_sha": "",
    }


class GitHubCommitClient(GiteaCommitClient):
    """Public, credential-free and bounded commit history with an optional path filter."""

    def __init__(self, session, source):
        super().__init__(session)
        _, self.source = upstream_source(
            "source", source["repository"], source["branch"], source.get("path", "")
        )

    async def _page(self, page, per_page=50):
        repository = self.source["repository"]
        params = {"sha": self.source["branch"], "page": page, "per_page": per_page}
        if self.source["path"]:
            params["path"] = self.source["path"]
        try:
            async with self.session.get(
                f"https://api.github.com/repos/{repository}/commits",
                params=params, allow_redirects=False,
                headers={"Accept": "application/vnd.github+json"},
            ) as response:
                if response.status != 200:
                    raise CommitCollectionError(f"GitHub returned HTTP {response.status}.")
                payload = await response.json(content_type=None)
        except CommitCollectionError:
            raise
        except Exception as exc:
            raise CommitCollectionError("Could not retrieve public GitHub commits.") from exc
        if not isinstance(payload, list) or len(payload) > per_page:
            raise CommitCollectionError("GitHub returned an invalid commit list.")
        commits = []
        for item in payload:
            commit = normalize_commit(item)
            if commit is None or not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit["sha"]):
                raise CommitCollectionError("GitHub returned an invalid commit entry.")
            commit["url"] = f"https://github.com/{repository}/commit/{commit['sha']}"
            commits.append(commit)
        return commits
