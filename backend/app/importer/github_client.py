"""Read-only GitHub access for strategy import.

Security contract (non-negotiable):

* Only HTTPS to ``github.com`` / ``api.github.com`` /
  ``raw.githubusercontent.com``. Anything else is rejected before any request.
* No clone, no checkout, no subprocess, no code execution — files are fetched
  as plain text through the REST API.
* Hard caps on file count and file size; oversized content is skipped with a
  warning, never truncated silently into analysis.
* Repository content is untrusted data. It is sanitized before it is stored
  as evidence and before it could ever reach an LLM prompt.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

__all__ = [
    "ALLOWED_HOSTS",
    "FetchCoverage",
    "GitHubClient",
    "GitHubError",
    "RepoFile",
    "RepoMeta",
    "parse_repo_url",
]

ALLOWED_HOSTS = frozenset(
    {"github.com", "www.github.com", "api.github.com", "raw.githubusercontent.com"}
)

# Extensions worth fetching for strategy analysis. Everything else is listed
# in the inventory but never downloaded.
STRATEGY_EXTENSIONS = frozenset({".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml"})

MAX_FILES_TO_FETCH = 30
MAX_FILE_BYTES = 200 * 1024
MAX_TREE_ENTRIES = 2000
DEFAULT_MAX_FILES = 12
FETCH_RETRIES = 1

# Wall-clock ceiling for one repository fetch. The per-request timeout bounds a
# single call, not the loop: 30 files x 2 attempts x 15s is 15 minutes of spinner
# before anyone hears back. A fetch that runs out of budget stops and reports the
# files it never tried, which is information the caller can act on.
DEFAULT_FETCH_BUDGET_SECONDS = 120.0


class GitHubError(RuntimeError):
    """Raised for any repository access problem (network, auth, limits)."""


@dataclass(frozen=True)
class RepoMeta:
    owner: str
    repo: str
    ref: str
    default_branch: str
    description: str | None
    license: str | None
    pushed_at: str | None
    html_url: str


@dataclass(frozen=True)
class RepoFile:
    path: str
    size: int
    sha: str
    content: str | None = None
    skipped_reason: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class FetchCoverage:
    """How much of the repository a fetch actually read (docs/05 section 4.1).

    The fetch cap and the byte limit are deliberate, but they used to be invisible:
    a caller could not tell "read 12 files" from "read 12 of 40", and the per-file
    skip reasons were computed here and then dropped. Everything the report needs is
    counted at the moment it happens, because that is the only place the candidate
    list, the cap and the failures are all visible at once.

    ``not_attempted_files`` counts every candidate this fetch never tried, whether
    the cap stopped it or ``max_seconds`` did; ``budget_exhausted`` says which of the
    two happened, because "lower max_files" and "raise the budget" are different
    instructions for the reader.
    """

    candidate_files: int
    candidate_python_files: int
    attempted_files: int
    downloaded_files: int
    skipped_files: int
    skipped_python_files: int
    not_attempted_files: int
    not_attempted_python_files: int
    cap: int
    max_seconds: float | None = None
    budget_exhausted: bool = False

    @property
    def complete(self) -> bool:
        """True only when every candidate file was downloaded."""
        return self.downloaded_files == self.candidate_files

    @property
    def unread_python_files(self) -> int:
        """Python candidates that were never read: rules may live in them."""
        return self.not_attempted_python_files + self.skipped_python_files


def parse_repo_url(url: str) -> tuple[str, str]:
    """Parse ``https://github.com/<owner>/<repo>[.git][/...]`` strictly.

    The path is split on "/" and the first two non-empty segments are owner
    and repo (a trailing ".git" is stripped; anything after the repo such as
    /tree/... or /blob/... is ignored).

    Raises:
        GitHubError: on any scheme/host/path that is not a plain public
            github.com repository URL.
    """

    text = (url or "").strip()
    try:
        parsed = urlparse(text)
    except ValueError as exc:
        raise GitHubError(f"malformed repository URL: {url!r}") from exc

    if parsed.scheme.lower() != "https":
        raise GitHubError("only https:// repository URLs are accepted")
    if parsed.hostname is None or parsed.hostname.lower() not in (
        "github.com",
        "www.github.com",
    ):
        raise GitHubError("only github.com repository URLs are accepted")
    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        raise GitHubError("credentials embedded in the URL are not accepted")

    segments = [part for part in (parsed.path or "").split("/") if part not in ("", ".")]
    if len(segments) < 2 or ".." in segments:
        raise GitHubError(f"cannot parse owner/repo from URL: {url!r}")
    owner, repo = segments[0], segments[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not owner or not repo or owner in {".."} or repo in {".."}:
        raise GitHubError(f"cannot parse owner/repo from URL: {url!r}")
    return owner, repo


class GitHubClient:
    """Minimal read-only client over the public GitHub REST API.

    The two low-level methods (:meth:`get_json`, :meth:`get_text`) are the
    only network touch points; tests override them so no test ever hits the
    network.
    """

    def __init__(
        self,
        token: str | None = None,
        timeout: float = 15.0,
        user_agent: str = "my-quant-lab-importer/1.0",
        total_budget: float = DEFAULT_FETCH_BUDGET_SECONDS,
    ) -> None:
        self._token = token
        self._timeout = timeout
        self._user_agent = user_agent
        self._total_budget = total_budget

    # -- low-level HTTP (override in tests) -------------------------------

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {"Accept": accept, "User-Agent": self._user_agent}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def get_json(self, url: str) -> Any:
        import httpx

        self._assert_allowed(url)
        try:
            response = httpx.get(
                url, headers=self._headers("application/vnd.github+json"), timeout=self._timeout
            )
        except Exception as exc:
            raise GitHubError(f"network error fetching {self._safe_url(url)}: {exc}") from exc
        self._check(response, url)
        try:
            return response.json()
        except Exception as exc:
            raise GitHubError(f"invalid JSON from GitHub API: {self._safe_url(url)}") from exc

    def get_text(self, url: str, *, max_bytes: int = MAX_FILE_BYTES) -> str:
        import httpx

        self._assert_allowed(url)
        try:
            with httpx.stream(
                "GET",
                url,
                headers=self._headers("application/vnd.github.raw"),
                timeout=self._timeout,
            ) as response:
                self._check(response, url)
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise GitHubError(
                            f"file exceeds {max_bytes} bytes, skipped: {self._safe_url(url)}"
                        )
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8", errors="replace")
        except GitHubError:
            raise
        except Exception as exc:
            raise GitHubError(f"network error fetching {self._safe_url(url)}: {exc}") from exc

    # -- high-level API ----------------------------------------------------

    def get_head_commit(self, owner: str, repo: str) -> str:
        """Latest commit SHA on the default branch (used by the watcher)."""

        url = f"https://api.github.com/repos/{owner}/{repo}/commits?per_page=1"
        payload = self.get_json(url)
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return str(payload[0].get("sha") or "")
        return ""

    def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        data = self.get_json(f"https://api.github.com/repos/{owner}/{repo}")
        if not isinstance(data, dict):
            raise GitHubError("unexpected repository response from GitHub API")
        return data

    def get_tree(self, owner: str, repo: str, ref: str) -> list[dict[str, Any]]:
        data = self.get_json(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}?recursive=1"
        )
        if not isinstance(data, dict) or data.get("truncated"):
            raise GitHubError("repository tree is truncated or unavailable; ref too large")
        entries = data.get("tree", [])
        if not isinstance(entries, list) or len(entries) > MAX_TREE_ENTRIES:
            raise GitHubError("repository tree too large to analyse safely")
        return [e for e in entries if isinstance(e, dict) and e.get("type") == "blob"]

    def get_raw_file(self, owner: str, repo: str, path: str, ref: str) -> str:
        return self.get_text(
            f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path.lstrip('/')}"
        )

    def fetch_repository(
        self,
        repo_url: str,
        ref: str | None = None,
        *,
        max_files: int = DEFAULT_MAX_FILES,
        max_seconds: float | None = None,
    ) -> tuple[RepoMeta, list[RepoFile], FetchCoverage]:
        """Fetch metadata + candidate files. Never executes anything.

        Strategy code (``.py``) is fetched first because it carries the rules;
        docs and configs follow only if the cap allows.  Each file is retried
        once on transient network errors; a file that still fails is recorded
        as skipped (with reason) instead of failing the whole analysis.

        ``max_seconds`` (defaults to the client's ``total_budget``) bounds the
        whole loop, not one request: when it runs out the remaining candidates are
        left unattempted and reported as such, so a slow network produces a
        smaller *and honest* report instead of a long silence.

        Returns the metadata, the files, and a :class:`FetchCoverage` that says
        how much of the candidate list the returned files actually cover: the
        skipped entries and the candidates beyond the cap are both counted, so a
        caller can report what was *not* read instead of implying it read it all.
        """

        budget = self._total_budget if max_seconds is None else max_seconds
        started = time.monotonic()
        owner, name = parse_repo_url(repo_url)
        meta_raw = self.get_repo(owner, name)
        default_branch = str(meta_raw.get("default_branch") or "main")
        resolved_ref = ref or default_branch
        license_info = meta_raw.get("license") or {}
        meta = RepoMeta(
            owner=owner,
            repo=str(meta_raw.get("name") or name),
            ref=resolved_ref,
            default_branch=default_branch,
            description=meta_raw.get("description"),
            license=license_info.get("spdx_id") or license_info.get("name"),
            pushed_at=meta_raw.get("pushed_at"),
            html_url=str(meta_raw.get("html_url") or repo_url),
        )

        entries = self.get_tree(owner, name, resolved_ref)
        candidates = [
            e
            for e in entries
            if any(str(e.get("path", "")).lower().endswith(ext) for ext in STRATEGY_EXTENSIONS)
        ]
        # Python files first (rules live there), then docs/configs.
        candidates.sort(key=lambda e: 0 if str(e.get("path", "")).lower().endswith(".py") else 1)

        cap = max(1, min(max_files, MAX_FILES_TO_FETCH))
        files: list[RepoFile] = []
        skipped_python = 0
        attempted = 0
        budget_exhausted = False
        for entry in candidates[:cap]:
            if time.monotonic() - started >= budget:
                budget_exhausted = True
                break
            attempted += 1
            path = str(entry.get("path", ""))
            size = int(entry.get("size") or 0)
            sha = str(entry.get("sha") or "")
            if size > MAX_FILE_BYTES:
                files.append(
                    RepoFile(path=path, size=size, sha=sha, skipped_reason="file too large")
                )
                if path.lower().endswith(".py"):
                    skipped_python += 1
                continue
            content: str | None = None
            failure: str | None = None
            for _ in range(FETCH_RETRIES + 1):
                try:
                    content = self.get_raw_file(owner, name, path, resolved_ref)
                    failure = None
                    break
                except GitHubError as exc:
                    failure = str(exc)
            if content is None:
                files.append(RepoFile(path=path, size=size, sha=sha, skipped_reason=failure))
                if path.lower().endswith(".py"):
                    skipped_python += 1
                continue
            files.append(RepoFile(path=path, size=size, sha=sha, content=content))

        # Candidates the cap already excluded, plus any the budget stopped short of.
        attempted_paths = {f.path for f in files}
        beyond = [e for e in candidates if str(e.get("path", "")) not in attempted_paths]
        skipped_rest = len(beyond)
        if skipped_rest:
            logger.info("skipped %d files beyond the fetch cap or budget", skipped_rest)

        coverage = FetchCoverage(
            candidate_files=len(candidates),
            candidate_python_files=sum(
                1 for e in candidates if str(e.get("path", "")).lower().endswith(".py")
            ),
            attempted_files=attempted,
            downloaded_files=sum(1 for f in files if f.content is not None),
            skipped_files=sum(1 for f in files if f.content is None),
            skipped_python_files=skipped_python,
            not_attempted_files=skipped_rest,
            not_attempted_python_files=sum(
                1 for e in beyond if str(e.get("path", "")).lower().endswith(".py")
            ),
            cap=cap,
            max_seconds=budget,
            budget_exhausted=budget_exhausted,
        )
        return meta, files, coverage

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _assert_allowed(url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        if host not in ALLOWED_HOSTS:
            raise GitHubError(f"refusing to fetch non-allow-listed host: {host!r}")

    def _check(self, response: Any, url: str) -> None:
        """Validate an HTTP status, translating failures to GitHubError."""
        status = getattr(response, "status_code", 200)
        if status == 200:
            return
        if status == 404:
            raise GitHubError(
                f"not found on GitHub: {self._safe_url(url)} "
                "(check owner/repo/ref, or the repository may be private)"
            )
        if status in (403, 429):
            raise GitHubError(
                f"GitHub rate limit or access denied ({status}); configure a token or retry later"
            )
        raise GitHubError(f"GitHub request failed with HTTP {status}: {self._safe_url(url)}")

    @staticmethod
    def _safe_url(url: str) -> str:
        # Never log tokens; raw URLs carry no secrets but keep it short anyway.
        return url[:160]


@dataclass
class FetchPlan:
    """Kept for API stability; describes what *would* be fetched."""

    owner: str
    repo: str
    ref: str
    candidate_paths: list[str] = field(default_factory=list)
