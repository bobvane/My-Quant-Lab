"""Turn an external source into the material this platform is willing to research.

Guard → fetch → parse → retention, in that order, and nothing else: no model call,
no database write. The caller records the observation
(``app/data/source_snapshot_service.py``), which keeps the network layer and the
storage layer apart while still letting one snapshot answer "what did this run
read?" (ADR-163).

Three identities are kept apart on purpose (ADR-161):

* ``source_hash`` — the bytes as they arrived. It covers everything that came back,
  including the part no parser will ever read.
* ``text_hash`` — the text this material actually offers to research, i.e. the
  retained excerpt after the retention policy and the artifact cap. A run that
  reads it can prove it read exactly this.
* ``chars_read`` and ``retained_chars`` — how much was parsed and how much was
  kept, so a reader can see "read 2 MB, parsed 40 K characters, kept 500".

The retention numbers themselves live in ``app/ai/research.py`` (ADR-161) and are
imported rather than restated: two copies of a policy is how a policy drifts.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.ai.research import (
    FETCHED_KINDS,
    MAX_ARTIFACT_CHARS,
    RETENTION_POLICIES,
    THIRD_PARTY_EXCERPT_CHARS,
    USER_OWNED_EXCERPT_CHARS,
)
from app.sources.fetch import (
    DOCUMENT_CONTENT_TYPES,
    MAX_DOCUMENT_BYTES,
    SourceFetchError,
    fetch_document,
    retrieve_document,
)
from app.sources.guard import SourceBlocked
from app.sources.parse import (
    OK,
    PARSE_FAILED,
    PARSE_STATUSES,
    UNSUPPORTED,
    ParsedDocument,
    parse_document,
)

#: Source kinds this module can produce material for (``FETCHED_KINDS``, re-exported
#: from the research layer so both layers agree on one list). ``user_input``/``text``/
#: ``github_file`` never come through here: the caller already has their text.
PDF_CONTENT_TYPES: tuple[str, ...] = ("application/pdf",)

#: What happened to the source as a whole (docs/27 §8). ``retained`` means an
#: observation exists and may hold an excerpt; ``parse_status`` answers the
#: separate question of whether any text could be read out of it.
RETAINED = "retained"
BLOCKED = "blocked"
FETCH_FAILED = "fetch_failed"
SNAPSHOT_STATUSES: tuple[str, ...] = (RETAINED, BLOCKED, FETCH_FAILED)

NOT_PARSED = "not_parsed"
PARSE_STATUS_VALUES: tuple[str, ...] = (OK, UNSUPPORTED, PARSE_FAILED, NOT_PARSED)

EXCERPT_CHARS = THIRD_PARTY_EXCERPT_CHARS
FULL_CHARS = USER_OWNED_EXCERPT_CHARS

Retriever = Callable[..., Any]


@dataclass(frozen=True)
class IngestedMaterial:
    """One observation of an external source, ready to be stored and read.

    ``status`` is :data:`RETAINED`, :data:`BLOCKED` or :data:`FETCH_FAILED`;
    ``code`` carries the stable reason code from the guard or the fetcher so a
    caller can report *why* (``scheme_not_allowed``, ``robots_disallowed``,
    ``timeout`` …) instead of a generic refusal. ``message`` is the sentence a
    human reads. Nothing here raises for a source that was refused: a refusal is
    a result, and it is stored like any other observation.
    """

    kind: str
    status: str
    parse_status: str = NOT_PARSED
    #: Set once the observation has been stored, so an artifact can name it.
    snapshot_id: int | None = None
    uri: str | None = None
    final_uri: str | None = None
    code: str | None = None
    message: str = ""
    text: str = ""
    chars_read: int = 0
    retained_chars: int = 0
    truncated: bool = False
    source_hash: str | None = None
    text_hash: str | None = None
    size_bytes: int = 0
    http_status: int | None = None
    content_type: str | None = None
    parser: str | None = None
    parser_version: str | None = None
    robots_ok: bool | None = None
    retention: str = "excerpt"
    license_note: str | None = None
    source_ref: str | None = None
    label: str | None = None
    warnings: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    fetched_at: datetime | None = None

    @property
    def usable(self) -> bool:
        """Whether a research run can read anything from this material."""

        return self.status == RETAINED and bool(self.text.strip())

    @property
    def excerpt_pieces(self) -> list[str]:
        """The retained text as paragraphs, for the API's ``excerpt`` list."""

        return [piece for piece in (line.strip() for line in self.text.splitlines()) if piece]


