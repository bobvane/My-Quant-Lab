"""Turning a fetched body into text — without ever guessing.

The parser policy (ADR-163):

* **HTML**: the standard library parser. No browser, no JavaScript, no rendering.
  ``script``/``style``/``noscript``/``template``/``svg`` content is dropped rather
  than extracted; every other element contributes its text, and block-level
  elements are read as paragraph breaks. A page that has no readable text after
  that is reported ``unsupported`` — the honest answer for a page that needs
  JavaScript, instead of an empty document that looks like research material.
* **PDF**: ``pypdf``, text layer only. A PDF with no text layer (a scan, a
  screenshot) is ``unsupported``; a PDF we cannot read is ``parse_failed``. No OCR,
  no rendering, no vision model, and nothing is invented to fill the gap.
* **text/plain**, **text/markdown**: decoded as declared, taken as they are.

Extraction is not injection defence. The text produced here is still untrusted
material, and this module deliberately contains no keyword list or regex "injection
detector": the boundary stays where it already is (system/task/source message
separation, role contracts, the untrusted-source wrapper). See ADR-163.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from html.parser import HTMLParser

import pypdf

HTML_KINDS = ("text/html", "application/xhtml+xml")
PDF_KINDS = ("application/pdf",)
TEXT_KINDS = ("text/plain", "text/markdown")
PARSEABLE_KINDS = HTML_KINDS + PDF_KINDS + TEXT_KINDS

HTML_PARSER = "stdlib.html.parser"
TEXT_PARSER = "stdlib.text"
PDF_PARSER = "pypdf"
PARSER_VERSION = "1.0"

MAX_PDF_PAGES = 50
MAX_PARSE_CHARS = 200_000

OK = "ok"
UNSUPPORTED = "unsupported"
PARSE_FAILED = "parse_failed"
PARSE_STATUSES = (OK, UNSUPPORTED, PARSE_FAILED)

DROP_ELEMENTS = frozenset(
    {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object"}
)
BLOCK_ELEMENTS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)


@dataclass(frozen=True)
class ParsedDocument:
    """What a parser made of a body, and whether it had to stop early.

    ``truncated`` is about *parsing* (we did not read all of it); how much text is
    finally kept is the retention layer's separate decision (ADR-161).
    """

    kind: str
    status: str
    parser: str
    parser_version: str
    content_type: str
    text: str = ""
    title: str = ""
    page_count: int | None = None
    truncated: bool = False
    error: str = ""

    @property
    def readable(self) -> bool:
        return self.status == OK and bool(self.text.strip())


def parse_document(
    body: bytes,
    content_type: str,
    *,
    max_pages: int = MAX_PDF_PAGES,
    max_chars: int = MAX_PARSE_CHARS,
) -> ParsedDocument:
    """Dispatch on the media type; a type we cannot read is ``unsupported``."""
    media_type = media_type_of(content_type)
    if media_type in HTML_KINDS:
        return parse_html(body, content_type=content_type, max_chars=max_chars)
    if media_type in PDF_KINDS:
        return parse_pdf(body, content_type=content_type, max_pages=max_pages, max_chars=max_chars)
    if media_type in TEXT_KINDS:
        return parse_text(body, content_type=content_type, max_chars=max_chars)
    return ParsedDocument(
        kind="unknown",
        status=UNSUPPORTED,
        parser="none",
        parser_version="",
        content_type=content_type,
        error=f"this version cannot read {media_type or 'an untyped body'}",
    )


def parse_text(
    body: bytes,
    *,
    content_type: str = "text/plain",
    max_chars: int = MAX_PARSE_CHARS,
) -> ParsedDocument:
    text = _tidy(_decode(body, _charset(content_type)))
    if not text:
        return ParsedDocument(
            kind="text",
            status=UNSUPPORTED,
            parser=TEXT_PARSER,
            parser_version=PARSER_VERSION,
            content_type=content_type,
            error="the document has no readable text",
        )
    return ParsedDocument(
        kind="text",
        status=OK,
        parser=TEXT_PARSER,
        parser_version=PARSER_VERSION,
        content_type=content_type,
        text=text[:max_chars],
        truncated=len(text) > max_chars,
    )


def parse_html(
    body: bytes,
    *,
    content_type: str = "text/html",
    max_chars: int = MAX_PARSE_CHARS,
) -> ParsedDocument:
    extractor = _HtmlText()
    try:
        extractor.feed(_decode(body, _charset(content_type)))
        extractor.close()
    except Exception as error:  # the stdlib parser is lenient, but stay honest
        return ParsedDocument(
            kind="html",
            status=PARSE_FAILED,
            parser=HTML_PARSER,
            parser_version=PARSER_VERSION,
            content_type=content_type,
            error=f"{error.__class__.__name__}: {error}",
        )
    text = _tidy(extractor.text())
    if not text:
        return ParsedDocument(
            kind="html",
            status=UNSUPPORTED,
            parser=HTML_PARSER,
            parser_version=PARSER_VERSION,
            content_type=content_type,
            title=extractor.title(),
            error=(
                "the page has no readable text without running scripts"
                " (this version does not render pages)"
            ),
        )
    return ParsedDocument(
        kind="html",
        status=OK,
        parser=HTML_PARSER,
        parser_version=PARSER_VERSION,
        content_type=content_type,
        text=text[:max_chars],
        title=extractor.title(),
        truncated=len(text) > max_chars,
    )


def parse_pdf(
    body: bytes,
    *,
    content_type: str = "application/pdf",
    max_pages: int = MAX_PDF_PAGES,
    max_chars: int = MAX_PARSE_CHARS,
) -> ParsedDocument:
    """Read the text layer of a PDF; nothing else is attempted."""
    if b"%PDF-" not in body[:1024]:
        return _pdf_result(
            PARSE_FAILED,
            content_type=content_type,
            error="the body does not begin with a PDF header",
        )
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        if reader.is_encrypted and not reader.decrypt(""):
            return _pdf_result(
                UNSUPPORTED,
                content_type=content_type,
                error="the PDF is encrypted",
            )
        page_count = len(reader.pages)
    except Exception as error:
        return _pdf_result(
            PARSE_FAILED,
            content_type=content_type,
            error=f"{error.__class__.__name__}: {error}",
        )

    chunks: list[str] = []
    errors: list[str] = []
    total = 0
    truncated = False
    for index, page in enumerate(reader.pages):
        if index >= max_pages:
            truncated = True
            break
        try:
            page_text = page.extract_text() or ""
        except Exception as error:
            if not chunks:
                return _pdf_result(
                    PARSE_FAILED,
                    content_type=content_type,
                    error=f"{error.__class__.__name__}: {error}",
                )
            # Keep what we already read, but say where it stopped.
            errors.append(f"page {index + 1}: {error.__class__.__name__}")
            truncated = True
            break
        chunks.append(page_text)
        total += len(page_text)
        if total >= max_chars:
            truncated = True
            break

    text = _tidy("\n".join(chunks))
    if not text:
        return _pdf_result(
            UNSUPPORTED,
            content_type=content_type,
            error=("the PDF has no text layer (it may be a scan); OCR is not part of this version"),
            page_count=page_count,
        )
    return _pdf_result(
        OK,
        content_type=content_type,
        text=text[:max_chars],
        page_count=page_count,
        truncated=truncated,
        error="; ".join(errors),
    )


def media_type_of(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _pdf_result(
    status: str,
    *,
    content_type: str,
    text: str = "",
    page_count: int | None = None,
    truncated: bool = False,
    error: str = "",
) -> ParsedDocument:
    return ParsedDocument(
        kind="pdf",
        status=status,
        parser=PDF_PARSER,
        parser_version=pypdf.__version__,
        content_type=content_type,
        text=text,
        page_count=page_count,
        truncated=truncated,
        error=error,
    )


class _HtmlText(HTMLParser):
    """Collect the readable text of an HTML document, and nothing else."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._dropping: list[str] = []
        self._in_title = 0
        self._title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._dropping:
            return
        if tag in DROP_ELEMENTS:
            self._dropping.append(tag)
            return
        if tag == "title":
            self._in_title += 1
            return
        if tag in BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._dropping:
            return
        if tag in BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._dropping:
            if tag in self._dropping:
                # Close whatever is open, in the order the document actually closed it.
                while self._dropping:
                    if self._dropping.pop() == tag:
                        break
            return
        if tag == "title" and self._in_title:
            self._in_title -= 1
            return
        if tag in BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._dropping:
            return
        if self._in_title:
            self._title.append(data)
            return
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)

    def title(self) -> str:
        return " ".join("".join(self._title).split())


def _charset(content_type: str) -> str:
    for parameter in content_type.split(";")[1:]:
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "charset":
            return value.strip().strip('"').lower()
    return ""


def _decode(body: bytes, charset: str) -> str:
    for encoding in (charset, "utf-8"):
        if not encoding:
            continue
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _tidy(text: str) -> str:
    """Collapse runs of whitespace, keep single blank lines between paragraphs."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    kept: list[str] = []
    for line in lines:
        if line or (kept and kept[-1]):
            kept.append(line)
    return "\n".join(kept).strip()
