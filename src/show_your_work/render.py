"""The ``render`` logic (plan sections 3.1, 3.4, 4): generated ``README.md`` and ``workflows.json``.

:func:`render_dir` runs :func:`show_your_work.checks.check_dir` first and refuses a failing set
(exit 1, nothing written). Otherwise it builds both outputs from the top-level ``*.mmd`` sources:

* ``README.md`` -- GENERATED marker, ``# Workflow diagrams``, ``intro.md`` verbatim (when present),
  ``## Contents`` (``[title](#anchor) - summary``), one section per diagram (summary, ``Source:``
  link, ``Drawn from:`` bullets, ``**Alt:**``, ``**Deliberately not shown:**``, the mermaid fence)
  and the ``## Regenerating`` footer. Anchors follow GitHub's slug rule, including its ``-1`` /
  ``-2`` suffixes for repeated headings.
* ``workflows.json`` -- schema ``show-your-work.workflows.v1`` with the exact key sets of
  section 3.4, items sorted by id, ``source_dir`` / ``source_path`` relative to the nearest
  ``.git`` ancestor (never cwd), ``source_sha256`` over CRLF-normalized bytes, ``engine`` from an
  ``<id>.excalidraw.svg`` sibling, and ``rasters`` preserved from the previous index only when
  both referenced files still exist (``null`` otherwise; PNG raster files are never deleted here)
  and are house PNGs exactly when the diagram has a sidecar (plan section 12.D). While a sidecar
  diagram's ``rasters`` are house entries its ``alt`` is the layout ``alt``.
* ``rasters/<id>-{light,dark}.svg`` -- the house SVGs of every diagram with a ``<id>.layout.json``
  sidecar (plan section 2.5), embedded in its README section through ``<picture>``. They are
  written before the README and index; an orphan house SVG (a regular top-level ``*-light.svg`` /
  ``*-dark.svg`` whose first child is ``HOUSE_SVG_MARKER``) is deleted LAST, and a generated path
  holding anything unmarked is exit 2. A linked ``rasters/`` is exit 2 whenever a file may be
  written there; otherwise (a sidecar-less plain render) cleanup is skipped silently through the
  link.
  ``render --check`` reports a changed, missing or orphan house SVG as stale.

Every reader normalizes CRLF to LF, and every write is LF / UTF-8 through an atomic replace, so
the bytes never depend on ``core.autocrlf``. A generated file whose content is already current
(``workflows.json`` compared with ``generated_at`` masked) is left untouched, so a repeat run
changes no bytes. ``check_only`` (``render --check``) writes nothing and also masks ``rasters``.

With a ``rasterizer`` (``render --png``, section 4) the check runs WITH that renderer, so the
garbage / known-good anchors run before any file is read; then the anchors run again through the
raster path (when a diagram has no sidecar) and the house anchors run (when one has one),
and every diagram is rasterized for both themes IN MEMORY before anything is written: a sidecar
diagram from its house SVG with the bundled fonts, tagged as a house PNG (plan section 2.6 Step
10). Only then are ``rasters/<id>-<theme>.png`` written (byte-identical files are left alone), then
``README.md`` / ``workflows.json`` with ``rasters`` filled from the fresh bytes (``path``,
``sha256``, ``width`` / ``height`` from the PNG's IHDR, ``bytes``), and LAST the orphan
PNGs are deleted under the section 4 safety rules (only regular ``*.png`` files directly inside
``rasters/`` that the new index does not reference: never recursing, never following a symlink,
never touching another file type). An interrupted run therefore never leaves an index naming a
deleted file, and a raster rewritten before the index was is caught by the strict
preserved-or-null rule on the next plain ``render``. Every canvas is checked against the pixel
caps (``MAX_RASTER_SIDE`` / ``MAX_RASTER_PIXELS``) before it is rasterized; over the cap is exit
2 with nothing written. ``check_only`` with a rasterizer writes and deletes nothing and compares
rasters unmasked, reporting each raster that would change.

``sha256_lf`` is a deliberate re-export of :func:`show_your_work.header.sha256_lf` (the one source
of truth), kept because render owned it in Phase 1 (plan section 2.2).
"""

from __future__ import annotations