# --------------------------------------------------------------------------- #
# Retention (ADR-161)
# --------------------------------------------------------------------------- #
def retention_policy(requested: str | None) -> str:
    """The policy to store under: ``excerpt`` unless the caller asked for ``full``.

    A fetched URL or PDF is third-party material by definition — the platform
    cannot know the user owns it — so the caller's own words are never the
    default here the way they are for ``user_input``.
    """

    policy = (requested or "").strip()
    if not policy:
        return "excerpt"
    if policy not in RETENTION_POLICIES:
        raise ValueError(
            f"unknown retention policy '{policy}'; expected one of {', '.join(RETENTION_POLICIES)}"
        )
    return policy


def check_retention_request(policy: str, license_note: str | None) -> None:
    """Refuse ``full`` for third-party material without a licence note."""

    if policy == "full" and not (license_note or "").strip():
        raise ValueError(
            "keeping material from elsewhere in full needs a license note saying the user "
            "owns it or is licensed to keep it; otherwise it is stored as an excerpt"
        )


def excerpt_budget(policy: str) -> int:
    return FULL_CHARS if policy == "full" else EXCERPT_CHARS


# --------------------------------------------------------------------------- #
# URL and PDF ingestion
# --------------------------------------------------------------------------- #
def ingest_url(
    uri: str,
    *,
    kind: str = "url",
    source_ref: str | None = None,
    label: str | None = None,
    retention: str | None = None,
    license_note: str | None = None,
    respect_robots: bool = True,
    allowed_types: Sequence[str] | None = DOCUMENT_CONTENT_TYPES,
    retrieve: Retriever = retrieve_document,
    fetched_at: datetime | None = None,
) -> IngestedMaterial:
    """Fetch one URL and read it, or record why that did not happen."""

    policy = retention_policy(retention)
    check_retention_request(policy, license_note)
    try:
        retrieved = retrieve(
            uri,
            respect_robots=respect_robots,
            allowed_types=allowed_types,
            retrieved_at=fetched_at,
        )
    except SourceBlocked as blocked:
        return _refused(
            kind,
            uri,
            status=BLOCKED,
            code=blocked.code,
            message=blocked.message,
            policy=policy,
            license_note=license_note,
            source_ref=source_ref,
            label=label,
            fetched_at=fetched_at,
        )
    except SourceFetchError as error:
        # A robots.txt rule is a policy refusal, not a network accident: it belongs
        # with the blocked sources so a research run rejects instead of degrading
        # (docs/27 §5.2, §8).
        status = BLOCKED if error.code == "robots_disallowed" else FETCH_FAILED
        return _refused(
            kind,
            uri,
            status=status,
            code=error.code,
            message=error.message,
            policy=policy,
            license_note=license_note,
            source_ref=source_ref,
            label=label,
            http_status=error.status,
            fetched_at=fetched_at,
        )

    document = retrieved.document
    return _read(
        kind,
        uri,
        document.body,
        content_type=document.content_type,
        final_uri=document.url,
        http_status=document.status_code,
        redirects=document.redirects,
        robots=retrieved.robots,
        policy=policy,
        license_note=license_note,
        source_ref=source_ref,
        label=label,
        fetched_at=document.retrieved_at,
    )


def ingest_pdf_uri(uri: str, **kwargs: Any) -> IngestedMaterial:
    """Fetch a PDF URL: anything that is not ``application/pdf`` is refused."""

    return ingest_url(uri, kind="pdf", allowed_types=PDF_CONTENT_TYPES, **kwargs)


