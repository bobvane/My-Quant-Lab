"""GitHub watcher: re-import on new commit (docs/05 §7, Phase 6)."""

from __future__ import annotations

import copy

from app.data.github_source_service import record_snapshot
from app.domain.models import GitHubSnapshot, GitHubSource, Strategy, StrategyVersion
from app.importer.extract import AnalysisResult, SkippedFile
from app.importer.github_client import FetchCoverage
from app.workers.tasks import check_source

_REPO = "https://github.com/bobvane/demo"
_DRAFT = {
    "schema_version": "1.0",
    "strategy": {"id": "d", "name": "D", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}
# What the importer actually produces: exit rules are never invented, so the
# draft has an empty exit block and the DSL parser rejects it (ADR-062).
_EXIT_LESS_DRAFT = {**_DRAFT, "exit": {}}


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


def _install_fake_client(
    monkeypatch,
    head: str,
    coverage: FetchCoverage | None = None,
    findings: AnalysisResult | None = None,
    draft: dict | None = None,
):
    import app.importer as importer

    report = coverage or _complete_coverage()
    drafted = _DRAFT if draft is None else draft

    class _FakeClient:
        def __init__(self) -> None:
            pass

        def get_head_commit(self, owner: str, repo: str) -> str:
            return head

        def fetch_repository(self, repo_url, ref=None, *, max_files=30):  # noqa: ANN001
            return (object(), [], report)

    monkeypatch.setattr(importer, "GitHubClient", _FakeClient)
    monkeypatch.setattr(
        importer, "analyze_repository_files", lambda files: findings or AnalysisResult()
    )
    monkeypatch.setattr(importer, "build_draft_dsl", lambda meta, findings: (drafted, []))


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


def test_a_python_file_that_did_not_parse_blocks_an_unattended_import(
    db_session, monkeypatch
) -> None:
    """Read is not the same claim as understood (ADR-059).

    A file can download successfully and still fail to parse. It used to be
    counted in ``files_parsed``, so ``coverage["complete"]`` stayed true, no
    warning said anything, and the watcher imported a draft with every rule that
    file declares silently missing.
    """

    findings = AnalysisResult(
        files_unparsed=[
            SkippedFile(
                path="legacy.py",
                reason=(
                    "file does not parse as Python (SyntaxError): "
                    "Missing parentheses in call to 'print'"
                ),
            )
        ]
    )
    _install_fake_client(monkeypatch, "newsha", findings=findings)
    source = _seed_importable_source(db_session)

    assert check_source(db_session, source) == "incomplete"
    assert source.last_import_status == "incomplete"
    assert db_session.query(StrategyVersion).count() == 1  # nothing imported
    # Not parsing is structural, not transient: re-reading changes nothing, so
    # the commit is marked as seen instead of being retried forever (ADR-057).
    assert source.current_commit == "newsha"
    db_session.commit()
    snapshot = db_session.query(GitHubSnapshot).one()
    assert snapshot.extraction_json["reason"] == "unparseable_python"
    assert snapshot.extraction_json["transient"] is False
    assert snapshot.extraction_json["files_unparsed"][0]["path"] == "legacy.py"
    assert any("did not parse" in w for w in snapshot.extraction_json["warnings"])


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


def test_the_watcher_numbers_versions_with_the_ledger() -> None:
    """One numbering rule per ledger, not a second one for the watcher (ADR-061).

    ``_bump_version`` used the project's *release* cadence (a tenth patch rolls the
    minor on), so the watcher wrote the same column ``strategy_service`` numbers,
    with different arithmetic: 1.0.9 became 1.1.0 for the watcher and 1.0.10 for
    the ledger. It is gone; the ledger decides.
    """

    from app.data.strategy_service import next_version

    assert next_version([]) == "1.0.0"
    assert next_version(["1.0.9"]) == "1.0.10"
    # Integer comparison, not string comparison: "1.9.0" sorts above "1.10.0" as
    # text, so the next version used to be 1.9.1 while another strategy already
    # had 1.10.0.
    assert next_version(["1.9.0"]) == "1.9.1"
    assert next_version(["1.9.0", "1.10.0"]) == "1.10.1"


def test_the_import_gate_names_what_blocks_an_unattended_import() -> None:
    """One gate for both import paths, and it says which rule refused (ADR-062).

    ``parse_spec`` catches a draft that cannot be a strategy at all; a draft that
    parses but fails validation was the quiet case, because the validator only
    records it and the ledger would still store an ``invalid`` row.
    """

    from app.data.strategy_service import strategy_dsl_problem

    assert strategy_dsl_problem(_DRAFT) is None
    assert strategy_dsl_problem(_EXIT_LESS_DRAFT) == (
        "invalid strategy DSL -> : Value error, at least one exit rule is required"
    )

    future = copy.deepcopy(_DRAFT)
    future["entry"] = {"long": {"all": [{"op": "gt", "left": "close", "right": "future_close"}]}}
    problem = strategy_dsl_problem(future) or ""
    assert problem.startswith("invalid strategy DSL -> entry.long.right:")
    assert "unavailable future data" in problem


def test_an_exit_less_draft_is_refused_instead_of_crashing(db_session, monkeypatch) -> None:
    """The watcher must be able to say "a human has to finish this" (ADR-062).

    No exit rule is ever invented for a draft, so every draft the importer builds
    fails ``parse_spec``. Handing it to ``create_strategy_version`` raised out of
    ``check_source``: the scheduled run died, no snapshot was written, no status
    was stored, and every source after this one was never checked. The refusal is
    now the recorded outcome.
    """

    _install_fake_client(monkeypatch, "newsha", draft=_EXIT_LESS_DRAFT)
    source = _seed_importable_source(db_session)

    assert check_source(db_session, source) == "review_required"
    assert source.last_import_status == "review_required"
    assert source.pending_review_commit == "newsha"
    assert source.current_commit == "newsha"  # the same commit yields the same verdict
    assert db_session.query(StrategyVersion).count() == 1  # nothing was imported
    db_session.commit()
    snapshot = db_session.query(GitHubSnapshot).one()
    assert snapshot.extraction_json["reason"] == "requires_review"
    assert "at least one exit rule is required" in snapshot.extraction_json["detail"]
    assert snapshot.extraction_json["imported"] is False
    assert snapshot.extraction_json["transient"] is False


def test_a_pending_review_survives_the_next_run_without_refetching(db_session, monkeypatch) -> None:
    """The wait is remembered, and asking again costs one request, not a fetch.

    ``current_commit`` advances when a commit is refused, so a plain
    "head == current_commit" check would report ``unchanged`` and erase the fact
    that a review is outstanding (ADR-062).
    """

    import app.importer as importer

    class _NoFetch:
        def __init__(self) -> None:
            pass

        def get_head_commit(self, owner: str, repo: str) -> str:
            return "newsha"

        def fetch_repository(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("a commit waiting for review must not be re-fetched")

    monkeypatch.setattr(importer, "GitHubClient", _NoFetch)
    source = GitHubSource(
        repository_url=_REPO,
        current_commit="newsha",
        pending_review_commit="newsha",
        is_watched=True,
    )
    db_session.add(source)
    db_session.commit()

    assert check_source(db_session, source) == "review_required"
    assert source.last_import_status == "review_required"
    assert db_session.query(GitHubSnapshot).count() == 0
    assert db_session.query(StrategyVersion).count() == 0


def test_a_new_commit_clears_a_review_that_was_never_done(db_session, monkeypatch) -> None:
    """A newer commit supersedes the one nobody reviewed, and is imported normally."""

    _install_fake_client(monkeypatch, "newer")
    source = _seed_importable_source(db_session)
    source.pending_review_commit = "newsha"
    db_session.commit()

    assert check_source(db_session, source) == "imported"
    assert source.pending_review_commit is None
    assert source.current_commit == "newer"


def test_an_import_that_raises_is_recorded_not_raised(db_session, monkeypatch) -> None:
    """An unexpected failure is an outcome, not the end of the scheduled run."""

    import app.data.strategy_service as strategy_service

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("ledger exploded")

    _install_fake_client(monkeypatch, "newsha")
    source = _seed_importable_source(db_session)
    monkeypatch.setattr(strategy_service, "create_strategy_version", _boom)

    assert check_source(db_session, source) == "error"
    assert source.last_import_status == "error"
    assert source.current_commit == "oldsha"  # not seen: the next run retries
    db_session.commit()
    snapshot = db_session.query(GitHubSnapshot).one()
    assert snapshot.extraction_json["reason"] == "import_failed"
    assert "ledger exploded" in snapshot.extraction_json["detail"]


def test_a_source_with_nothing_linked_says_so(db_session, monkeypatch) -> None:
    """Watching a repository nobody imported is not "the DSL did not change"."""

    _install_fake_client(monkeypatch, "newsha")
    source = GitHubSource(repository_url=_REPO, current_commit="oldsha", is_watched=True)
    db_session.add(source)
    db_session.commit()

    assert check_source(db_session, source) == "no_change"
    db_session.commit()
    snapshot = db_session.query(GitHubSnapshot).one()
    assert snapshot.extraction_json["reason"] == "no_linked_strategy"
    assert snapshot.extraction_json["versions"] == []


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


def test_a_source_that_crashes_does_not_stop_the_run(db_session, monkeypatch) -> None:
    """The beat task always returns a summary, with the crash counted (ADR-062).

    A handler that raises used to end the run: the sources after the broken one
    were never examined and the operator got an exception instead of a report.
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

    real = tasks.check_source
    calls: list[str] = []

    def _sometimes_crash(db, source):  # noqa: ANN001, ANN202
        calls.append(source.repository_url)
        if source.repository_url.endswith("demo-a"):
            raise RuntimeError("boom")
        return real(db, source)

    @contextmanager
    def _scope():  # noqa: ANN202 - test double for session_scope()
        yield db_session

    monkeypatch.setattr(tasks, "session_scope", _scope)
    monkeypatch.setattr(tasks, "check_source", _sometimes_crash)

    summary = tasks.check_github_sources()

    assert summary["checked"] == 2
    assert summary["error"] == 1
    assert summary["unchanged"] == 1
    assert len(calls) == 2  # the second source was still checked


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
