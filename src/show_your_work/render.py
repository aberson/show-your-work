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
  both referenced files still exist (``null`` otherwise; raster files are never deleted here).

Every reader normalizes CRLF to LF, and every write is LF / UTF-8 through an atomic replace, so
the bytes never depend on ``core.autocrlf``. A generated file whose content is already current
(``workflows.json`` compared with ``generated_at`` masked) is left untouched, so a repeat run
changes no bytes. ``check_only`` (``render --check``) writes nothing and also masks ``rasters``.

With a ``rasterizer`` (``render --png``, section 4) the check runs WITH that renderer, so the
garbage / known-good anchors run before any file is read; then the anchors run again through the
raster path, and every diagram is rasterized for both themes IN MEMORY before anything is
written. Only then are ``rasters/<id>-<theme>.png`` written (byte-identical files are left
alone), then ``README.md`` / ``workflows.json`` with ``rasters`` filled from the fresh bytes
(``path``, ``sha256``, ``width`` / ``height`` from the PNG's IHDR, ``bytes``), and LAST the orphan
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
    error_detail,
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
    MAX_GENERATED_BYTES,
    MERMAID_FENCE_INFO,
    MIN_FENCE,
    MSG_ANCHOR_BROKEN,
    MSG_CANNOT_DELETE,
    MSG_CANNOT_READ,
    MSG_CANNOT_WRITE,
    MSG_INTRO_NOT_UTF8,
    MSG_RASTER_FAILED,
    MSG_RASTERS_NOT_DIR,
    MSG_SOURCE_CHANGED,
    MSG_STAMP_NOT_UTC,
    MSG_SYMLINK_SOURCE,
    NO_PROJECT_ROOT_DIR,
    RASTER_ENTRY_KEYS,
    RASTER_FILE,
    RASTER_SUFFIX,
    RASTER_THEMES,
    RASTERS_DIR,
    README_ALT_LABEL,
    README_CONTENTS_HEADING,
    README_DRAWN_FROM,
    README_NAME,
    README_NOT_SHOWN_LABEL,
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
from show_your_work.renderer import RasterRenderer

__all__ = [
    "Diagram",
    "OutputStatus",
    "RenderResult",
    "build_index",
    "build_readme",
    "github_anchor",
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
    """One checked source: its id, parsed header, LF-normalized text and engine."""

    id: str
    header: Header
    text: str
    sha256: str
    engine: str


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


def _load_diagrams(target: Path) -> list[Diagram]:
    """Re-read every source after ``check`` passed; a file that no longer parses is exit 2."""
    sources, drawings = discover(target)
    drawn = {path.name[: -len(EXCALIDRAW_SUFFIX)] for path in drawings}
    diagrams: list[Diagram] = []
    for path in sources:
        raw = read_source(path)
        text = normalize_newlines(raw.decode("utf-8", errors="replace"))
        header = check_header(text)
        if isinstance(header, HeaderError):
            raise GateError(MSG_SOURCE_CHANGED.format(path=path.as_posix()))
        diagram_id = path.name[: -len(SOURCE_SUFFIX)]
        engine = ENGINE_EXCALIDRAW if diagram_id in drawn else ENGINE_MERMAID
        diagrams.append(Diagram(diagram_id, header, text, sha256_lf(raw), engine))
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


def build_readme(diagrams: Sequence[Diagram], intro: str | None, source_dir: str) -> str:
    """The generated ``README.md`` text (LF, deterministic for identical inputs)."""
    slugger = _Slugger()
    slugger.slug(README_TITLE)
    intro_text = intro.rstrip("\n") if intro is not None and intro.strip() else None
    if intro_text is not None:
        for heading in _intro_headings(intro_text):
            slugger.slug(heading)
    slugger.slug(README_CONTENTS_HEADING)
    anchors = [slugger.slug(d.header.title) for d in diagrams]
    slugger.slug(README_REGEN_HEADING)

    parts: list[str] = [
        f"<!-- {GENERATED_MARKER}.\n"
        f"     Produced by `{GENERATOR_NAME} render` from {INTRO_NAME} and the {SOURCE_SUFFIX}\n"
        "     files in this directory. Edit those and re-run; edits here are overwritten. -->\n",
        f"# {README_TITLE}\n\n",
    ]
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
        parts.append(f"\n{README_ALT_LABEL} {h.alt}\n\n")
        parts.append(f"{README_NOT_SHOWN_LABEL} {h.not_shown}\n\n")
        body = diagram.text.rstrip() + "\n"
        fence = "`" * max(MIN_FENCE, _longest_backtick_run(body) + 1)
        parts.append(f"{fence}{MERMAID_FENCE_INFO}\n{body}{fence}\n")

    parts.append(
        f"\n---\n\n## {README_REGEN_HEADING}\n\n"
        f"This file and `{INDEX_NAME}` are generated from the `*{SOURCE_SUFFIX}` sources and "
        f"`{INTRO_NAME}`.\nAfter editing any of them, run from the project root:\n\n"
        f"```\n{_regen_command(source_dir)}\n```\n\n"
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


def _raster_entry(
    diagram_id: str, theme: str, entry: object, target: Path
) -> dict[str, Any] | None:
    """A previous ``rasters.<theme>`` entry rebuilt with the exact key set, or ``None``.

    Section 3.4's preserved-or-null rule, read strictly: the entry survives only while the file
    it names still exists AND still matches it (``bytes`` and ``sha256`` of the on-disk file), so
    a raster changed or replaced on disk is never re-published under its old hash.
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
    return dict(zip(RASTER_ENTRY_KEYS, (path, digest, *dims), strict=True))


def _preserved_rasters(
    diagram_id: str, previous: dict[str, Any] | None, target: Path
) -> dict[str, Any] | None:
    """Section 3.4: keep a previous item's ``rasters`` only when BOTH files still exist."""
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
        entries = {t: _raster_entry(diagram_id, t, rasters.get(t), target) for t in RASTER_THEMES}
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
    rule; without them every ``rasters`` is ``null``.
    """
    if generated_at.utcoffset() != timedelta(0):
        raise ValueError(MSG_STAMP_NOT_UTC)
    items: list[dict[str, Any]] = []
    for d in sorted(diagrams, key=lambda x: x.id):
        if rasters is not None:
            item_rasters: dict[str, Any] | None = rasters[d.id]
        elif target is not None:
            item_rasters = _preserved_rasters(d.id, previous, target)
        else:
            item_rasters = None
        values = (
            d.id,
            d.header.title,
            d.header.summary,
            d.header.alt,
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
    diagrams: Sequence[Diagram], rasterizer: RasterRenderer
) -> dict[str, dict[str, bytes]]:
    """Every diagram x theme rasterized IN MEMORY (nothing is written until all succeed).

    :func:`show_your_work.renderer.png` checks each canvas against the pixel caps before the
    native rasterizer runs; an over-cap canvas, a renderer fault or a non-PNG result is exit 2.
    """
    rendered: dict[str, dict[str, bytes]] = {}
    for diagram in diagrams:
        per_theme: dict[str, bytes] = {}
        for theme in RASTER_THEMES:
            try:
                data = renderer_mod.png(rasterizer, diagram.text, theme)
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


def _rasters_dir(target: Path) -> Path | None:
    """``TARGET/rasters`` when it is a plain directory, ``None`` when absent; exit 2 otherwise.

    A symlink or a Windows junction (which ``is_symlink`` does not report) is refused, so neither
    writing nor orphan cleanup ever reaches through a link into another directory.
    """
    path = target / RASTERS_DIR
    try:
        if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_dir()):
            raise GateError(MSG_RASTERS_NOT_DIR.format(path=path.as_posix()))
        return path if path.is_dir() else None
    except OSError as exc:
        raise GateError(
            MSG_CANNOT_READ.format(path=path.as_posix(), reason=_os_reason(exc))
        ) from exc


def _orphan_pngs(directory: Path | None, keep: frozenset[str]) -> list[Path]:
    """Regular ``*.png`` files directly inside ``directory`` whose name is not in ``keep``.

    ``keep`` holds case-folded names, so on a case-insensitive filesystem a just-written raster
    whose directory entry kept an older casing is never mistaken for an orphan. Symlinks,
    directories (and anything beneath them) and non-PNG names are never candidates.
    """
    if directory is None:
        return []
    orphans: list[Path] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.name.endswith(RASTER_SUFFIX) or entry.name.casefold() in keep:
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                orphans.append(Path(entry.path))
    except OSError as exc:
        path = directory.as_posix()
        raise GateError(MSG_CANNOT_READ.format(path=path, reason=_os_reason(exc))) from exc
    return sorted(orphans, key=lambda p: p.name)


def _delete(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError as exc:
        path_text = path.as_posix()
        raise GateError(MSG_CANNOT_DELETE.format(path=path_text, reason=_os_reason(exc))) from exc


@dataclass(frozen=True, slots=True)
class _RasterPlan:
    """What ``render --png`` would change under ``rasters/``, decided before any write."""

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


def _plan_rasters(target: Path, rendered: Mapping[str, Mapping[str, bytes]]) -> _RasterPlan:
    existing = _rasters_dir(target)
    directory = target / RASTERS_DIR
    files: list[tuple[str, bytes, bool]] = []
    for diagram_id, per_theme in rendered.items():
        for theme in RASTER_THEMES:
            name = RASTER_FILE.format(id=diagram_id, theme=theme)
            data = per_theme[theme]
            current = existing is not None and _read_generated_bytes(directory / name) == data
            files.append((name, data, current))
    keep = frozenset(name.casefold() for name, _, _ in files)
    orphans = tuple(_orphan_pngs(existing, keep))
    return _RasterPlan(directory, existing is not None, tuple(files), orphans)


def _write_rasters(plan: _RasterPlan) -> None:
    """Phase 1 of a ``--png`` write: create ``rasters/`` and (re)write every changed raster."""
    if not plan.exists:
        try:
            plan.directory.mkdir()
        except OSError as exc:
            path = plan.directory.as_posix()
            raise GateError(MSG_CANNOT_WRITE.format(path=path, reason=_os_reason(exc))) from exc
    for name, data, current in plan.files:
        if not current:
            write_atomic(plan.directory / name, data)


def render_dir(
    target: Path, *, check_only: bool = False, rasterizer: RasterRenderer | None = None
) -> RenderResult:
    """Check, then regenerate (or with ``check_only`` compare) ``README.md`` + ``workflows.json``.

    With ``rasterizer`` (``render --png``) the check renders every file (anchors first), the
    raster anchors run and every raster is rendered in memory. The write order is then: changed
    rasters, ``README.md`` + ``workflows.json``, and orphan deletion last.

    Raises :class:`GateError` (exit 2) when the gate cannot run or an output cannot be written.
    A failing check set returns exit 1 with nothing written; ``check_only`` returns exit 1 when
    any output is stale.
    """
    target = target.resolve()
    report = check_dir(target, renderer=rasterizer)
    if report.failed:
        return RenderResult(report, (), EXIT_FINDINGS)

    diagrams = _load_diagrams(target)
    rendered: dict[str, dict[str, bytes]] | None = None
    if rasterizer is not None:
        if not renderer_mod.raster_anchor_ok(rasterizer):
            raise GateError(MSG_ANCHOR_BROKEN)
        rendered = _render_rasters(diagrams, rasterizer)
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
    plan = _plan_rasters(target, rendered) if rendered is not None else None
    raster_statuses = plan.statuses(check_only=check_only) if plan is not None else []

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

    if plan is not None:
        _write_rasters(plan)  # before the index, so it never names a file not yet written
    statuses: list[OutputStatus] = []
    for path, text, current in (
        (readme_path, readme, readme_current),
        (index_path, serialize_index(index), index_current),
    ):
        if not current:
            write_atomic(path, text)
        statuses.append(OutputStatus(path.name, STATE_CURRENT if current else STATE_WRITTEN))
    if plan is not None:
        for orphan in plan.orphans:  # last: an interrupted run never strands the index
            _delete(orphan)
    return RenderResult(report, (*statuses, *raster_statuses), EXIT_OK)
