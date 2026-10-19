"""Public Gitea commit collection for scheduled BotUpdates drafts."""

import datetime


REPOSITORY_API_URL = "https://gitea.rcs1.top/api/v1/repos/sickprodigy/Sick-Cogs"
REPOSITORY_WEB_URL = "https://gitea.rcs1.top/sickprodigy/Sick-Cogs"
REPOSITORY_BRANCH = "main"
MAX_COLLECTED_COMMITS = 100


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
        "A scheduled Sick-Cogs change digest is ready for human review.",
        "",
    ]
    for commit in reversed(commits):
        short_sha = commit["sha"][:8]
        line = f"• `{short_sha}` — {commit['message']}"
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
        return [commit for item in payload if (commit := normalize_commit(item))]

    async def latest(self):
        commits = await self._page(1, per_page=1)
        if not commits:
            raise CommitCollectionError("Gitea returned no Sick-Cogs commits.")
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
                        "More than 100 commits are pending; review the collector cursor manually."
                    )
            if len(commits) < 50:
                break
        raise CommitCollectionError(
            "The saved commit cursor was not found in the bounded Gitea history."
        )
