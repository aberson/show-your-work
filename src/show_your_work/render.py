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
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from show_your_work import __version__
from show_your_work.checks import GateError, Report, check_dir, discover, read_source
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
    MSG_CANNOT_WRITE,
    MSG_INTRO_NOT_UTF8,
    MSG_SOURCE_CHANGED,
    MSG_STAMP_NOT_UTC,
    MSG_SYMLINK_SOURCE,
    NO_PROJECT_ROOT_DIR,
    RASTER_ENTRY_KEYS,
    RASTER_FILE,
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
    STATE_STALE,
    STATE_WRITTEN,
)
from show_your_work.header import Header, HeaderError, check_header, normalize_newlines

__all__ = [
    "Diagram",
    "OutputStatus",
    "RenderResult",
    "build_index",
    "build_readme",
    "github_anchor",
    "index_up_to_date",
    "project_root",
    "render_dir",
    "serialize_index",
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


def _sha256_lf(raw: bytes) -> str:
    """SHA-256 over the bytes after CRLF -> LF normalization (section 3.4)."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


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
        diagrams.append(Diagram(diagram_id, header, text, _sha256_lf(raw), engine))
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


def _read_generated(path: Path) -> str | None:
    """An existing generated file as LF text; ``None`` (= stale) when absent or unusable."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as handle:
            raw = handle.read(MAX_GENERATED_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_GENERATED_BYTES:
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


def _raster_entry(
    diagram_id: str, theme: str, entry: object, target: Path
) -> dict[str, Any] | None:
    """A previous ``rasters.<theme>`` entry rebuilt with the exact key set, or ``None``."""
    if not isinstance(entry, dict):
        return None
    expected_path = f"{RASTERS_DIR}/{RASTER_FILE.format(id=diagram_id, theme=theme)}"
    path, digest, *dims = (entry.get(key) for key in RASTER_ENTRY_KEYS)
    if path != expected_path:
        return None
    if not isinstance(digest, str) or SHA256_HEX_RE.fullmatch(digest) is None:
        return None
    if not all(_non_negative_int(value) for value in dims):
        return None
    try:
        exists = target.joinpath(
            RASTERS_DIR, RASTER_FILE.format(id=diagram_id, theme=theme)
        ).is_file()
    except OSError:
        exists = False
    if not exists:
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
) -> dict[str, Any]:
    """The ``show-your-work.workflows.v1`` document, keys in their fixed order.

    ``previous`` (the parsed existing index) and ``target`` feed the rasters preserved-or-null
    rule; without them every ``rasters`` is ``null``.
    """
    if generated_at.utcoffset() != timedelta(0):
        raise ValueError(MSG_STAMP_NOT_UTC)
    items: list[dict[str, Any]] = []
    for d in sorted(diagrams, key=lambda x: x.id):
        rasters = _preserved_rasters(d.id, previous, target) if target is not None else None
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
            rasters,
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


def write_atomic(path: Path, text: str, *, mode: int | None = None) -> None:
    """Write UTF-8 / LF bytes through a temp file in the same directory and ``os.replace``.

    ``os.replace`` swaps the directory entry, so a symlink at ``path`` is replaced, never written
    through. ``mode`` (permission bits) is applied to the temp file before the swap; without it
    the file keeps ``mkstemp``'s owner-only mode. Any I/O failure is :class:`GateError` (exit 2).
    """
    data = text.encode("utf-8")
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


def render_dir(target: Path, *, check_only: bool = False) -> RenderResult:
    """Check, then regenerate (or with ``check_only`` compare) ``README.md`` + ``workflows.json``.

    Raises :class:`GateError` (exit 2) when the gate cannot run or an output cannot be written.
    A failing check set returns exit 1 with nothing written; ``check_only`` returns exit 1 when
    either output is stale.
    """
    target = target.resolve()
    report = check_dir(target)
    if report.failed:
        return RenderResult(report, (), EXIT_FINDINGS)

    diagrams = _load_diagrams(target)
    intro = _read_intro(target)
    source_dir = source_dir_of(target)
    readme_path = target / README_NAME
    index_path = target / INDEX_NAME
    existing_readme = _read_generated(readme_path)
    existing_index = _read_generated(index_path)

    readme = build_readme(diagrams, intro, source_dir)
    index = build_index(
        diagrams, source_dir, _utc_now(), previous=_parse_index(existing_index), target=target
    )
    readme_current = existing_readme == readme
    index_current = index_up_to_date(existing_index, index, mask_rasters=check_only)

    if check_only:
        outputs = tuple(
            OutputStatus(name, STATE_CURRENT if current else STATE_STALE)
            for name, current in ((README_NAME, readme_current), (INDEX_NAME, index_current))
        )
        code = EXIT_FINDINGS if any(o.state == STATE_STALE for o in outputs) else EXIT_OK
        return RenderResult(report, outputs, code)

    statuses: list[OutputStatus] = []
    for path, text, current in (
        (readme_path, readme, readme_current),
        (index_path, serialize_index(index), index_current),
    ):
        if not current:
            write_atomic(path, text)
        statuses.append(OutputStatus(path.name, STATE_CURRENT if current else STATE_WRITTEN))
    return RenderResult(report, tuple(statuses), EXIT_OK)
