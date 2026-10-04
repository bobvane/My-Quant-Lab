#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic Markdown -> DOCX builder for the My Quant Lab development spec.

Usage
-----
    python scripts/build_dev_spec_docx.py

Defaults (no arguments needed):

    input  : docs/My_Quant_Lab_Development_Spec_V1.1.md
    output : docs/My_Quant_Lab_Development_Spec_V1.1.docx
             My_Quant_Lab_Development_Spec_V1.1.docx   (repository root)

Both DOCX files are written from the *same* in-memory byte string, so the
repository-root copy is byte-identical to the docs/ copy by construction.

Optional arguments::

    python scripts/build_dev_spec_docx.py --input <path.md> --output <path.docx>

The Markdown file is the single source of truth. Never hand-edit the DOCX.

Style mapping (Markdown -> Word)
--------------------------------
    # H1                 -> style "Title"          (document title)
    ## H2                -> style "Heading 1"
    ### H3               -> style "Heading 2"
    - item               -> style "List Bullet"
    > quoted line        -> style "Quote"
    ``` fenced block ``` -> Normal paragraph, Consolas font, no first-line indent
    1. item              -> Normal paragraph (literal "1." kept; no Word auto-numbering)
    | a | b |            -> Word table, style "Table Grid"
    everything else      -> style "Normal", one paragraph per blank-line-separated block

Inline markup: ``**bold**`` becomes bold, `` `code` `` becomes a Consolas run.
Literal ``\\|`` inside a table cell is unescaped to ``|``.

Determinism
-----------
The script performs no network access, reads no clock, and uses no randomness:
it only depends on python-docx and the standard library. Running it twice on the
same Markdown input produces byte-identical DOCX output (verified by SHA256).

python-docx itself is *not* byte-deterministic: OPC packaging writes every ZIP
member through ``zipfile.ZipInfo``, whose default ``date_time`` is the current
wall clock, so two otherwise identical runs differ in the ZIP headers. This
script therefore re-packs the generated package with a fixed member timestamp
(1980-01-01 00:00:00, the ZIP epoch) and a fixed compression method, which is
what makes the output reproducible. See ``_normalise_zip()``.

Fonts
-----
"Normal", "Title", "Heading 1", "Heading 2" and "List Bullet" are all set to
"微软雅黑" with the East Asian font attribute set explicitly, so Chinese text
renders correctly in Word instead of falling back to a Latin-only face.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parent.parent

DEFAULT_INPUT = REPO_ROOT / "docs" / "My_Quant_Lab_Development_Spec_V1.1.md"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "My_Quant_Lab_Development_Spec_V1.1.docx"
DEFAULT_ROOT_COPY = REPO_ROOT / "My_Quant_Lab_Development_Spec_V1.1.docx"

BODY_FONT = "微软雅黑"
CODE_FONT = "Consolas"

_HEADING_STYLES = {1: "Title", 2: "Heading 1", 3: "Heading 2"}
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_SEPARATOR_RE = re.compile(r"^\|?[\s:\-|]+\|?$")


# --------------------------------------------------------------------------- #
# low level helpers
# --------------------------------------------------------------------------- #

def _set_style_font(style, *, name: str, size_pt: float | None = None,
                    bold: bool | None = None) -> None:
    """Pin a style's Latin and East Asian font so Chinese text renders."""
    style.font.name = name
    if size_pt is not None:
        style.font.size = _pt(size_pt)
    if bold is not None:
        style.font.bold = bold
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:cs"), name)


def _pt(value: float):
    from docx.shared import Pt

    return Pt(value)


def _set_run_font(run, name: str) -> None:
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:cs"), name)


def _add_code_spans(paragraph, text: str, *, bold: bool) -> None:
    """Emit plain text plus `` `code` `` runs, all sharing one bold flag."""
    position = 0
    for match in _CODE_RE.finditer(text):
        if match.start() > position:
            run = paragraph.add_run(text[position:match.start()])
            run.bold = bold
            _set_run_font(run, BODY_FONT)
        run = paragraph.add_run(match.group(1))
        run.bold = bold
        _set_run_font(run, CODE_FONT)
        position = match.end()
    if position < len(text):
        run = paragraph.add_run(text[position:])
        run.bold = bold
        _set_run_font(run, BODY_FONT)


def _add_inline(paragraph, text: str, *, bold: bool = False) -> None:
    """Append text to a paragraph, honouring ``**bold**`` and `` `code` ``.

    Bold spans are resolved first and code spans inside them are resolved
    recursively, so ``**contract `RESEARCHER.md` ready**`` keeps its code run
    instead of leaking literal backticks into the bold text.
    """
    text = text.replace("\\|", "|")
    position = 0
    for match in _BOLD_RE.finditer(text):
        _add_code_spans(paragraph, text[position:match.start()], bold=bold)
        _add_inline(paragraph, match.group(1), bold=True)
        position = match.end()
    _add_code_spans(paragraph, text[position:], bold=bold)


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _normalise_zip(payload: bytes) -> bytes:
    """Re-pack a DOCX with fixed ZIP member metadata so output is reproducible.

    python-docx stamps every OPC part with the current wall clock, which makes
    two runs differ byte-for-byte. Rewriting the archive with the ZIP epoch
    (1980-01-01 00:00:00), a fixed compression method and a fixed file mode
    removes the only non-deterministic input.
    """
    source = zipfile.ZipFile(io.BytesIO(payload))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            data = source.read(name)
            info = zipfile.ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0o600 << 16
            target.writestr(info, data)
    return output.getvalue()


