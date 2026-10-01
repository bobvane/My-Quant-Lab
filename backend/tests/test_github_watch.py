"""GitHub watcher: re-import on new commit (docs/05 §7, Phase 6)."""

from __future__ import annotations

from app.domain.models import GitHubSnapshot, GitHubSource, Strategy, StrategyVersion
from app.workers.tasks import _bump_version, check_source

_REPO = "https://github.com/bobvane/demo"
_DRAFT = {
    "schema_version": "1.0",
    "strategy": {"id": "d", "name": "D", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}


def test_bump_version_scheme() -> None:
    assert _bump_version("1.0.0") == "1.0.1"
    assert _bump_version("1.0.8") == "1.0.9"
    assert _bump_version("1.0.9") == "1.1.0"
    assert _bump_version("1.9.9") == "2.0.0"


def _install_fake_client(monkeypatch, head: str):
    import app.importer as importer

    class _FakeClient:
        def __init__(self) -> None:
            pass

        def get_head_commit(self, owner: str, repo: str) -> str:
            return head

        def fetch_repository(self, repo_url, ref=None, *, max_files=30):  # noqa: ANN001
            return (object(), [])

    monkeypatch.setattr(importer, "GitHubClient", _FakeClient)
    monkeypatch.setattr(importer, "analyze_repository_files", lambda files: None)
    monkeypatch.setattr(importer, "build_draft_dsl", lambda meta, findings: (_DRAFT, []))


def test_check_source_imports_new_commit(db_session, monkeypatch) -> None:
    _install_fake_client(monkeypatch, "newsha")
    source = GitHubSource(repository_url=_REPO, current_commit="oldsha", is_watched=True)
    db_session.add(source)
    db_session.flush()
    strategy = Strategy(name="Demo", slug="demo-strategy", source_url=_REPO)
    db_session.add(strategy)
    db_session.flush()
    old_dsl = {**_DRAFT, "execution": {"fill_model": "next_bar_open", "fee_bps": 0}}
    db_session.add(
        StrategyVersion(
            strategy_id=strategy.id, version="1.0.0", dsl_json=old_dsl, immutable_hash="x" * 64
        )
    )
    db_session.commit()

    assert check_source(db_session, source) == "imported"
    db_session.commit()
    versions = db_session.query(StrategyVersion).all()
    assert len(versions) == 2
    new_version = versions[-1]
    assert new_version.version == "1.0.1"
    assert new_version.source_commit == "newsha"
    assert source.current_commit == "newsha"
    assert db_session.query(GitHubSnapshot).count() == 1


def test_check_source_unchanged_when_commit_same(db_session, monkeypatch) -> None:
    _install_fake_client(monkeypatch, "oldsha")
    source = GitHubSource(repository_url=_REPO, current_commit="oldsha", is_watched=True)
    db_session.add(source)
    db_session.commit()

    assert check_source(db_session, source) == "unchanged"
    assert db_session.query(GitHubSnapshot).count() == 0


def test_check_source_error_on_failure(db_session, monkeypatch) -> None:
    import app.importer as importer

    class _Broken:
        def get_head_commit(self, owner, repo):  # noqa: ANN001
            raise RuntimeError("network down")

    monkeypatch.setattr(importer, "GitHubClient", _Broken)
    source = GitHubSource(repository_url=_REPO, current_commit=None, is_watched=True)
    db_session.add(source)
    db_session.commit()

    assert check_source(db_session, source) == "error"
    assert source.last_import_status == "error"
