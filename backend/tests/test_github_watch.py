"""GitHub watcher: re-import on new commit (docs/05 §7, Phase 6)."""

from __future__ import annotations

from app.data.github_source_service import record_snapshot
from app.domain.models import GitHubSnapshot, GitHubSource, Strategy, StrategyVersion
from app.importer.extract import AnalysisResult
from app.importer.github_client import FetchCoverage
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


def _complete_coverage(**overrides) -> FetchCoverage:
    """A fetch that read every candidate file, i.e. the only case that imports."""

    fields = {
        "candidate_files": 3,
        "candidate_python_files": 3,
        "attempted_files": 3,
        "downloaded_files": 3,
        "skipped_files": 0,
        "skipped_python_files": 0,
        "not_attempted_files": 0,
        "not_attempted_python_files": 0,
        "cap": 30,
    }
    fields.update(overrides)
    return FetchCoverage(**fields)


def test_bump_version_scheme() -> None:
    assert _bump_version("1.0.0") == "1.0.1"
    assert _bump_version("1.0.8") == "1.0.9"
    assert _bump_version("1.0.9") == "1.1.0"
    assert _bump_version("1.9.9") == "2.0.0"


def _install_fake_client(monkeypatch, head: str, coverage: FetchCoverage | None = None):
    import app.importer as importer

    report = coverage or _complete_coverage()

    class _FakeClient:
        def __init__(self) -> None:
            pass

        def get_head_commit(self, owner: str, repo: str) -> str:
            return head

        def fetch_repository(self, repo_url, ref=None, *, max_files=30):  # noqa: ANN001
            return (object(), [], report)

    monkeypatch.setattr(importer, "GitHubClient", _FakeClient)
    monkeypatch.setattr(importer, "analyze_repository_files", lambda files: AnalysisResult())
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
    assert source.last_import_status == "imported"
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
    assert source.last_import_status == "unchanged"
    assert db_session.query(GitHubSnapshot).count() == 0


def test_a_new_commit_with_an_unchanged_dsl_is_stored_as_no_change(db_session, monkeypatch) -> None:
    """A fetched commit that changes nothing is not the same as "nothing to check".

    Both used to be stored as "checked", so the row could not tell a user whether
    the watcher had actually fetched anything (ADR-058).
    """

    _install_fake_client(monkeypatch, "newsha")
    source = GitHubSource(repository_url=_REPO, current_commit="oldsha", is_watched=True)
    db_session.add(source)
    db_session.flush()
    strategy = Strategy(name="Demo", slug="demo-strategy", source_url=_REPO)
    db_session.add(strategy)
    db_session.flush()
    db_session.add(
        StrategyVersion(
            strategy_id=strategy.id,
            version="1.0.0",
            dsl_json=dict(_DRAFT),
            immutable_hash="x" * 64,
        )
    )
    db_session.commit()

    assert check_source(db_session, source) == "no_change"
    assert source.last_import_status == "no_change"
    assert source.current_commit == "newsha"  # the commit was read, nothing changed
    db_session.commit()
    assert db_session.query(StrategyVersion).count() == 1
    snapshot = db_session.query(GitHubSnapshot).one()
    assert snapshot.extraction_json["imported"] is False


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


def _seed_importable_source(db_session) -> GitHubSource:
    source = GitHubSource(repository_url=_REPO, current_commit="oldsha", is_watched=True)
    db_session.add(source)
    db_session.flush()
    strategy = Strategy(name="Demo", slug="demo-strategy", source_url=_REPO)
    db_session.add(strategy)
    db_session.flush()
    db_session.add(
        StrategyVersion(
            strategy_id=strategy.id,
            version="1.0.0",
            dsl_json={**_DRAFT, "execution": {"fill_model": "next_bar_open", "fee_bps": 0}},
            immutable_hash="x" * 64,
        )
    )
    db_session.commit()
    return source


def test_unread_python_files_block_an_unattended_import(db_session, monkeypatch) -> None:
    """A partial read must not silently replace a strategy version (ADR-056).

    The watcher imports without a human in the loop. If a Python file was left
    unread (beyond the fetch cap here), the draft is built from partial evidence
    and importing it would drop whatever rules that file holds.
    """

    coverage = _complete_coverage(
        candidate_files=40,
        candidate_python_files=35,
        attempted_files=30,
        downloaded_files=30,
        not_attempted_files=10,
        not_attempted_python_files=5,
    )
    _install_fake_client(monkeypatch, "newsha", coverage)
    source = _seed_importable_source(db_session)

    assert check_source(db_session, source) == "incomplete"
    assert source.last_import_status == "incomplete"
    assert source.current_commit == "newsha"
    assert db_session.query(StrategyVersion).count() == 1  # nothing imported
    db_session.commit()
    snapshot = db_session.query(GitHubSnapshot).one()
    assert snapshot.extraction_json["reason"] == "incomplete_analysis"
    assert snapshot.extraction_json["coverage"]["unread_python_files"] == 5
    assert snapshot.extraction_json["transient"] is False  # a cap-limited read is stable
    assert any("never fetched" in w for w in snapshot.extraction_json["warnings"])