# --------------------------------------------------------------------------- #
# document building
# --------------------------------------------------------------------------- #

def _configure_styles(document: Document) -> None:
    styles = document.styles

    normal = styles["Normal"]
    _set_style_font(normal, name=BODY_FONT, size_pt=10.5)
    normal.paragraph_format.space_after = _pt(4)
    normal.paragraph_format.line_spacing = 1.15

    for style_name, size in (("Title", 20), ("Heading 1", 15), ("Heading 2", 12.5)):
        style = styles[style_name]
        _set_style_font(style, name=BODY_FONT, size_pt=size, bold=True)
        style.paragraph_format.space_before = _pt(10)
        style.paragraph_format.space_after = _pt(6)

    for style_name in ("List Bullet", "Quote"):
        _set_style_font(styles[style_name], name=BODY_FONT, size_pt=10.5)


def _add_table(document: Document, rows: list[list[str]]) -> None:
    if not rows:
        return
    columns = max(len(row) for row in rows)
    table = document.add_table(rows=0, cols=columns)
    table.style = "Table Grid"
    table.autofit = True
    for row_index, row in enumerate(rows):
        cells = table.add_row().cells
        for column_index in range(columns):
            cell = cells[column_index]
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = _pt(0)
            text = row[column_index] if column_index < len(row) else ""
            _add_inline(paragraph, text)
            for run in paragraph.runs:
                run.font.size = _pt(9)
                if row_index == 0:
                    run.bold = True


def build_document(markdown_text: str) -> Document:
    document = Document()
    _configure_styles(document)

    lines = markdown_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    total = len(lines)
    paragraph_buffer: list[str] = []

    def flush_paragraphs() -> None:
        nonlocal paragraph_buffer
        if not paragraph_buffer:
            return
        text = "\n".join(paragraph_buffer).strip()
        paragraph_buffer = []
        if not text:
            return
        paragraph = document.add_paragraph(style="Normal")
        _add_inline(paragraph, text)

    while index < total:
        line = lines[index]
        stripped = line.strip()

        # blank line: close the current paragraph block
        if not stripped:
            flush_paragraphs()
            index += 1
            continue

        # fenced code block
        if stripped.startswith("```"):
            flush_paragraphs()
            index += 1
            code_lines: list[str] = []
            while index < total and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index].rstrip())
                index += 1
            index += 1  # consume the closing fence
            for code_line in code_lines:
                paragraph = document.add_paragraph(style="Normal")
                paragraph.paragraph_format.space_after = _pt(0)
                paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
                run = paragraph.add_run(code_line if code_line else " ")
                _set_run_font(run, CODE_FONT)
                run.font.size = _pt(9)
            continue

        # markdown table
        if stripped.startswith("|"):
            flush_paragraphs()
            table_rows: list[list[str]] = []
            while index < total and lines[index].strip().startswith("|"):
                raw = lines[index].strip()
                if _TABLE_SEPARATOR_RE.match(raw) and set(raw) <= set("|-: "):
                    index += 1
                    continue
                table_rows.append(_split_table_row(raw))
                index += 1
            _add_table(document, table_rows)
            document.add_paragraph(style="Normal")
            continue

        # headings
        match = _HEADING_RE.match(stripped)
        if match:
            flush_paragraphs()
            level = len(match.group(1))
            style = _HEADING_STYLES.get(level, "Heading 3")
            paragraph = document.add_paragraph(style=style)
            _add_inline(paragraph, match.group(2).strip())
            index += 1
            continue

        # blockquote
        if stripped.startswith(">"):
            flush_paragraphs()
            paragraph = document.add_paragraph(style="Quote")
            _add_inline(paragraph, stripped.lstrip(">").strip())
            index += 1
            continue

        # bullet list item
        if re.match(r"^\s*-\s+", line):
            flush_paragraphs()
            paragraph = document.add_paragraph(style="List Bullet")
            _add_inline(paragraph, re.sub(r"^\s*-\s+", "", line).strip())
            index += 1
            continue

        # anything else accumulates into a paragraph block
        paragraph_buffer.append(stripped)
        index += 1

    flush_paragraphs()
    return document


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                        help="Markdown source (default: %(default)s)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="DOCX destination (default: %(default)s)")
    parser.add_argument("--no-root-copy", action="store_true",
                        help="do not also write the repository-root copy")
    args = parser.parse_args(argv)

    source = args.input
    if not source.is_file():
        print(f"ERROR: markdown source not found: {source}", file=sys.stderr)
        return 2

    markdown_text = source.read_text(encoding="utf-8")
    document = build_document(markdown_text)

    buffer = io.BytesIO()
    document.save(buffer)
    payload = _normalise_zip(buffer.getvalue())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)

    written = [args.output]
    if not args.no_root_copy and args.output.name == DEFAULT_OUTPUT.name:
        DEFAULT_ROOT_COPY.write_bytes(payload)
        written.append(DEFAULT_ROOT_COPY)

    print(f"source   : {source}")
    for path in written:
        print(f"written  : {path}  ({len(payload)} bytes)")
    print(f"paragraphs: {len(document.paragraphs)}  "
          f"headings: {sum(1 for p in document.paragraphs if p.style.name.startswith(('Title', 'Heading')))}  "
          f"tables: {len(document.tables)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
