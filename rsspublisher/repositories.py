import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, urlparse, urlunparse


_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


class RepositoryURLValidationError(ValueError):
    """Raised when a repository URL or branch cannot form a safe public feed."""


@dataclass(frozen=True)
class RepositoryFeed:
    provider: str
    repository: str
    branch: str
    repository_url: str
    feed_url: str


def _clean_segments(path: str) -> list[str]:
    segments = [segment for segment in path.strip("/").split("/") if segment]
    if segments and segments[-1].endswith(".git"):
        segments[-1] = segments[-1][:-4]
    if not segments or any(not _SEGMENT_PATTERN.fullmatch(segment) for segment in segments):
        raise RepositoryURLValidationError(
            "Repository paths may contain only letters, numbers, dots, underscores, and hyphens."
        )
    return segments


def build_repository_feed(repository_url: str, branch: str) -> RepositoryFeed:
    """Build a provider branch feed from a canonical public repository URL."""
    raw_url = (repository_url or "").strip()
    parsed = urlparse(raw_url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RepositoryURLValidationError("Use a complete HTTPS repository URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RepositoryURLValidationError(
            "Repository URLs cannot contain credentials, query parameters, or fragments."
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise RepositoryURLValidationError("The repository URL contains an invalid port.") from exc
    host = parsed.hostname.casefold().rstrip(".")
    netloc = host if port is None else f"{host}:{port}"
    segments = _clean_segments(parsed.path)

    clean_branch = (branch or "").strip()
    if (
        not clean_branch
        or len(clean_branch) > 255
        or _CONTROL_PATTERN.search(clean_branch)
        or clean_branch.startswith("/")
        or clean_branch.endswith("/")
        or clean_branch in {".", ".."}
    ):
        raise RepositoryURLValidationError("Provide a valid branch name up to 255 characters.")
    encoded_branch = quote(clean_branch, safe="")
    base_url = urlunparse(("https", netloc, "", "", "", ""))

    if host == "github.com":
        if len(segments) != 2:
            raise RepositoryURLValidationError(
                "GitHub repository URLs must look like https://github.com/owner/repository."
            )
        provider = "github"
        repository = "/".join(segments)
        canonical_url = f"{base_url}/{repository}"
        feed_url = f"{canonical_url}/commits/{encoded_branch}.atom"
    elif host == "gitlab.com":
        if len(segments) < 2:
            raise RepositoryURLValidationError(
                "GitLab repository URLs must include a namespace and repository."
            )
        provider = "gitlab"
        repository = "/".join(segments)
        canonical_url = f"{base_url}/{repository}"
        feed_url = f"{canonical_url}/-/commits/{encoded_branch}?format=atom"
    else:
        if len(segments) != 2:
            raise RepositoryURLValidationError(
                "Gitea repository URLs must look like https://host/owner/repository."
            )
        provider = "gitea"
        repository = "/".join(segments)
        canonical_url = f"{base_url}/{repository}"
        feed_url = f"{canonical_url}/rss/branch/{encoded_branch}"

    return RepositoryFeed(
        provider=provider,
        repository=repository,
        branch=clean_branch,
        repository_url=canonical_url,
        feed_url=feed_url,
    )


def repository_metadata(feed: object) -> Optional[dict[str, str]]:
    if not isinstance(feed, dict):
        return None
    provider = feed.get("repository_provider")
    repository = feed.get("repository_name")
    branch = feed.get("repository_branch")
    repository_url = feed.get("repository_url")
    if not all(isinstance(value, str) and value for value in (
        provider,
        repository,
        branch,
        repository_url,
    )):
        return None
    return {
        "provider": provider,
        "repository": repository,
        "branch": branch,
        "repository_url": repository_url,
    }
