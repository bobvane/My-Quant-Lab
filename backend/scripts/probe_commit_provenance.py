"""Live probe: does an analysis name the revision it read? (ADR-060)

Read-only, and by default entirely offline: the GitHub client is replaced by an
in-memory double that answers like GitHub and remembers every revision it was
asked for. ``--live REPO_URL`` instead asks the real GitHub which commit a ref
points at right now (one extra request, no token needed for public repos).

What this demonstrates:

* the report carries ``commit``, not just the ``ref`` the caller typed;
* the tree and every file are requested by that commit, so a push in the middle
  of a fetch can no longer mix two revisions into one report;
* the watcher already holds a SHA, so pinning costs it no extra request;
* what an import would now record as ``source_commit``.

Usage::

    python backend/scripts/probe_commit_provenance.py
    python backend/scripts/probe_commit_provenance.py --live https://github.com/psf/requests
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.importer.github_client import GitHubClient, GitHubError, parse_repo_url  # noqa: E402

REPO_URL = "https://github.com/acme/strat"
BRANCH = "main"
COMMIT = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
STRATEGY = "fast = ema(close, 5)\nlong_entry = close > fast\n"

_META = {
    "name": "strat",
    "default_branch": BRANCH,
    "description": "demo",
    "license": {"spdx_id": "MIT"},
    "pushed_at": None,
    "html_url": REPO_URL,
}


class RecordingGitHub(GitHubClient):
    """Answers like GitHub and records the revision of every content request."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def get_json(self, url: str):  # type: ignore[override]
        self.urls.append(url)
        if url.endswith(f"/commits/{BRANCH}"):
            return {"sha": COMMIT}
        if "/git/trees/" in url:
            return {
                "tree": [{"path": "strat.py", "type": "blob", "size": len(STRATEGY), "sha": "a"}],
                "truncated": False,
            }
        return dict(_META)

    def get_text(self, url: str, *, max_bytes: int = 1) -> str:  # type: ignore[override]
        self.urls.append(url)
        return STRATEGY


def _report(client: RecordingGitHub) -> int:
    meta, files, _coverage = client.fetch_repository(REPO_URL)

    tree_urls = [u for u in client.urls if "/git/trees/" in u]
    file_urls = [u for u in client.urls if "raw.githubusercontent.com" in u]
    lookups = [u for u in client.urls if "/commits/" in u]

    print(f"asked for ref      : {meta.ref}")
    print(f"report names commit: {meta.commit}")
    print(f"commit lookups     : {len(lookups)} ({lookups[0] if lookups else 'none'})")
    print(f"tree requests      : {tree_urls}")
    print(f"file requests      : {len(file_urls)} -> {file_urls}")
    print(f"files returned     : {[f.path for f in files]}")
    print(f"import would record: source_commit={meta.commit}")

    assert meta.ref == BRANCH, meta.ref
    assert meta.commit == COMMIT, meta.commit  # a branch name is not a revision
    assert lookups and lookups[0].endswith(f"/commits/{BRANCH}"), lookups
    assert tree_urls and all(COMMIT in url for url in tree_urls), tree_urls
    assert file_urls and all(f"/{COMMIT}/" in url for url in file_urls), file_urls
    assert not [u for u in client.urls if f"/trees/{BRANCH}" in u], "read the branch, not the commit"
    assert not [u for u in file_urls if f"/{BRANCH}/" in u], "read the branch, not the commit"

    # The watcher already has a SHA, so the extra lookup does not happen there.
    watcher = RecordingGitHub()
    assert watcher.resolve_commit("acme", "strat", COMMIT) == COMMIT
    assert not [u for u in watcher.urls if "/commits/" in u], watcher.urls
    print("watcher path       : commit passed straight through, no lookup")

    # A ref that resolves to nothing must fail loudly instead of being reported
    # as the revision that was read.
    class NotARef(RecordingGitHub):
        def get_json(self, url: str):  # type: ignore[override]
            if "/commits/" in url:
                return {"message": "Not Found"}
            return super().get_json(url)

    try:
        NotARef().resolve_commit("acme", "strat", "does-not-exist")
    except GitHubError as exc:
        print(f"unresolvable ref   : refused ({exc})")
    else:  # pragma: no cover - the assertion above is the point
        raise AssertionError("an unresolvable ref was accepted as a commit")
    return 0


def _live(repo_url: str) -> int:
    owner, repo = parse_repo_url(repo_url)
    print(f"live  : {owner}/{repo}")
    client = GitHubClient()
    try:
        meta, files, _coverage = client.fetch_repository(repo_url, max_files=1)
    except GitHubError as exc:
        print(f"failed: {exc}")
        return 1
    print(f"  ref    : {meta.ref}")
    print(f"  commit : {meta.commit}")
    print(f"  read   : {[f.path for f in files]}")
    if not meta.commit:
        print("  PROBLEM: the report does not name a commit")
        return 1
    print("  ok     : the report names the revision it read")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        metavar="REPO_URL",
        default=None,
        help="resolve a real repository's ref against api.github.com instead",
    )
    args = parser.parse_args()
    if args.live:
        return _live(args.live)
    return _report(RecordingGitHub())


if __name__ == "__main__":
    raise SystemExit(main())
