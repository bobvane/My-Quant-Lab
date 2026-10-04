"""Source ingestion endpoints and snapshot storage (Step 3.5).

The network is never touched here. What is under test is the contract around it:
what a refusal looks like, that a refusal is still stored, that the full document
never comes back out, that the two dimensions (``snapshot_status``,
``parse_status``) stay independent, and that a second fetch of the same URL writes a
second observation instead of overwriting the first.

The fetch/parse layers have their own suites (``test_source_fetch.py``,
``test_source_parse.py``); the recipes they were given to produce each kind of
material are replayed here through the router so the endpoint, the storage and the
response shape are exercised as one path.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.api.routers import sources as sources_router
from app.data import source_snapshot_service as snapshots
from app.domain.models import AISourceSnapshot, AITask
from app.sources import ingest
from app.sources.ingest import IngestedMaterial

API = "/api/v1"


def retained(**overrides) -> IngestedMaterial:
    """Material as :func:`app.sources.ingest.ingest_url` returns it for a read page."""

    text = overrides.pop("text", "Alpha paragraph.\n\nBeta paragraph.")
    fields = {
        "kind": "url",
        "status": ingest.RETAINED,
        "parse_status": "ok",
        "uri": "https://example.test/page",
        "final_uri": "https://example.test/page",
        "text": text,
        "chars_read": len(text),
        "retained_chars": len(text),
        "source_hash": hashlib.sha256(b"<html>page</html>").hexdigest(),
        "text_hash": hashlib.sha256(text.encode()).hexdigest(),
        "size_bytes": 18,
        "http_status": 200,
        "content_type": "text/html",
        "parser": "stdlib.html.parser",
        "parser_version": "1.0",
        "robots_ok": True,
        "metadata": {"redirects": [], "redirect_count": 0},
        "fetched_at": datetime(2026, 2, 1, 12, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return IngestedMaterial(**fields)


def refused(status: str, code: str, message: str = "refused") -> IngestedMaterial:
    return IngestedMaterial(
        kind="url",
        status=status,
        uri="http://10.0.0.7/secret",
        code=code,
        message=message,
        metadata={"error_code": code},
    )


def row_count(db) -> int:
    return len(list(db.execute(select(AISourceSnapshot)).scalars()))


def test_url_endpoint_stores_the_observation_it_returns(client, db_session, monkeypatch):
    monkeypatch.setattr(sources_router, "ingest_url", lambda uri, **kw: retained(uri=uri))

    response = client.post(
        f"{API}/ai/sources/url",
        json={"uri": "https://example.test/page", "source_ref": "docs", "label": "Docs page"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_status"] == "retained"
    assert body["parse_status"] == "ok"
    assert body["source_kind"] == "url"
    assert body["source_hash"] == retained().source_hash
    assert body["text_hash"] == retained().text_hash
    assert body["retention"] == {"policy": "excerpt", "retained_chars": 33, "truncated": False}
    assert body["excerpt"] == ["Alpha paragraph.", "Beta paragraph."]
    assert body["source_ref"] == "docs"
    assert body["label"] == "Docs page"
    assert body["error_code"] is None

    row = db_session.get(AISourceSnapshot, body["snapshot_id"])
    assert row is not None
    assert row.status == "retained"
    assert row.parse_status == "ok"
    assert row.excerpt == "Alpha paragraph.\n\nBeta paragraph."


def test_response_describes_the_document_without_republishing_it(client, monkeypatch):
    long_text = "x" * 900
    monkeypatch.setattr(
        sources_router,
        "ingest_url",
        lambda uri, **kw: retained(text=long_text[:500], chars_read=900, truncated=True),
    )

    body = client.post(f"{API}/ai/sources/url", json={"uri": "https://example.test/long"}).json()

    assert body["chars_read"] == 900
    assert body["retention"]["retained_chars"] == 500
    assert body["retention"]["truncated"] is True
    assert sum(len(piece) for piece in body["excerpt"]) == 500
    assert "full_text" not in body
    assert long_text not in str(body)


def test_blocked_source_is_refused_as_422_and_still_stored(client, db_session, monkeypatch):
    monkeypatch.setattr(
        sources_router,
        "ingest_url",
        lambda uri, **kw: refused(ingest.BLOCKED, "address_not_allowed", "the address is private"),
    )

    response = client.post(f"{API}/ai/sources/url", json={"uri": "http://10.0.0.7/secret"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["snapshot_status"] == "blocked"
    assert detail["code"] == "address_not_allowed"
    assert detail["snapshot_id"] is not None

    row = db_session.get(AISourceSnapshot, detail["snapshot_id"])
    assert row is not None
    assert row.status == "blocked"
    assert row.parse_status == "not_parsed"
    assert row.source_hash is None
    assert row.error == "the address is private"


def test_unreachable_source_is_502_not_a_policy_refusal(client, db_session, monkeypatch):
    monkeypatch.setattr(
        sources_router,
        "ingest_url",
        lambda uri, **kw: refused(ingest.FETCH_FAILED, "timeout", "the request timed out"),
    )

    response = client.post(f"{API}/ai/sources/url", json={"uri": "https://example.test/slow"})

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "timeout"
    assert row_count(db_session) == 1


@pytest.mark.parametrize(
    ("uri", "code"),
    [
        ["http://127.0.0.1/", "address_not_allowed"],
        ["file:///etc/passwd", "scheme_not_allowed"],
        ["http://[::1]/", "address_not_allowed"],
    ],
)
def test_real_guard_refuses_before_any_socket_exists(client, db_session, uri, code):
    """No stub here: the endpoint's own guard is what answers, and nothing is fetched."""

    response = client.post(f"{API}/ai/sources/url", json={"uri": uri})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["snapshot_status"] == "blocked"

    row = db_session.get(AISourceSnapshot, detail["snapshot_id"])
    assert row is not None and row.status == "blocked" and row.parse_status == "not_parsed"