def ingest_pdf_bytes(
    body: bytes,
    *,
    uri: str | None = None,
    filename: str | None = None,
    source_ref: str | None = None,
    label: str | None = None,
    retention: str | None = None,
    license_note: str | None = None,
    fetched_at: datetime | None = None,
) -> IngestedMaterial:
    """Read a PDF the caller handed over, without fetching anything.

    The same byte cap as a fetched document applies: handing over a 200 MB file is
    not a way around the limits that exist because parsing is synchronous.
    """

    policy = retention_policy(retention)
    check_retention_request(policy, license_note)
    if not body:
        return _refused(
            "pdf",
            uri,
            status=FETCH_FAILED,
            code="empty_response",
            message="the PDF payload is empty",
            policy=policy,
            license_note=license_note,
            source_ref=source_ref,
            label=label,
            fetched_at=fetched_at,
        )
    if len(body) > MAX_DOCUMENT_BYTES:
        return _refused(
            "pdf",
            uri,
            status=FETCH_FAILED,
            code="response_too_large",
            message=f"the PDF is larger than {MAX_DOCUMENT_BYTES} bytes",
            policy=policy,
            license_note=license_note,
            source_ref=source_ref,
            label=label,
            fetched_at=fetched_at,
        )
    return _read(
        "pdf",
        uri,
        body,
        content_type="application/pdf",
        final_uri=uri,
        http_status=None,
        redirects=(),
        robots=None,
        policy=policy,
        license_note=license_note,
        source_ref=source_ref,
        label=label,
        fetched_at=fetched_at,
        extra_metadata={"filename": filename} if filename else None,
    )


