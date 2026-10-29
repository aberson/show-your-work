"""The ``check`` logic (plan sections 3.2, 3.3, 3.5 and 4): findings with stable codes.

:func:`check_dir` validates the top-level ``*.mmd`` files of one ``docs/workflows/`` folder
(discovery is non-recursive, so ``rasters/`` and nested folders are never scanned):

* id grammar on the file stem (rejected, never sanitized) and case-folded duplicate ids;
* an ``<id>.excalidraw.svg`` without a sibling ``<id>.mmd`` is an ``excalidraw-orphan`` row;
* a layout sidecar ``<id>.layout.json`` (Phase 2 section 2.4) is validated by
  :func:`show_your_work.layout.layout_findings`; ``layout-invalid`` / ``layout-bound`` /
  ``layout-stale`` attach to the ``<id>.mmd`` row and a sidecar without a sibling ``<id>.mmd`` is
  its own ``layout-orphan`` row. A folder without sidecars reports exactly as in Phase 1;
* the header contract via the single parser (:func:`show_your_work.header.check_header`);
* whole-file ASCII and no machine-absolute ``Users`` path;
* the legibility budget measurable from source: longest node / edge label, subgraph count and
  ``subgraph`` / ``end`` balance;
* with a renderer: the render anchors first (before any file is listed or read), then a real
  render per file and exact node / edge counts from the SVG. Without one, counts stay ``None``.

Conditions under which the gate cannot run at all raise :class:`GateError` (exit 2): a missing
or unlistable TARGET, no ``*.mmd``, a broken anchor, and -- because ``check`` must stay safe on
hostile input -- an unreadable source, a symlinked ``*.mmd`` or one over ``MAX_SOURCE_BYTES``, and a
symlinked sidecar or one over ``MAX_LAYOUT_BYTES`` (section 2.4).
The label regexes are linear-time (see :mod:`show_your_work.constants`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from show_your_work import renderer as renderer_mod
from show_your_work.constants import (
    CODE_ABS_PATH,
    CODE_BUDGET_EDGE_LABEL,
    CODE_BUDGET_EDGES,
    CODE_BUDGET_NODE_LABEL,
    CODE_BUDGET_NODES,
    CODE_BUDGET_SUBGRAPHS,
    CODE_EXCALIDRAW_ORPHAN,
    CODE_ID_DUPLICATE,
    CODE_ID_GRAMMAR,
    CODE_LAYOUT_ORPHAN,
    CODE_NON_ASCII,
    CODE_RENDER_ERROR,
    CODE_SUBGRAPH_UNBALANCED,
    COMMENT_PREFIX,
    EDGE_LABEL_RES,
    EXCALIDRAW_SUFFIX,
    EXIT_FINDINGS,
    EXIT_OK,
    ID_RE,
    LABEL_STRIP_CHARS,
    LAYOUT_SUFFIX,
    MAX_EDGE_LABEL,
    MAX_EDGES,
    MAX_LAYOUT_BYTES,
    MAX_NODE_LABEL,
    MAX_NODES,
    MAX_RENDER_ERROR_DETAIL,
    MAX_SOURCE_BYTES,
    MAX_SUBGRAPHS,
    MSG_ANCHOR_BROKEN,
    MSG_CANNOT_READ,
    MSG_LAYOUT_TOO_LARGE,
    MSG_NO_SOURCES,
    MSG_ORPHAN_HINT,
    MSG_SOURCE_TOO_LARGE,
    MSG_SYMLINK_LAYOUT,
    MSG_SYMLINK_SOURCE,
    MSG_TARGET_NOT_DIR,
    NODE_LABEL_RES,
    SOURCE_SUFFIX,
    SUBGRAPH_END_RE,
    SUBGRAPH_RE,
    USERS_PATH_RE,
)
from show_your_work.header import HeaderError, check_header, clip_detail, normalize_newlines
from show_your_work.layout import layout_findings
from show_your_work.renderer import Renderer

__all__ = [
    "FileResult",
    "Finding",
    "GateError",
    "Report",
    "check_dir",
    "check_source",
    "discover",
    "discover_layouts",
    "error_detail",
    "read_layout",
    "read_source",
]


class GateError(Exception):
    """The gate could not run (exit 2): bad target, no sources, broken renderer anchor."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class Finding:
    """One violation in one file, with a stable code from :mod:`show_your_work.constants`."""

    file: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class FileResult:
    """The outcome for one discovered ``.mmd`` (or one orphan ``.excalidraw.svg``)."""

    file: str
    id: str
    nodes: int | None
    edges: int | None
    findings: tuple[Finding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings


@dataclass(frozen=True, slots=True)
class Report:
    """Every file result for one TARGET, sorted by file name."""

    target: Path
    files: tuple[FileResult, ...]
    rendered: bool

    @property
    def total(self) -> int:
        return len(self.files)

    @property
    def passed(self) -> int:
        return sum(1 for f in self.files if f.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def exit_code(self) -> int:
        return EXIT_FINDINGS if self.failed else EXIT_OK


def _cannot_read(path: Path, exc: OSError) -> GateError:
    reason = exc.strerror or type(exc).__name__
    return GateError(MSG_CANNOT_READ.format(path=path.as_posix(), reason=reason))


def read_source(
    path: Path, *, limit: int = MAX_SOURCE_BYTES, too_large_msg: str = MSG_SOURCE_TOO_LARGE
) -> bytes:
    """Read one file, at most ``limit`` bytes; any I/O failure or oversize is exit 2.

    ``too_large_msg`` is the ``{path}`` message template raised when the file exceeds ``limit``.
    """
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError as exc:
        raise _cannot_read(path, exc) from exc
    if len(raw) > limit:
        raise GateError(too_large_msg.format(path=path.as_posix()))
    return raw


def read_layout(path: Path) -> bytes:
    """The layout sidecar read API: ``read_source`` with the layout limit and too-large message.

    At most ``MAX_LAYOUT_BYTES`` (64 KiB) are read; a larger sidecar raises :class:`GateError`
    (exit 2) with ``MSG_LAYOUT_TOO_LARGE``, which names that limit.
    """
    return read_source(path, limit=MAX_LAYOUT_BYTES, too_large_msg=MSG_LAYOUT_TOO_LARGE)


def discover(target: Path) -> tuple[list[Path], list[Path]]:
    """Return ``(sources, excalidraw_svgs)``: regular files directly inside ``target``.

    A symlinked ``*.mmd`` raises :class:`GateError` (``check`` never follows a link out of
    TARGET); a listing failure raises it too.
    """
    sources: list[Path] = []
    drawings: list[Path] = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: p.name)
        for entry in entries:
            if entry.name.endswith(EXCALIDRAW_SUFFIX):
                if entry.is_file():
                    drawings.append(entry)
            elif entry.name.endswith(SOURCE_SUFFIX):
                if entry.is_symlink():
                    raise GateError(MSG_SYMLINK_SOURCE.format(path=entry.as_posix()))
                if entry.is_file():
                    sources.append(entry)
    except OSError as exc:
        raise _cannot_read(target, exc) from exc
    return sources, drawings


def discover_layouts(target: Path) -> list[Path]:
    """Return the layout sidecars (regular ``*.layout.json`` files) directly inside ``target``.

    A symlinked sidecar raises :class:`GateError` (never followed out of TARGET), as does a
    listing failure. Nested folders (``rasters/``) are never scanned.
    """
    layouts: list[Path] = []
    try:
        for entry in sorted(target.iterdir(), key=lambda p: p.name):
            if not entry.name.endswith(LAYOUT_SUFFIX):
                continue
            if entry.is_symlink():
                raise GateError(MSG_SYMLINK_LAYOUT.format(path=entry.as_posix()))
            if entry.is_file():
                layouts.append(entry)
    except OSError as exc:
        raise _cannot_read(target, exc) from exc
    return layouts


def _stem(name: str, suffix: str) -> str:
    return name[: -len(suffix)]


def _body(text: str) -> str:
    """The diagram body: every line that is not a ``%%`` comment."""
    return "\n".join(
        line for line in text.split("\n") if not line.strip().startswith(COMMENT_PREFIX)
    )


def _longest(patterns: Iterable[re.Pattern[str]], body: str) -> int:
    lengths = [len(m.group(1).strip(LABEL_STRIP_CHARS)) for p in patterns for m in p.finditer(body)]
    return max(lengths, default=0)


def _without_node_labels(body: str) -> str:
    """``body`` with node labels blanked, so a ``|`` inside ``A["p|q"]`` is not an edge label."""
    for pattern in NODE_LABEL_RES:
        body = pattern.sub(" ", body)
    return body


def _first_non_ascii_line(raw: bytes) -> int | None:
    for number, line in enumerate(raw.split(b"\n"), start=1):
        if not line.isascii():
            return number
    return None


def _source_findings(name: str, raw: bytes) -> list[Finding]:
    """Every check that needs only the file's bytes (no renderer)."""
    findings: list[Finding] = []
    bad_line = _first_non_ascii_line(raw)
    if bad_line is not None:
        findings.append(
            Finding(
                name, CODE_NON_ASCII, f"file is not ASCII (first non-ASCII byte on line {bad_line})"
            )
        )
    text = normalize_newlines(raw.decode("utf-8", errors="replace"))
    if USERS_PATH_RE.search(text):
        findings.append(
            Finding(name, CODE_ABS_PATH, "file contains a machine-absolute 'Users' path")
        )

    header = check_header(text)
    if isinstance(header, HeaderError):
        findings.extend(Finding(name, f.code, f.message) for f in header.findings)

    body = _body(text)
    node_label = _longest(NODE_LABEL_RES, body)
    if node_label > MAX_NODE_LABEL:
        findings.append(
            Finding(
                name,
                CODE_BUDGET_NODE_LABEL,
                f"longest node label is {node_label} chars (max {MAX_NODE_LABEL})",
            )
        )
    edge_label = _longest(EDGE_LABEL_RES, _without_node_labels(body))
    if edge_label > MAX_EDGE_LABEL:
        findings.append(
            Finding(
                name,
                CODE_BUDGET_EDGE_LABEL,
                f"longest edge label is {edge_label} chars (max {MAX_EDGE_LABEL})",
            )
        )
    subgraphs = len(SUBGRAPH_RE.findall(body))
    ends = len(SUBGRAPH_END_RE.findall(body))
    if subgraphs > MAX_SUBGRAPHS:
        findings.append(
            Finding(
                name,
                CODE_BUDGET_SUBGRAPHS,
                f"{subgraphs} subgraphs (max {MAX_SUBGRAPHS})",
            )
        )
    if subgraphs != ends:
        findings.append(
            Finding(
                name,
                CODE_SUBGRAPH_UNBALANCED,
                f"{subgraphs} 'subgraph' lines but {ends} 'end' lines",
            )
        )
    return findings


def error_detail(exc: BaseException) -> str:
    """A renderer error as one whitespace-collapsed line of at most ``MAX_RENDER_ERROR_DETAIL``
    characters (plus ``...``); the exception type name when the message is empty."""
    return clip_detail(str(exc), MAX_RENDER_ERROR_DETAIL, type(exc).__name__)


def _render_findings(
    name: str, text: str, renderer: Renderer
) -> tuple[int | None, int | None, list[Finding]]:
    """Render one source; return exact counts and any render / node / edge budget findings."""
    try:
        svg = renderer.svg(text)
    except Exception as exc:  # any renderer failure is a per-file finding, never a crash
        detail = error_detail(exc)
        return None, None, [Finding(name, CODE_RENDER_ERROR, f"mermaid render failed: {detail}")]
    nodes, edges = renderer_mod.svg_counts(svg)
    findings: list[Finding] = []
    if nodes > MAX_NODES:
        findings.append(Finding(name, CODE_BUDGET_NODES, f"{nodes} nodes (max {MAX_NODES})"))
    if edges > MAX_EDGES:
        findings.append(Finding(name, CODE_BUDGET_EDGES, f"{edges} edges (max {MAX_EDGES})"))
    return nodes, edges, findings


def check_source(name: str, raw: bytes, *, renderer: Renderer | None = None) -> FileResult:
    """Check one ``.mmd`` file's name and bytes (no directory-level checks)."""
    diagram_id = _stem(name, SOURCE_SUFFIX)
    findings: list[Finding] = []
    if ID_RE.fullmatch(diagram_id) is None:
        findings.append(
            Finding(
                name,
                CODE_ID_GRAMMAR,
                f"id {diagram_id!r} does not match {ID_RE.pattern} (rename the file; ids are "
                "never sanitized)",
            )
        )
    findings.extend(_source_findings(name, raw))
    nodes: int | None = None
    edges: int | None = None
    if renderer is not None:
        text = normalize_newlines(raw.decode("utf-8", errors="replace"))
        nodes, edges, rendered = _render_findings(name, text, renderer)
        findings.extend(rendered)
    return FileResult(name, diagram_id, nodes, edges, tuple(findings))


def _duplicate_findings(sources: Sequence[Path]) -> dict[str, list[Finding]]:
    """Case-folded id collisions (possible on Windows via case variants), keyed by file name."""
    groups: dict[str, list[str]] = {}
    for path in sources:
        groups.setdefault(_stem(path.name, SOURCE_SUFFIX).casefold(), []).append(path.name)
    out: dict[str, list[Finding]] = {}
    for names in groups.values():
        if len(names) < 2:
            continue
        for name in names:
            others = ", ".join(repr(n) for n in names if n != name)
            out[name] = [
                Finding(name, CODE_ID_DUPLICATE, f"id collides (case-folded) with {others}")
            ]
    return out


def _orphan_results(
    sources: Sequence[Path], companions: Sequence[Path], suffix: str, code: str, hint: str
) -> list[FileResult]:
    """Each ``<id><suffix>`` companion without a sibling ``<id>.mmd`` is its own ``code`` row."""
    source_names = {p.name for p in sources}
    results: list[FileResult] = []
    for path in companions:
        diagram_id = _stem(path.name, suffix)
        sibling = diagram_id + SOURCE_SUFFIX
        if sibling in source_names:
            continue
        finding = Finding(path.name, code, f"{path.name!r} has no sibling {sibling!r}; {hint}")
        results.append(FileResult(path.name, diagram_id, None, None, (finding,)))
    return results


def _with_layout(
    result: FileResult,
    sidecar: Path | None,
    source_raw: bytes,
    drawings: Sequence[Path],
) -> FileResult:
    """``result`` plus the findings of its sidecar (if any); a no-op without one."""
    if sidecar is None:
        return result
    raw = read_layout(sidecar)
    drawing = result.id + EXCALIDRAW_SUFFIX
    beside = any(d.name == drawing for d in drawings)
    sidecar_findings = layout_findings(result.id, raw, source_raw, beside_drawing=beside)
    if not sidecar_findings:
        return result
    extra = tuple(Finding(result.file, f.code, f.message) for f in sidecar_findings)
    return FileResult(
        result.file, result.id, result.nodes, result.edges, (*result.findings, *extra)
    )


def check_dir(target: Path, *, renderer: Renderer | None = None) -> Report:
    """Check every top-level ``*.mmd`` in ``target``; raise :class:`GateError` if it cannot run.

    With ``renderer``, the render anchors (garbage must raise, known-good must render) run
    **before** any file is listed or read; a failing anchor aborts the whole run. Unreadable,
    symlinked or oversized sources and sidecars abort it too (exit 2: the gate cannot run safely).
    """
    if renderer is not None and not renderer_mod.anchor_ok(renderer):
        raise GateError(MSG_ANCHOR_BROKEN)
    if not target.is_dir():
        raise GateError(MSG_TARGET_NOT_DIR.format(path=target.as_posix()))
    sources, drawings = discover(target)
    if not sources:
        raise GateError(MSG_NO_SOURCES.format(path=target.as_posix()))
    layouts = discover_layouts(target)
    sidecars = {_stem(p.name, LAYOUT_SUFFIX) + SOURCE_SUFFIX: p for p in layouts}

    duplicates = _duplicate_findings(sources)
    results: list[FileResult] = []
    for path in sources:
        raw = read_source(path)
        result = check_source(path.name, raw, renderer=renderer)
        extra = duplicates.get(path.name)
        if extra:
            result = FileResult(
                result.file, result.id, result.nodes, result.edges, (*extra, *result.findings)
            )
        results.append(_with_layout(result, sidecars.get(path.name), raw, drawings))
    results.extend(
        _orphan_results(
            sources, drawings, EXCALIDRAW_SUFFIX, CODE_EXCALIDRAW_ORPHAN, MSG_ORPHAN_HINT
        )
    )
    layout_orphans = _orphan_results(
        sources, layouts, LAYOUT_SUFFIX, CODE_LAYOUT_ORPHAN, MSG_ORPHAN_HINT
    )
    # Exit-2 size gate for orphan sidecars: an orphan is never validated, but an oversized or
    # unreadable one is still a gate error, like any sidecar. The bytes are not needed.
    for orphan in layout_orphans:
        read_layout(target / orphan.file)
    results.extend(layout_orphans)
    results.sort(key=lambda r: r.file)
    return Report(target, tuple(results), rendered=renderer is not None)