def test_retention_and_license_rules_are_400_and_do_not_fetch(client, db_session):
    bad_policy = client.post(
        f"{API}/ai/sources/url",
        json={"uri": "https://example.test/page", "retention": "forever"},
    )
    # "forever" is not a policy Literal, so pydantic answers for the request shape.
    assert bad_policy.status_code == 422

    missing_note = client.post(
        f"{API}/ai/sources/url",
        json={"uri": "http://127.0.0.1/", "retention": "full"},
    )
    assert missing_note.status_code == 400
    assert "license note" in missing_note.json()["detail"]
    assert row_count(db_session) == 0


def test_pdf_endpoint_requires_exactly_one_payload(client, db_session):
    neither = client.post(f"{API}/ai/sources/pdf", json={})
    both = client.post(
        f"{API}/ai/sources/pdf",
        json={"uri": "https://example.test/a.pdf", "content_base64": "JVBERi0="},
    )

    assert neither.status_code == 400
    assert both.status_code == 400
    assert "exactly one" in both.json()["detail"]
    assert row_count(db_session) == 0


def test_inline_pdf_is_decoded_and_filed_under_its_filename(client, db_session, monkeypatch):
    seen: dict[str, object] = {}

    def fake_pdf(value: str, **kwargs) -> IngestedMaterial:
        seen["value"] = value
        seen.update(kwargs)
        return retained(kind="pdf", parse_status="ok", content_type="application/pdf")

    monkeypatch.setattr(sources_router, "ingest_pdf_base64", fake_pdf)
    payload = base64.b64encode(b"%PDF-1.4 fake").decode()

    response = client.post(
        f"{API}/ai/sources/pdf",
        json={"content_base64": payload, "filename": "notes.pdf", "label": "My notes"},
    )

    assert response.status_code == 200
    assert seen["value"] == payload
    assert seen["filename"] == "notes.pdf"
    assert seen["retention"] is None
    assert response.json()["parse_status"] == "ok"


def test_inline_pdf_rejects_junk_without_decoding_it_into_the_parser(client, db_session):
    response = client.post(f"{API}/ai/sources/pdf", json={"content_base64": "not base64!!"})

    assert response.status_code == 400
    assert "base64" in response.json()["detail"]
    assert row_count(db_session) == 0


def test_pdf_by_url_is_restricted_to_pdf_content_types(client, db_session, monkeypatch):
    seen: dict[str, object] = {}

    def fake_pdf_uri(uri: str, **kwargs) -> IngestedMaterial:
        seen["uri"] = uri
        seen.update(kwargs)
        return retained(kind="pdf", content_type="application/pdf")

    monkeypatch.setattr(sources_router, "ingest_pdf_uri", fake_pdf_uri)
    monkeypatch.setattr(
        sources_router,
        "ingest_url",
        lambda *a, **kw: pytest.fail("a PDF URL must not go through the page path"),
    )

    response = client.post(f"{API}/ai/sources/pdf", json={"uri": "https://example.test/paper.pdf"})

    assert response.status_code == 200
    assert seen["uri"] == "https://example.test/paper.pdf"