def test_unread_non_python_files_do_not_block_an_import(db_session, monkeypatch) -> None:
    """Skipped READMEs and configs are reported, but they carry no rules."""

    coverage = _complete_coverage(
        candidate_files=40,
        candidate_python_files=3,
        attempted_files=30,
        downloaded_files=30,
        not_attempted_files=10,
        not_attempted_python_files=0,
    )
    _install_fake_client(monkeypatch, "newsha", coverage)
    source = _seed_importable_source(db_session)

    assert check_source(db_session, source) == "imported"
    assert source.last_import_status == "imported"
    assert db_session.query(StrategyVersion).count() == 2
    db_session.commit()
    snapshot = db_session.query(GitHubSnapshot).one()
    assert snapshot.extraction_json["coverage"]["not_attempted_files"] == 10
    assert snapshot.extraction_json["coverage"]["complete"] is False


def test_re_checking_a_commit_refreshes_its_snapshot(db_session, monkeypatch) -> None:
    """One row per (source, commit): a re-check must not abort the run (ADR-058).

    A commit is legitimately looked at more than once - a human imports it and the
    watcher then checks it, or a check that ran out of its budget is retried. The
    second insert raised ``IntegrityError: UNIQUE constraint failed:
    github_snapshots.source_id, github_snapshots.commit``, which failed the whole
    scheduled run and lost every other source's outcome with it.
    """

    coverage = _complete_coverage(
        candidate_files=40,
        candidate_python_files=35,
        attempted_files=4,
        downloaded_files=4,
        not_attempted_files=36,
        not_attempted_python_files=31,
        budget_exhausted=True,
    )
    _install_fake_client(monkeypatch, "newsha", coverage)
    source = _seed_importable_source(db_session)
    record_snapshot(
        db_session,
        source.id,
        "newsha",
        "m" * 64,
        {"imported": True, "reason": "manual_import"},
    )
    db_session.commit()

    assert check_source(db_session, source) == "incomplete"
    db_session.commit()
    snapshots = db_session.query(GitHubSnapshot).all()
    assert len(snapshots) == 1
    assert snapshots[0].extraction_json["reason"] == "incomplete_analysis"
    assert snapshots[0].extraction_json["transient"] is True


def test_the_task_summary_names_the_statuses_the_run_produced(db_session, monkeypatch) -> None:
    """The summary is derived from the run, not a hardcoded list (ADR-058).

    The old summary only knew about ``checked``/``imported``/``incomplete``/
    ``error``, so a run in which nothing had changed reported ``checked`` with no
    way to tell that nothing had changed.
    """

    from contextlib import contextmanager

    import app.workers.tasks as tasks

    _install_fake_client(monkeypatch, "oldsha")
    for slug in ("demo-a", "demo-b"):
        db_session.add(
            GitHubSource(
                repository_url=f"https://github.com/bobvane/{slug}",
                current_commit="oldsha",
                is_watched=True,
            )
        )
    db_session.commit()

    @contextmanager
    def _scope():  # noqa: ANN202 - test double for session_scope()
        yield db_session

    monkeypatch.setattr(tasks, "session_scope", _scope)

    summary = tasks.check_github_sources()

    assert summary["checked"] == 2
    assert summary["unchanged"] == 2


def test_a_fetch_that_ran_out_of_time_is_retried_next_run(db_session, monkeypatch) -> None:
    """A transient gap must not be marked as seen (ADR-057).

    A read limited by the cap will read exactly as much next time, so that gap is
    recorded against the commit. A read that ran out of its time budget may
    succeed on a quieter network, so the source must stay on the old commit and
    try again instead of abandoning the update forever.
    """

    coverage = _complete_coverage(
        candidate_files=40,
        candidate_python_files=35,
        attempted_files=4,
        downloaded_files=4,
        not_attempted_files=36,
        not_attempted_python_files=31,
        max_seconds=120.0,
        budget_exhausted=True,
    )
    _install_fake_client(monkeypatch, "newsha", coverage)
    source = _seed_importable_source(db_session)

    assert check_source(db_session, source) == "incomplete"
    assert source.last_import_status == "incomplete"
    assert source.current_commit == "oldsha"  # not seen: the next run retries
    assert db_session.query(StrategyVersion).count() == 1
    db_session.commit()
    snapshot = db_session.query(GitHubSnapshot).one()
    assert snapshot.extraction_json["transient"] is True
    assert any("stopped after" in w for w in snapshot.extraction_json["warnings"])
