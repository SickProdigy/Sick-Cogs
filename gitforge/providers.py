"""Provider-neutral async clients for Gitea, GitHub, and GitLab."""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import aiohttp

SUPPORTED_PROVIDERS = {"gitea", "github", "gitlab"}
PUBLIC_HOSTS = {"github.com", "gitlab.com"}


class ForgeError(RuntimeError):
    def __init__(self, message, *, code="api_error", status=0, retry_after=None):
        super().__init__(message)
        self.code = code
        self.status = int(status or 0)
        self.retry_after = retry_after


@dataclass(frozen=True)
class CreatedIssue:
    number: int
    title: str
    url: str


def normalize_alias(value):
    alias = str(value).strip().casefold()
    if not alias or len(alias) > 32 or not all(char.isalnum() or char in "-_" for char in alias):
        raise ValueError("Aliases must be 1-32 characters using letters, numbers, hyphens, or underscores.")
    return alias


def normalize_provider(value):
    provider = str(value).strip().casefold()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError("Provider must be gitea, github, or gitlab.")
    return provider


def normalize_base_url(value, provider):
    provider = normalize_provider(provider)
    base = str(value).strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https":
        raise ValueError("Forge URLs must use HTTPS.")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Enter a forge URL without credentials, query parameters, or fragments.")
    if parsed.port not in (None, 443):
        raise ValueError("Forge URLs must use the standard HTTPS port.")
    path = parsed.path.rstrip("/")
    if provider == "github" and parsed.hostname.casefold() == "github.com":
        path = ""
    if provider == "gitlab" and path.endswith("/api/v4"):
        path = path[:-7]
    if provider == "gitea" and path.endswith("/api/v1"):
        path = path[:-7]
    return f"https://{parsed.hostname.casefold()}{path}"


def forge_hostname(base_url):
    return (urlsplit(base_url).hostname or "").casefold()


async def validate_public_destination(base_url):
    parsed = urlsplit(base_url)
    try:
        results = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ForgeError("The forge hostname could not be resolved.", code="connection_failed") from exc
    addresses = {item[4][0] for item in results}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ForgeError("The forge hostname resolves to a local or private address.", code="unsafe_destination")