def decode_base64_payload(value: str, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> bytes:
    """Decode an inline payload, refusing junk and refusing to buffer a huge one."""

    compact = "".join(value.split())
    if not compact:
        raise ValueError("the payload is empty")
    try:
        body = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("the payload is not valid base64") from error
    if len(body) > max_bytes:
        raise ValueError(f"the payload is larger than {max_bytes} bytes")
    return body


def ingest_pdf_base64(value: str, **kwargs: Any) -> IngestedMaterial:
    """Decode then :func:`ingest_pdf_bytes`; a bad payload is the caller's 400."""

    body = decode_base64_payload(value)
    return ingest_pdf_bytes(body, **kwargs)


def material_from_snapshot(snapshot: Any) -> IngestedMaterial:
    """Rebuild the material a stored snapshot stands for, without touching the network.

    Only the retained excerpt comes back, because that is all a snapshot keeps
    (ADR-161): a research run that names a snapshot reads exactly what was kept at
    ingestion time, and the hashes it reports are that material's hashes.
    """

    text = snapshot.excerpt or ""
    metadata = dict(snapshot.metadata_json or {})
    return IngestedMaterial(
        kind=snapshot.source_type,
        status=snapshot.status if snapshot.status in SNAPSHOT_STATUSES else FETCH_FAILED,
        parse_status=snapshot.parse_status or NOT_PARSED,
        snapshot_id=snapshot.id,
        uri=snapshot.url,
        final_uri=snapshot.final_url,
        code=(snapshot.metadata_json or {}).get("error_code"),
        message=snapshot.error or "",
        text=text,
        chars_read=snapshot.chars_read or 0,
        retained_chars=snapshot.retained_chars or 0,
        truncated=bool(snapshot.truncated),
        source_hash=snapshot.source_hash,
        text_hash=snapshot.text_hash,
        size_bytes=snapshot.size_bytes or 0,
        http_status=snapshot.http_status,
        content_type=snapshot.content_type,
        parser=snapshot.parser,
        parser_version=snapshot.parser_version,
        robots_ok=snapshot.robots_ok,
        retention=snapshot.retention,
        license_note=snapshot.license_note,
        source_ref=metadata.get("source_ref"),
        label=metadata.get("label"),
        metadata=metadata,
        fetched_at=snapshot.fetched_at,
    )


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _refused(
    kind: str,
    uri: str | None,
    *,
    status: str,
    code: str,
    message: str,
    policy: str,
    license_note: str | None,
    source_ref: str | None,
    label: str | None,
    http_status: int | None = None,
    fetched_at: datetime | None = None,
) -> IngestedMaterial:
    return IngestedMaterial(
        kind=kind,
        status=status,
        parse_status=NOT_PARSED,
        uri=uri,
        code=code,
        message=message,
        retention=policy,
        license_note=license_note,
        source_ref=source_ref,
        label=label,
        http_status=http_status,
        metadata={"error_code": code},
        fetched_at=fetched_at or datetime.now(UTC),
    )


def _read(
    kind: str,
    uri: str | None,
    body: bytes,
    *,
    content_type: str,
    final_uri: str | None,
    http_status: int | None,
    redirects: tuple[str, ...],
    robots: Any,
    policy: str,
    license_note: str | None,
    source_ref: str | None,
    label: str | None,
    fetched_at: datetime | None,
    extra_metadata: dict[str, Any] | None = None,
) -> IngestedMaterial:
    """Hash the bytes, read them, and keep no more than the policy allows."""

    parsed: ParsedDocument = parse_document(body, content_type)
    source_hash = hashlib.sha256(body).hexdigest()
    chars_read = len(parsed.text)
    budget = excerpt_budget(policy)
    text = parsed.text[:MAX_ARTIFACT_CHARS]
    warnings: list[dict[str, Any]] = []
    if chars_read > len(text):
        warnings.append(
            {
                "kind": "truncated",
                "source_ref": source_ref,
                "kept_chars": MAX_ARTIFACT_CHARS,
                "original_chars": chars_read,
                "note": "only the beginning of the source was read",
            }
        )
    retained = text[:budget]
    truncated = len(retained) < chars_read
    if len(retained) < len(text):
        warnings.append(
            {
                "kind": "excerpt_limited",
                "source_ref": source_ref,
                "policy": policy,
                "retention_chars": budget,
                "stored_chars": len(retained),
                "read_chars": chars_read,
                "note": "only an excerpt of this source is kept; the text itself is not stored",
            }
        )
    if robots is not None and not robots.checked:
        warnings.append(
            {
                "kind": "no_robots_txt",
                "source_ref": source_ref,
                "note": robots.reason,
            }
        )

    metadata: dict[str, Any] = {
        "redirects": list(redirects),
        "redirect_count": len(redirects),
        "parse_kind": parsed.kind,
        "page_count": parsed.page_count,
        "title": parsed.title or None,
    }
    if robots is not None:
        metadata["robots"] = {
            "checked": robots.checked,
            "allowed": robots.allowed,
            "reason": robots.reason,
            "matched_rule": robots.matched_rule,
            "status_code": robots.status_code,
        }
    if parsed.error:
        metadata["parse_error"] = parsed.error
    if extra_metadata:
        metadata.update(extra_metadata)

    return IngestedMaterial(
        kind=kind,
        status=RETAINED,
        parse_status=parsed.status if parsed.status in PARSE_STATUSES else NOT_PARSED,
        uri=uri,
        final_uri=final_uri,
        code=None if parsed.status == OK else f"parse_{parsed.status}",
        message=parsed.error,
        text=retained,
        chars_read=chars_read,
        retained_chars=len(retained),
        truncated=truncated,
        source_hash=source_hash,
        text_hash=hashlib.sha256(retained.encode("utf-8")).hexdigest(),
        size_bytes=len(body),
        http_status=http_status,
        content_type=content_type,
        parser=parsed.parser,
        parser_version=parsed.parser_version,
        robots_ok=None if robots is None else robots.allowed,
        retention=policy,
        license_note=license_note,
        source_ref=source_ref,
        label=label,
        warnings=tuple(warnings),
        metadata=metadata,
        fetched_at=fetched_at or datetime.now(UTC),
    )


__all__ = [
    "BLOCKED",
    "DOCUMENT_CONTENT_TYPES",
    "EXCERPT_CHARS",
    "FETCHED_KINDS",
    "FETCH_FAILED",
    "FULL_CHARS",
    "IngestedMaterial",
    "NOT_PARSED",
    "PDF_CONTENT_TYPES",
    "RETAINED",
    "SNAPSHOT_STATUSES",
    "check_retention_request",
    "decode_base64_payload",
    "excerpt_budget",
    "fetch_document",
    "ingest_pdf_base64",
    "ingest_pdf_bytes",
    "ingest_pdf_uri",
    "ingest_url",
    "material_from_snapshot",
    "retention_policy",
]