import contextlib
import hashlib
import html
import json
import os
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from show_your_work import __version__
from show_your_work import renderer as renderer_mod
from show_your_work.checks import (
    GateError,
    Report,
    check_dir,
    discover,
    discover_layouts,
    error_detail,
    read_layout,
    read_source,
)
from show_your_work.constants import (
    ANCHOR_KEEP_CHARS,
    ATX_HEADING_RE,
    ATX_MARKER,
    BLOCK_START_RE,
    ENGINE_EXCALIDRAW,
    ENGINE_MERMAID,
    EXCALIDRAW_SUFFIX,
    EXIT_FINDINGS,
    EXIT_OK,
    FENCE_LINE_RE,
    GENERATED_MARKER,
    GENERATOR_NAME,
    GIT_MARKER,
    HOUSE_SVG_FILE,
    HOUSE_SVG_MARKER,
    HOUSE_SVG_ROOT_START,
    HOUSE_SVG_SUFFIXES,
    INDENTED_CODE_RE,
    INDEX_GENERATED_AT,
    INDEX_ITEM_ID,
    INDEX_ITEM_KEYS,
    INDEX_ITEM_RASTERS,
    INDEX_ITEMS,
    INDEX_KEYS,
    INDEX_NAME,
    INDEX_SCHEMA,
    INLINE_LINK_RE,
    INTRO_NAME,
    LAYOUT_SUFFIX,
    MAX_GENERATED_BYTES,
    MERMAID_FENCE_INFO,
    MIN_FENCE,
    MSG_ANCHOR_BROKEN,
    MSG_CANNOT_DELETE,
    MSG_CANNOT_READ,
    MSG_CANNOT_WRITE,
    MSG_HOUSE_ANCHOR_BROKEN,
    MSG_INTRO_NOT_UTF8,
    MSG_RASTER_FAILED,
    MSG_RASTERS_NOT_DIR,
    MSG_SOURCE_CHANGED,
    MSG_STAMP_NOT_UTC,
    MSG_SVG_NOT_GENERATED,
    MSG_SYMLINK_SOURCE,
    NO_PROJECT_ROOT_DIR,
    RASTER_DARK,
    RASTER_ENTRY_KEYS,
    RASTER_FILE,
    RASTER_LIGHT,
    RASTER_SUFFIX,
    RASTER_THEMES,
    RASTERS_DIR,
    README_ALT_LABEL,
    README_ALT_SOURCE_LABEL,
    README_CONTENTS_HEADING,
    README_DRAWN_FROM,
    README_LAYOUT_MARKER_LINES,
    README_LAYOUT_REGEN,
    README_MARKER_INDENT,
    README_MARKER_LINES,
    README_MARKER_OPEN,
    README_NAME,
    README_NOT_SHOWN_LABEL,
    README_PICTURE_TEMPLATE,
    README_REGEN_HEADING,
    README_SOURCE_LABEL,
    README_TITLE,
    SETEXT_UNDERLINE_RE,
    SHA256_HEX_RE,
    SOURCE_SUFFIX,
    STATE_CURRENT,
    STATE_DELETED,
    STATE_STALE,
    STATE_WRITTEN,
)
from show_your_work.header import (
    Header,
    HeaderError,
    check_header,
    normalize_newlines,
    sha256_lf,
)
from show_your_work.layout import Layout, draw_svg, layout_findings, parse_layout
from show_your_work.renderer import RasterRenderer

__all__ = [
    "Diagram",
    "OutputStatus",
    "RenderResult",
    "build_index",
    "build_readme",
    "github_anchor",
    "house_svg_path",
    "index_up_to_date",
    "project_root",
    "raster_path",
    "render_dir",
    "serialize_index",
    "sha256_lf",
    "source_dir_of",
    "write_atomic",
]


@dataclass(frozen=True, slots=True)
class Diagram:
    """One checked source: its id, parsed header, LF-normalized text, engine and layout sidecar."""

    id: str
    header: Header
    text: str
    sha256: str
    engine: str
    layout: Layout | None = None


@dataclass(frozen=True, slots=True)
class OutputStatus:
    """What happened to one generated file (``written`` / ``up to date`` / ``stale``)."""

    name: str
    state: str


@dataclass(frozen=True, slots=True)
class RenderResult:
    """The check report plus the per-output outcome; ``outputs`` is empty when refused."""

    report: Report
    outputs: tuple[OutputStatus, ...]
    exit_code: int

    @property
    def refused(self) -> bool:
        return bool(self.report.failed)

    @property
    def stale(self) -> tuple[str, ...]:
        return tuple(o.name for o in self.outputs if o.state == STATE_STALE)


def _utc_now() -> datetime:
    """The ``generated_at`` clock (a seam tests may monkeypatch)."""
    return datetime.now(UTC)


# --- project root and paths (section 3.4) ---------------------------------------------------


def _has_git_marker(directory: Path) -> bool:
    try:
        return (directory / GIT_MARKER).exists()
    except OSError:
        return False


def project_root(target: Path) -> Path | None:
    """The nearest directory at or above ``target`` holding a ``.git`` file or directory."""
    resolved = target.resolve()
    for candidate in (resolved, *resolved.parents):
        if _has_git_marker(candidate):
            return candidate
    return None


def source_dir_of(target: Path) -> str:
    """``target`` relative to its project root, POSIX separators; ``"."`` without a root."""
    resolved = target.resolve()
    root = project_root(resolved)
    if root is None:
        return NO_PROJECT_ROOT_DIR
    return resolved.relative_to(root).as_posix()