def test_a_pdf_with_no_text_layer_is_retained_and_says_so(client, db_session, monkeypatch):
    """``unsupported`` is an answer, not a failure: the observation is still kept."""

    monkeypatch.setattr(
        sources_router,
        "ingest_pdf_uri",
        lambda uri, **kw: retained(
            kind="pdf",
            parse_status="unsupported",
            text="",
            chars_read=0,
            retained_chars=0,
            code="parse_unsupported",
            message="the PDF has no text layer; this version has no OCR",
        ),
    )

    response = client.post(f"{API}/ai/sources/pdf", json={"uri": "https://example.test/scan.pdf"})

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_status"] == "retained"
    assert body["parse_status"] == "unsupported"
    assert body["excerpt"] == []
    row = db_session.get(AISourceSnapshot, body["snapshot_id"])
    assert row is not None and row.status == "retained" and row.parse_status == "unsupported"


def test_one_snapshot_can_be_read_back_and_a_missing_one_is_404(client, db_session, monkeypatch):
    monkeypatch.setattr(sources_router, "ingest_url", lambda uri, **kw: retained(uri=uri))
    created = client.post(f"{API}/ai/sources/url", json={"uri": "https://example.test/page"}).json()

    read_back = client.get(f"{API}/ai/sources/{created['snapshot_id']}")
    missing = client.get(f"{API}/ai/sources/999999")

    assert read_back.status_code == 200
    assert read_back.json() == created
    assert missing.status_code == 404


def test_the_same_url_fetched_twice_writes_two_observations(client, db_session, monkeypatch):
    """Snapshots are append-only, so a later run can still see what the first one read."""

    monkeypatch.setattr(
        sources_router,
        "ingest_url",
        lambda uri, **kw: retained(uri=uri, source_hash=hashlib.sha256(uri.encode()).hexdigest()),
    )

    first = client.post(f"{API}/ai/sources/url", json={"uri": "https://example.test/page"}).json()
    second = client.post(f"{API}/ai/sources/url", json={"uri": "https://example.test/page"}).json()

    assert first["snapshot_id"] != second["snapshot_id"]
    assert row_count(db_session) == 2
    assert first["snapshot_id"] < second["snapshot_id"]


def test_ingesting_never_creates_an_ai_task_or_spends_budget(client, db_session, monkeypatch):
    """Reading a page is not a model call: no task row, no provider, no cost."""

    monkeypatch.setattr(sources_router, "ingest_url", lambda uri, **kw: retained(uri=uri))

    client.post(f"{API}/ai/sources/url", json={"uri": "https://example.test/page"})

    assert list(db_session.execute(select(AITask)).scalars()) == []


def test_a_stored_snapshot_can_be_reused_without_fetching_again(client, db_session, monkeypatch):
    monkeypatch.setattr(sources_router, "ingest_url", lambda uri, **kw: retained(uri=uri))
    created = client.post(
        f"{API}/ai/sources/url",
        json={"uri": "https://example.test/page", "source_ref": "docs", "label": "Docs"},
    ).json()

    material = ingest.material_from_snapshot(
        snapshots.get_snapshot(db_session, created["snapshot_id"])
    )
    assert material.snapshot_id == created["snapshot_id"]
    assert material.text == "Alpha paragraph.\n\nBeta paragraph."
    assert material.text_hash == created["text_hash"]
    assert material.source_ref == "docs"

    class Item:
        kind = "url"
        snapshot_id = created["snapshot_id"]
        uri = None
        retention = None
        license_note = None
        source_ref = None
        label = None

    def explode(*args, **kwargs):
        pytest.fail("a named snapshot must not be fetched again")

    reused = snapshots.snapshot_ingester(db_session, retrieve=explode)(Item())

    assert reused.snapshot_id == created["snapshot_id"]
    assert reused.text == material.text
    assert row_count(db_session) == 1


def test_unknown_snapshot_id_is_reported_rather_than_fetched(client, db_session):
    class Item:
        kind = "url"
        snapshot_id = 4242
        uri = "https://example.test/page"
        retention = None
        license_note = None
        source_ref = None
        label = None

    with pytest.raises(ValueError, match="unknown snapshot_id 4242"):
        snapshots.snapshot_ingester(db_session)(Item())


def test_retention_policy_is_third_party_by_default():
    assert ingest.retention_policy(None) == "excerpt"
    assert ingest.retention_policy("full") == "full"
    assert ingest.excerpt_budget("excerpt") == 500
    with pytest.raises(ValueError, match="unknown retention policy"):
        ingest.retention_policy("forever")
    with pytest.raises(ValueError, match="license note"):
        ingest.check_retention_request("full", None)
    ingest.check_retention_request("full", "owned by the user")
