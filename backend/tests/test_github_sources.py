"""GitHub source/snapshot persistence on import (docs/05 §7, docs/11)."""

from __future__ import annotations

_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "g", "name": "G", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}


def test_import_persists_github_source_and_snapshot(client, db_session) -> None:
    from app.domain.models import GitHubSource

    response = client.post(
        "/api/v1/importer/github/import",
        json={
            "repo_url": "https://github.com/bobvane/demo",
            "name": "GIT Test",
            "version": "1.0.0",
            "dsl": _DSL,
            "ref": "abc123",
        },
    )
    assert response.status_code == 201, response.text

    source = db_session.query(GitHubSource).one()
    assert source.repository_url == "https://github.com/bobvane/demo"
    assert source.current_commit == "abc123"

    listed = client.get("/api/v1/importer/github/sources").json()
    assert listed[0]["repository_url"] == "https://github.com/bobvane/demo"

    details = client.get(f"/api/v1/importer/github/sources/{source.id}").json()
    assert details["default_branch"] == "main"

    snapshots = client.get(f"/api/v1/importer/github/sources/{source.id}/snapshots").json()
    assert len(snapshots) == 1
    assert snapshots[0]["commit"] == "abc123"
    # A snapshot has to explain itself: the reason lives in the extraction, and
    # dropping it left "why did this check end this way?" unanswerable (ADR-058).
    assert snapshots[0]["extraction"]["imported"] is True
    assert snapshots[0]["extraction"]["reason"] == "manual_import"


def test_import_missing_source_404(client) -> None:
    assert client.get("/api/v1/importer/github/sources/999").status_code == 404


def test_a_second_record_for_the_same_commit_updates_the_first(db_session) -> None:
    """Snapshots are unique per (source, commit), so later observations refresh.

    Inserting a second row raised ``IntegrityError: UNIQUE constraint failed:
    github_snapshots.source_id, github_snapshots.commit`` (ADR-058).
    """

    from app.data.github_source_service import record_snapshot
    from app.domain.models import GitHubSnapshot, GitHubSource

    source = GitHubSource(repository_url="https://github.com/bobvane/demo", current_commit="abc")
    db_session.add(source)
    db_session.commit()

    first = record_snapshot(
        db_session, source.id, "abc", "h" * 64, {"imported": True, "reason": "manual_import"}
    )
    db_session.commit()
    second = record_snapshot(
        db_session, source.id, "abc", "j" * 64, {"imported": False, "reason": "incomplete_analysis"}
    )
    db_session.commit()

    assert first.id == second.id
    assert db_session.query(GitHubSnapshot).count() == 1
    stored = db_session.query(GitHubSnapshot).one()
    assert stored.content_hash == "j" * 64
    assert stored.extraction_json["reason"] == "incomplete_analysis"


def test_check_source_now_reports_update(client, db_session, monkeypatch) -> None:
    import app.importer as importer
    from app.domain.models import GitHubSource

    class _FakeClient:
        def get_head_commit(self, owner, repo):  # noqa: ANN001
            return "newsha"

    monkeypatch.setattr(importer, "GitHubClient", _FakeClient)
    source = GitHubSource(repository_url="https://github.com/bobvane/demo", current_commit="old")
    db_session.add(source)
    db_session.commit()

    body = client.get(f"/api/v1/importer/github/sources/{source.id}/check").json()
    assert body["has_update"] is True
    assert body["head"] == "newsha"

    assert client.get("/api/v1/importer/github/sources/999/check").status_code == 404