def _source_path(source_dir: str, diagram_id: str) -> str:
    return f"{source_dir}/{diagram_id}{SOURCE_SUFFIX}"


# --- reading -------------------------------------------------------------------------------


def _load_layout(path: Path, diagram_id: str, source_raw: bytes, *, beside: bool) -> Layout:
    """Re-read a sidecar after ``check`` passed; one that no longer passes it is exit 2."""
    raw = read_layout(path)
    if layout_findings(diagram_id, raw, source_raw, beside_drawing=beside):
        raise GateError(MSG_SOURCE_CHANGED.format(path=path.as_posix()))
    return parse_layout(raw)


def _load_diagrams(target: Path) -> list[Diagram]:
    """Re-read every source and sidecar after ``check``; one that no longer passes is exit 2."""
    sources, drawings = discover(target)
    drawn = {path.name[: -len(EXCALIDRAW_SUFFIX)] for path in drawings}
    sidecars = {path.name[: -len(LAYOUT_SUFFIX)]: path for path in discover_layouts(target)}
    diagrams: list[Diagram] = []
    for path in sources:
        raw = read_source(path)
        text = normalize_newlines(raw.decode("utf-8", errors="replace"))
        header = check_header(text)
        if isinstance(header, HeaderError):
            raise GateError(MSG_SOURCE_CHANGED.format(path=path.as_posix()))
        diagram_id = path.name[: -len(SOURCE_SUFFIX)]
        engine = ENGINE_EXCALIDRAW if diagram_id in drawn else ENGINE_MERMAID
        sidecar = sidecars.get(diagram_id)
        layout = (
            None
            if sidecar is None
            else _load_layout(sidecar, diagram_id, raw, beside=diagram_id in drawn)
        )
        diagrams.append(Diagram(diagram_id, header, text, sha256_lf(raw), engine, layout))
    diagrams.sort(key=lambda d: d.id)
    return diagrams