class ForgeClient:
    def __init__(self, provider, base_url, token):
        self.provider = normalize_provider(provider)
        self.base_url = normalize_base_url(base_url, self.provider)
        self.token = str(token or "").strip()
        if not self.token:
            raise ValueError("A forge access token is required.")

    @property
    def api_url(self):
        if self.provider == "github":
            return "https://api.github.com" if forge_hostname(self.base_url) == "github.com" else self.base_url + "/api/v3"
        if self.provider == "gitlab":
            return self.base_url + "/api/v4"
        return self.base_url + "/api/v1"

    def headers(self):
        headers = {"Accept": "application/json", "User-Agent": "SickCogs-GitForge/0.1"}
        if self.provider == "github":
            headers.update({"Authorization": f"Bearer {self.token}", "X-GitHub-Api-Version": "2022-11-28"})
        elif self.provider == "gitlab":
            headers["PRIVATE-TOKEN"] = self.token
        else:
            headers["Authorization"] = f"token {self.token}"
        return headers

    async def request(self, method, path, *, params=None, payload=None):
        await validate_public_destination(self.base_url)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.request(
                    method, self.api_url + path, params=params, json=payload,
                    headers=self.headers(), allow_redirects=False,
                ) as response:
                    if 300 <= response.status < 400:
                        raise ForgeError("The forge returned an unexpected redirect.", code="unexpected_redirect", status=response.status)
                    try:
                        body = await response.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError):
                        body = {}
                    if response.status >= 400:
                        message = self._error_message(body) or f"The forge returned HTTP {response.status}."
                        raise ForgeError(message, code="http_error", status=response.status, retry_after=response.headers.get("Retry-After"))
        except ForgeError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            if method.upper() not in {"GET", "HEAD"}:
                raise ForgeError(
                    "The forge response was interrupted. The result is uncertain; check the repository before retrying.",
                    code="submission_uncertain",
                ) from exc
            raise ForgeError("Could not connect to the forge.", code="connection_failed") from exc
        if not isinstance(body, (dict, list)):
            raise ForgeError("The forge returned an invalid response.", code="invalid_response")
        return body

    @staticmethod
    def _error_message(body):
        if not isinstance(body, dict):
            return None
        message = body.get("message") or body.get("error_description") or body.get("error")
        return str(message)[:300] if isinstance(message, (str, int, float)) else None

    async def test_connection(self):
        return await self.request("GET", "/user")

    def repo_path(self, owner, repo):
        if self.provider == "gitlab":
            project = quote(owner + "/" + repo, safe="")
            return f"/projects/{project}"
        owner = quote(owner, safe="")
        repo = quote(repo, safe="")
        return f"/repos/{owner}/{repo}"

    async def repository(self, owner, repo):
        return await self.request("GET", self.repo_path(owner, repo))

    async def resolve_labels(self, owner, repo, labels):
        labels = [label.strip() for label in labels if label.strip()]
        if not labels or self.provider != "gitea":
            return labels
        body = await self.request("GET", self.repo_path(owner, repo) + "/labels", params={"limit": 100})
        available = {str(item.get("name", "")).casefold(): item.get("id") for item in body if isinstance(item, dict)}
        missing = [label for label in labels if label.casefold() not in available]
        if missing:
            raise ForgeError("Unknown repository labels: " + ", ".join(missing), code="invalid_labels")
        return [available[label.casefold()] for label in labels]

    async def create_issue(self, owner, repo, title, description, labels):
        labels = await self.resolve_labels(owner, repo, labels)
        if self.provider == "gitlab":
            payload = {"title": title, "description": description}
            if labels:
                payload["labels"] = ",".join(labels)
        else:
            payload = {"title": title, "body": description}
            if labels:
                payload["labels"] = labels
        body = await self.request("POST", self.repo_path(owner, repo) + "/issues", payload=payload)
        number = body.get("iid") if self.provider == "gitlab" else body.get("number")
        url = body.get("web_url") if self.provider == "gitlab" else body.get("html_url")
        if not number or not url:
            raise ForgeError("The forge returned an incomplete issue response.", code="invalid_response")
        return CreatedIssue(int(number), str(body.get("title") or title), str(url))

    async def list_issues(self, owner, repo, *, limit=20):
        params = {"state": "open"}
        params["per_page" if self.provider in {"github", "gitlab"} else "limit"] = min(limit, 50)
        if self.provider == "gitea":
            params["type"] = "issues"
        body = await self.request("GET", self.repo_path(owner, repo) + "/issues", params=params)
        issues = []
        for item in body if isinstance(body, list) else []:
            if self.provider == "github" and "pull_request" in item:
                continue
            user = item.get("author") if self.provider == "gitlab" else item.get("user")
            issues.append({
                "id": int(item.get("id") or 0),
                "number": int((item.get("iid") if self.provider == "gitlab" else item.get("number")) or 0),
                "title": str(item.get("title") or "Untitled issue"),
                "url": str((item.get("web_url") if self.provider == "gitlab" else item.get("html_url")) or ""),
                "author": str((user or {}).get("username") or (user or {}).get("login") or "Unknown"),
            })
        return issues[:limit]

    async def list_ci_runs(self, owner, repo, *, limit=10):
        path = self.repo_path(owner, repo)
        if self.provider == "github":
            body = await self.request("GET", path + "/actions/runs", params={"per_page": limit})
            source = body.get("workflow_runs", []) if isinstance(body, dict) else []
        elif self.provider == "gitlab":
            source = await self.request("GET", path + "/pipelines", params={"per_page": limit})
        else:
            body = await self.request("GET", path + "/actions/runs", params={"limit": limit})
            source = body.get("workflow_runs", body.get("runs", [])) if isinstance(body, dict) else []
        return [{
            "id": str(item.get("id")),
            "name": str(item.get("name") or ("GitLab pipeline" if self.provider == "gitlab" else "Actions workflow")),
            "status": str(item.get("conclusion") or item.get("status") or "unknown"),
            "url": str(item.get("web_url") or item.get("html_url") or ""),
            "branch": str(item.get("ref") or item.get("head_branch") or ""),
        } for item in source if isinstance(item, dict)]