def _read_intro(target: Path) -> str | None:
    """``intro.md`` as LF text, or ``None`` when absent. Unreadable / non-UTF-8 is exit 2."""
    path = target / INTRO_NAME
    if path.is_symlink():
        raise GateError(MSG_SYMLINK_SOURCE.format(path=path.as_posix()))
    if not path.is_file():
        return None
    raw = read_source(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateError(MSG_INTRO_NOT_UTF8.format(path=path.as_posix())) from exc
    return normalize_newlines(text)


def _read_generated_bytes(path: Path) -> bytes | None:
    """An existing generated file's bytes; ``None`` (= stale) when absent, a link or oversized."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as handle:
            raw = handle.read(MAX_GENERATED_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_GENERATED_BYTES:
        return None
    return raw


def _read_generated(path: Path) -> str | None:
    """An existing generated file as LF text; ``None`` (= stale) when absent or unusable."""
    raw = _read_generated_bytes(path)
    if raw is None:
        return None
    try:
        return normalize_newlines(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def _parse_index(text: str | None) -> dict[str, Any] | None:
    if text is None:
        return None
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):  # corrupt, or nested deeper than the parser allows
        return None
    return document if isinstance(document, dict) else None


# --- README (section 4) --------------------------------------------------------------------


def github_anchor(title: str) -> str:
    """GitHub's heading slug: lowercase, punctuation (colons too) dropped, whitespace -> ``-``."""
    out: list[str] = []
    for ch in title.strip().lower():
        if ch.isalnum() or ch in ANCHOR_KEEP_CHARS:
            out.append(ch)
        elif ch.isspace():
            out.append("-")
    return "".join(out)


class _Slugger:
    """Assigns anchors in document order with GitHub's ``-1``, ``-2`` suffixes for repeats."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def slug(self, heading: str) -> str:
        base = github_anchor(INLINE_LINK_RE.sub(r"\1", heading))
        candidate = base
        n = 0
        while candidate in self._seen:
            n += 1
            candidate = f"{base}-{n}"
        self._seen.add(candidate)
        return candidate


def _atx_text(content: str) -> str:
    """ATX heading content without its optional closing ``#`` sequence (CommonMark 4.2)."""
    text = content.strip(" \t")
    unclosed = text.rstrip(ATX_MARKER)
    if unclosed != text and (not unclosed or unclosed[-1] in " \t"):
        text = unclosed.rstrip(" \t")
    return text


def _intro_headings(intro: str) -> Iterator[str]:
    """ATX and setext headings of ``intro.md`` outside fenced code, in document order.

    Each occupies a README anchor. A setext heading is the paragraph directly above a ``===`` /
    ``---`` underline; a ``---`` with no paragraph above it is a thematic break, not a heading.
    """
    fence: str | None = None
    paragraph: list[str] = []
    for line in intro.split("\n"):
        match = FENCE_LINE_RE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            paragraph = []
            continue
        if fence is not None:
            continue
        if not line.strip():
            paragraph = []
            continue
        if paragraph and SETEXT_UNDERLINE_RE.match(line):
            yield " ".join(paragraph)
            paragraph = []
            continue
        heading = ATX_HEADING_RE.match(line)
        if heading:
            yield _atx_text(heading.group(1) or "")
            paragraph = []
            continue
        if BLOCK_START_RE.match(line) or SETEXT_UNDERLINE_RE.match(line):
            paragraph = []  # list item, block quote or thematic break: no heading paragraph
            continue
        if not paragraph and INDENTED_CODE_RE.match(line):
            continue  # indented code block
        paragraph.append(line.strip())


def _longest_backtick_run(text: str) -> int:
    longest = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return longest


def _code_span(text: str) -> str:
    """``text`` as an inline code span that survives backticks inside it."""
    delimiter = "`" * (_longest_backtick_run(text) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{delimiter}{pad}{text}{pad}{delimiter}"


def _link_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _regen_command(source_dir: str, *extra: str) -> str:
    target = f'"{source_dir}"' if any(ch.isspace() for ch in source_dir) else source_dir
    return " ".join([GENERATOR_NAME, "render", target, *extra])


def _marker_comment(*, layouts: bool) -> str:
    """The GENERATED comment; it names the sidecars only when the folder has one (section 2.5)."""
    head = f"{README_MARKER_OPEN}{GENERATED_MARKER}."
    lines = README_LAYOUT_MARKER_LINES if layouts else README_MARKER_LINES
    return "\n".join([head, *(README_MARKER_INDENT + line for line in lines)]) + "\n"


def _diagram_block(diagram: Diagram, fence_block: str) -> str:
    """The mermaid fence, or for a sidecar diagram the section 2.5 ``<picture>`` embed around it."""
    if diagram.layout is None:
        return fence_block
    return README_PICTURE_TEMPLATE.format(
        dark=house_svg_path(diagram.id, RASTER_DARK),
        light=house_svg_path(diagram.id, RASTER_LIGHT),
        alt=html.escape(diagram.layout.alt, quote=True),
        fence=fence_block,
    )


def build_readme(diagrams: Sequence[Diagram], intro: str | None, source_dir: str) -> str:
    """The generated ``README.md`` text (LF, deterministic for identical inputs).

    A sidecar-less folder gets the Phase 1 text byte for byte; a sidecar diagram's section embeds
    its house SVGs (section 2.5) and labels the header Alt as the mermaid source's.
    """
    slugger = _Slugger()
    slugger.slug(README_TITLE)
    intro_text = intro.rstrip("\n") if intro is not None and intro.strip() else None
    if intro_text is not None:
        for heading in _intro_headings(intro_text):
            slugger.slug(heading)
    slugger.slug(README_CONTENTS_HEADING)
    anchors = [slugger.slug(d.header.title) for d in diagrams]
    slugger.slug(README_REGEN_HEADING)
    layouts = any(d.layout is not None for d in diagrams)

    parts: list[str] = [_marker_comment(layouts=layouts), f"# {README_TITLE}\n\n"]
    if intro_text is not None:
        parts.append(f"{intro_text}\n\n")
    parts.append(f"## {README_CONTENTS_HEADING}\n\n")
    for diagram, anchor in zip(diagrams, anchors, strict=True):
        h = diagram.header
        parts.append(f"- [{_link_text(h.title)}](#{anchor}) - {h.summary}\n")

    for diagram in diagrams:
        h = diagram.header
        name = f"{diagram.id}{SOURCE_SUFFIX}"
        parts.append(f"\n---\n\n## {h.title}\n\n{h.summary}\n\n")
        parts.append(f"{README_SOURCE_LABEL} [{_code_span(name)}]({name})\n\n")
        parts.append(f"{README_DRAWN_FROM}\n\n")
        parts.extend(f"- {_code_span(source)}\n" for source in h.sources)
        alt_label = README_ALT_LABEL if diagram.layout is None else README_ALT_SOURCE_LABEL
        parts.append(f"\n{alt_label} {h.alt}\n\n")
        parts.append(f"{README_NOT_SHOWN_LABEL} {h.not_shown}\n\n")
        body = diagram.text.rstrip() + "\n"
        fence = "`" * max(MIN_FENCE, _longest_backtick_run(body) + 1)
        parts.append(_diagram_block(diagram, f"{fence}{MERMAID_FENCE_INFO}\n{body}{fence}\n"))

    parts.append(
        f"\n---\n\n## {README_REGEN_HEADING}\n\n"
        f"This file and `{INDEX_NAME}` are generated from the `*{SOURCE_SUFFIX}` sources and "
        f"`{INTRO_NAME}`.\nAfter editing any of them, run from the project root:\n\n"
        f"```\n{_regen_command(source_dir)}\n```\n\n"
    )
    if layouts:
        parts.append(f"{README_LAYOUT_REGEN}\n\n")
    parts.append(
        f"`{_regen_command(source_dir, '--check')}` writes nothing and exits 1 when this file "
        f"or `{INDEX_NAME}` is out of date.\n"
    )
    return "".join(parts)


# --- workflows.json (section 3.4) ----------------------------------------------------------


def _non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def raster_path(diagram_id: str, theme: str) -> str:
    """The index ``path`` of one raster: ``rasters/<id>-<theme>.png`` (POSIX, TARGET-relative)."""
    return f"{RASTERS_DIR}/{RASTER_FILE.format(id=diagram_id, theme=theme)}"


def house_svg_path(diagram_id: str, theme: str) -> str:
    """The README path of one house SVG: ``rasters/<id>-<theme>.svg`` (POSIX, TARGET-relative)."""
    return f"{RASTERS_DIR}/{HOUSE_SVG_FILE.format(id=diagram_id, theme=theme)}"


def _raster_entry(
    diagram_id: str, theme: str, entry: object, target: Path, *, house: bool
) -> dict[str, Any] | None:
    """A previous ``rasters.<theme>`` entry rebuilt with the exact key set, or ``None``.

    Section 3.4's preserved-or-null rule, read strictly: the entry survives only while the file
    it names still exists AND still matches it (``bytes`` and ``sha256`` of the on-disk file), so
    a raster changed or replaced on disk is never re-published under its old hash. The house
    provenance rule (plan section 12.D) narrows it further: the file must be a house PNG exactly
    when ``house`` (the diagram has a sidecar), so adding or removing a sidecar nulls the entry
    until ``--png``.
    """
    if not isinstance(entry, dict):
        return None
    expected_path = raster_path(diagram_id, theme)
    path, digest, *dims = (entry.get(key) for key in RASTER_ENTRY_KEYS)
    if path != expected_path:
        return None
    if not isinstance(digest, str) or SHA256_HEX_RE.fullmatch(digest) is None:
        return None
    if not all(_non_negative_int(value) for value in dims):
        return None
    on_disk = _read_generated_bytes(
        target.joinpath(RASTERS_DIR, RASTER_FILE.format(id=diagram_id, theme=theme))
    )
    if on_disk is None or len(on_disk) != dims[-1]:
        return None
    if hashlib.sha256(on_disk).hexdigest() != digest:
        return None
    if renderer_mod.is_house_png(on_disk) != house:
        return None
    return dict(zip(RASTER_ENTRY_KEYS, (path, digest, *dims), strict=True))


def _preserved_rasters(
    diagram_id: str, previous: dict[str, Any] | None, target: Path, *, house: bool
) -> dict[str, Any] | None:
    """Section 3.4: keep a previous item's ``rasters`` only when BOTH files still exist.

    ``house`` (the diagram has a sidecar) is the provenance both files must carry.
    """
    if previous is None:
        return None
    items = previous.get(INDEX_ITEMS)
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict) or item.get(INDEX_ITEM_ID) != diagram_id:
            continue
        rasters = item.get(INDEX_ITEM_RASTERS)
        if not isinstance(rasters, dict):
            return None
        entries = {
            t: _raster_entry(diagram_id, t, rasters.get(t), target, house=house)
            for t in RASTER_THEMES
        }
        if any(entry is None for entry in entries.values()):
            return None
        return entries
    return None


def build_index(
    diagrams: Sequence[Diagram],
    source_dir: str,
    generated_at: datetime,
    *,
    previous: dict[str, Any] | None = None,
    target: Path | None = None,
    rasters: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The ``show-your-work.workflows.v1`` document, keys in their fixed order.

    ``rasters`` (id -> freshly rendered entries, ``render --png``) wins when given. Otherwise
    ``previous`` (the parsed existing index) and ``target`` feed the rasters preserved-or-null
    rule; without them every ``rasters`` is ``null``. While a sidecar diagram's ``rasters`` are
    house entries its ``alt`` is the layout ``alt``, else the header Alt.
    """
    if generated_at.utcoffset() != timedelta(0):
        raise ValueError(MSG_STAMP_NOT_UTC)
    items: list[dict[str, Any]] = []
    for d in sorted(diagrams, key=lambda x: x.id):
        if rasters is not None:
            item_rasters: dict[str, Any] | None = rasters[d.id]
        elif target is not None:
            item_rasters = _preserved_rasters(d.id, previous, target, house=d.layout is not None)
        else:
            item_rasters = None
        alt = d.header.alt if d.layout is None or item_rasters is None else d.layout.alt
        values = (
            d.id,
            d.header.title,
            d.header.summary,
            alt,
            d.header.not_shown,
            list(d.header.sources),
            _source_path(source_dir, d.id),
            d.sha256,
            d.engine,
            item_rasters,
        )
        items.append(dict(zip(INDEX_ITEM_KEYS, values, strict=True)))
    stamp = generated_at.astimezone(UTC).isoformat(timespec="seconds")
    top = (INDEX_SCHEMA, f"{GENERATOR_NAME} {__version__}", stamp, source_dir, items)
    return dict(zip(INDEX_KEYS, top, strict=True))


def serialize_index(document: dict[str, Any]) -> str:
    """Canonical serialization: 2-space indent, ASCII-escaped, one trailing LF."""
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


def _valid_stamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return False
    return stamp.utcoffset() == timedelta(0)


def _masked(document: dict[str, Any], *, mask_rasters: bool) -> dict[str, Any]:
    masked = dict(document)
    if INDEX_GENERATED_AT in masked:
        masked[INDEX_GENERATED_AT] = None
    items = masked.get(INDEX_ITEMS)
    if mask_rasters and isinstance(items, list):
        masked[INDEX_ITEMS] = [
            {**item, INDEX_ITEM_RASTERS: None}
            if isinstance(item, dict) and INDEX_ITEM_RASTERS in item
            else item
            for item in items
        ]
    return masked


def index_up_to_date(existing: str | None, fresh: dict[str, Any], *, mask_rasters: bool) -> bool:
    """``existing`` (LF text) equals ``fresh`` with ``generated_at`` (and ``rasters``) masked.

    The existing file must also be the canonical serialization of itself and carry a valid
    UTC ``generated_at``, so a hand-edited or reformatted index counts as stale.
    """
    document = _parse_index(existing)
    if document is None or existing != serialize_index(document):
        return False
    if not _valid_stamp(document.get(INDEX_GENERATED_AT)):
        return False
    return serialize_index(_masked(document, mask_rasters=mask_rasters)) == serialize_index(
        _masked(fresh, mask_rasters=mask_rasters)
    )


# --- writing -------------------------------------------------------------------------------


def write_atomic(path: Path, text: str | bytes, *, mode: int | None = None) -> None:
    """Write ``text`` (UTF-8 encoded; ``bytes`` as-is) through a temp file and ``os.replace``.

    ``os.replace`` swaps the directory entry, so a symlink at ``path`` is replaced, never written
    through. ``mode`` (permission bits) is applied to the temp file before the swap; without it
    the file keeps ``mkstemp``'s owner-only mode. Any I/O failure is :class:`GateError` (exit 2).
    """
    data = text.encode("utf-8") if isinstance(text, str) else text
    tmp: str | None = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
        tmp = None
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise GateError(MSG_CANNOT_WRITE.format(path=path.as_posix(), reason=reason)) from exc
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp)


# --- rasters (section 4 "PNG rendering") ---------------------------------------------------


def _render_rasters(
    diagrams: Sequence[Diagram],
    rasterizer: RasterRenderer,
    svgs: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, bytes]]:
    """Every diagram x theme rasterized IN MEMORY (nothing is written until all succeed).

    A sidecar diagram rasterizes its house SVG from ``svgs`` through
    :func:`show_your_work.renderer.house_png` (tagged), any other diagram its mermaid source
    through :func:`show_your_work.renderer.png`. Both check each canvas against the pixel caps
    before the native rasterizer runs; an over-cap canvas, a renderer fault or a non-PNG result is
    exit 2.
    """
    rendered: dict[str, dict[str, bytes]] = {}
    for diagram in diagrams:
        per_theme: dict[str, bytes] = {}
        for theme in RASTER_THEMES:
            try:
                if diagram.layout is None:
                    data = renderer_mod.png(rasterizer, diagram.text, theme)
                else:
                    data = renderer_mod.house_png(svgs[diagram.id][theme], theme)
                renderer_mod.png_size(data)
            except Exception as exc:  # a renderer fault after the anchors passed: exit 2
                message = MSG_RASTER_FAILED.format(
                    id=diagram.id, theme=theme, detail=error_detail(exc)
                )
                raise GateError(message) from exc
            per_theme[theme] = data
        rendered[diagram.id] = per_theme
    return rendered


def _raster_entries(rendered: Mapping[str, Mapping[str, bytes]]) -> dict[str, dict[str, Any]]:
    """id -> ``{"light": entry, "dark": entry}`` with the exact section 3.4 entry key set."""
    out: dict[str, dict[str, Any]] = {}
    for diagram_id, per_theme in rendered.items():
        entries: dict[str, Any] = {}
        for theme in RASTER_THEMES:
            data = per_theme[theme]
            width, height = renderer_mod.png_size(data)
            values = (
                raster_path(diagram_id, theme),
                hashlib.sha256(data).hexdigest(),
                width,
                height,
                len(data),
            )
            entries[theme] = dict(zip(RASTER_ENTRY_KEYS, values, strict=True))
        out[diagram_id] = entries
    return out


def _os_reason(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def _rasters_dir(target: Path, *, guarded: bool = True) -> Path | None:
    """``TARGET/rasters`` when it is a plain directory, ``None`` when absent; exit 2 otherwise.

    A symlink or a Windows junction (which ``is_symlink`` does not report) is refused, so neither
    writing nor orphan cleanup ever reaches through a link into another directory. Unguarded (a
    run that writes nothing there: a plain render of a sidecar-less folder), such a ``rasters/``
    is skipped silently instead (``None``, plan section 2.5).
    """
    path = target / RASTERS_DIR
    try:
        if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_dir()):
            if not guarded:
                return None
            raise GateError(MSG_RASTERS_NOT_DIR.format(path=path.as_posix()))
        return path if path.is_dir() else None
    except OSError as exc:
        raise GateError(
            MSG_CANNOT_READ.format(path=path.as_posix(), reason=_os_reason(exc))
        ) from exc


def _orphans(directory: Path | None, keep: frozenset[str], suffixes: tuple[str, ...]) -> list[Path]:
    """Regular files directly inside ``directory`` named ``*<suffix>`` and not in ``keep``.

    ``keep`` holds case-folded names, so on a case-insensitive filesystem a just-written raster
    whose directory entry kept an older casing is never mistaken for an orphan. Symlinks,
    directories (and anything beneath them) and other names are never candidates.
    """
    if directory is None:
        return []
    orphans: list[Path] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.name.endswith(suffixes) or entry.name.casefold() in keep:
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                orphans.append(Path(entry.path))
    except OSError as exc:
        path = directory.as_posix()
        raise GateError(MSG_CANNOT_READ.format(path=path, reason=_os_reason(exc))) from exc
    return sorted(orphans, key=lambda p: p.name)


def _is_generated_svg(path: Path) -> bool:
    """A regular UTF-8 file (never a link) shaped like ``draw_svg`` output.

    It starts with ``HOUSE_SVG_ROOT_START`` and ``HOUSE_SVG_MARKER`` is the whole line right after
    the root start tag (closed by its first ``>``: attribute values escape ``>``). Lines compare as
    LF, so a CRLF checkout still counts; the marker text anywhere else does not.
    """
    text = _read_generated(path)
    if text is None or not text.startswith(HOUSE_SVG_ROOT_START):
        return False
    return text.startswith(f"\n{HOUSE_SVG_MARKER}\n", text.find(">") + 1)


def _refuse_unmarked(path: Path) -> None:
    if os.path.lexists(path) and not _is_generated_svg(path):
        message = MSG_SVG_NOT_GENERATED.format(path=path.as_posix(), generator=GENERATOR_NAME)
        raise GateError(message)


def _delete(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError as exc:
        path_text = path.as_posix()
        raise GateError(MSG_CANNOT_DELETE.format(path=path_text, reason=_os_reason(exc))) from exc


@dataclass(frozen=True, slots=True)
class _RasterPlan:
    """What a run would change under ``rasters/``, decided before any write."""

    directory: Path
    exists: bool
    files: tuple[tuple[str, bytes, bool], ...]  # (file name, fresh bytes, already current)
    orphans: tuple[Path, ...]

    def statuses(self, *, check_only: bool) -> list[OutputStatus]:
        changed = STATE_STALE if check_only else STATE_WRITTEN
        removed = STATE_STALE if check_only else STATE_DELETED
        out = [
            OutputStatus(f"{RASTERS_DIR}/{name}", STATE_CURRENT if current else changed)
            for name, _, current in self.files
        ]
        out.extend(OutputStatus(f"{RASTERS_DIR}/{p.name}", removed) for p in self.orphans)
        return out


def _planned(existing: Path | None, name: str, data: bytes) -> tuple[str, bytes, bool]:
    """A PNG raster: current only when the on-disk bytes equal ``data`` exactly."""
    return name, data, existing is not None and _read_generated_bytes(existing / name) == data


def _planned_text(existing: Path | None, name: str, text: str) -> tuple[str, bytes, bool]:
    """A house SVG: compared as LF text, like ``README.md`` (a CRLF checkout stays current)."""
    current = existing is not None and _read_generated(existing / name) == text
    return name, text.encode("utf-8"), current


def _plan_rasters(
    target: Path,
    rendered: Mapping[str, Mapping[str, bytes]] | None,
    svgs: Mapping[str, Mapping[str, str]],
) -> _RasterPlan:
    """The PNGs (``--png`` only) and house SVGs (every sidecar diagram) to write, and the orphans.

    Orphans are the unreferenced PNGs (``--png`` only) and the unreferenced house SVGs whose first
    child is ``HOUSE_SVG_MARKER`` (plan section 2.5). ``rasters/`` is guarded whenever a file may be
    written there; otherwise a link there is skipped silently. A house SVG path that holds anything
    but a marked file is exit 2: it is never overwritten.
    """
    existing = _rasters_dir(target, guarded=rendered is not None or bool(svgs))
    files = [
        _planned(existing, RASTER_FILE.format(id=diagram_id, theme=theme), per_theme[theme])
        for diagram_id, per_theme in (rendered or {}).items()
        for theme in RASTER_THEMES
    ]
    for diagram_id, per_theme in svgs.items():
        for theme in RASTER_THEMES:
            name = HOUSE_SVG_FILE.format(id=diagram_id, theme=theme)
            if existing is not None:
                _refuse_unmarked(existing / name)
            files.append(_planned_text(existing, name, per_theme[theme]))
    keep = frozenset(name.casefold() for name, _, _ in files)
    orphans = [p for p in _orphans(existing, keep, HOUSE_SVG_SUFFIXES) if _is_generated_svg(p)]
    if rendered is not None:
        orphans = _orphans(existing, keep, (RASTER_SUFFIX,)) + orphans
    directory = target / RASTERS_DIR
    return _RasterPlan(directory, existing is not None, tuple(files), tuple(orphans))


def _write_rasters(plan: _RasterPlan) -> None:
    """Create ``rasters/`` when a file must be written and (re)write every changed file."""
    changed = [(name, data) for name, data, current in plan.files if not current]
    if changed and not plan.exists:
        try:
            plan.directory.mkdir()
        except OSError as exc:
            path = plan.directory.as_posix()
            raise GateError(MSG_CANNOT_WRITE.format(path=path, reason=_os_reason(exc))) from exc
    for name, data in changed:
        write_atomic(plan.directory / name, data)


def render_dir(
    target: Path, *, check_only: bool = False, rasterizer: RasterRenderer | None = None
) -> RenderResult:
    """Check, then regenerate (or with ``check_only`` compare) ``README.md`` + ``workflows.json``.

    Every sidecar diagram's house SVGs are drawn in memory (section 2.5). With ``rasterizer``
    (``render --png``) the check renders every file (anchors first), then the mermaid raster
    anchors run when a diagram has no sidecar and the house anchors when one has, and every
    raster is rendered in memory. The write order is then: changed rasters and house SVGs,
    ``README.md`` + ``workflows.json``, and orphan deletion last.

    Raises :class:`GateError` (exit 2) when the gate cannot run or an output cannot be written.
    A failing check set returns exit 1 with nothing written; ``check_only`` returns exit 1 when
    any output is stale.
    """
    target = target.resolve()
    report = check_dir(target, renderer=rasterizer)
    if report.failed:
        return RenderResult(report, (), EXIT_FINDINGS)

    diagrams = _load_diagrams(target)
    svgs = {
        d.id: {theme: draw_svg(d.layout, theme) for theme in RASTER_THEMES}
        for d in diagrams
        if d.layout is not None
    }
    rendered: dict[str, dict[str, bytes]] | None = None
    if rasterizer is not None:
        mermaid = any(d.layout is None for d in diagrams)
        if mermaid and not renderer_mod.raster_anchor_ok(rasterizer):
            raise GateError(MSG_ANCHOR_BROKEN)
        if svgs and not renderer_mod.house_anchor_ok():
            raise GateError(MSG_HOUSE_ANCHOR_BROKEN)
        rendered = _render_rasters(diagrams, rasterizer, svgs)
    intro = _read_intro(target)
    source_dir = source_dir_of(target)
    readme_path = target / README_NAME
    index_path = target / INDEX_NAME
    existing_readme = _read_generated(readme_path)
    existing_index = _read_generated(index_path)

    readme = build_readme(diagrams, intro, source_dir)
    index = build_index(
        diagrams,
        source_dir,
        _utc_now(),
        previous=_parse_index(existing_index),
        target=target,
        rasters=_raster_entries(rendered) if rendered is not None else None,
    )
    readme_current = existing_readme == readme
    mask_rasters = check_only and rendered is None
    index_current = index_up_to_date(existing_index, index, mask_rasters=mask_rasters)
    plan = _plan_rasters(target, rendered, svgs)
    raster_statuses = plan.statuses(check_only=check_only)

    if check_only:
        outputs = (
            *(
                OutputStatus(name, STATE_CURRENT if current else STATE_STALE)
                for name, current in ((README_NAME, readme_current), (INDEX_NAME, index_current))
            ),
            *raster_statuses,
        )
        code = EXIT_FINDINGS if any(o.state == STATE_STALE for o in outputs) else EXIT_OK
        return RenderResult(report, outputs, code)

    _write_rasters(plan)  # before the README and index, so neither names a file not yet written
    statuses: list[OutputStatus] = []
    for path, text, current in (
        (readme_path, readme, readme_current),
        (index_path, serialize_index(index), index_current),
    ):
        if not current:
            write_atomic(path, text)
        statuses.append(OutputStatus(path.name, STATE_CURRENT if current else STATE_WRITTEN))
    for orphan in plan.orphans:  # last: an interrupted run never strands the README or index
        _delete(orphan)
    return RenderResult(report, (*statuses, *raster_statuses), EXIT_OK)
