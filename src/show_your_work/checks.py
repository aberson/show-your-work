"""The ``check`` logic (plan sections 3.2, 3.3, 3.5 and 4): findings with stable codes.

:func:`check_dir` validates the top-level ``*.mmd`` files of one ``docs/workflows/`` folder
(discovery is non-recursive, so ``rasters/`` and nested folders are never scanned):

* id grammar on the file stem (rejected, never sanitized) and case-folded duplicate ids;
* an ``<id>.excalidraw.svg`` without a sibling ``<id>.mmd`` is an ``excalidraw-orphan`` row;
* the header contract via the single parser (:func:`show_your_work.header.check_header`);
* whole-file ASCII and no machine-absolute ``Users`` path;
* the legibility budget measurable from source: longest node / edge label, subgraph count and
  ``subgraph`` / ``end`` balance;
* with a renderer: the render anchors first (before any file is listed or read), then a real
  render per file and exact node / edge counts from the SVG. Without one, counts stay ``None``.

Conditions under which the gate cannot run at all raise :class:`GateError` (exit 2): a missing
or unlistable TARGET, no ``*.mmd``, a broken anchor, and -- because ``check`` must stay safe on
hostile input -- an unreadable source, a symlinked ``*.mmd`` or one over ``MAX_SOURCE_BYTES``.
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
    MAX_EDGE_LABEL,
    MAX_EDGES,
    MAX_NODE_LABEL,
    MAX_NODES,
    MAX_RENDER_ERROR_DETAIL,
    MAX_SOURCE_BYTES,
    MAX_SUBGRAPHS,
    MSG_ANCHOR_BROKEN,
    MSG_CANNOT_READ,
    MSG_NO_SOURCES,
    MSG_SOURCE_TOO_LARGE,
    MSG_SYMLINK_SOURCE,
    MSG_TARGET_NOT_DIR,
    NODE_LABEL_RES,
    SOURCE_SUFFIX,
    SUBGRAPH_END_RE,
    SUBGRAPH_RE,
    USERS_PATH_RE,
)
from show_your_work.header import HeaderError, check_header, normalize_newlines
from show_your_work.renderer import Renderer

__all__ = [
    "FileResult",
    "Finding",
    "GateError",
    "Report",
    "check_dir",
    "check_source",
    "discover",
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


def read_source(path: Path) -> bytes:
    """Read one source, at most ``MAX_SOURCE_BYTES``; any I/O failure or oversize is exit 2."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise _cannot_read(path, exc) from exc
    if len(raw) > MAX_SOURCE_BYTES:
        raise GateError(MSG_SOURCE_TOO_LARGE.format(path=path.as_posix()))
    return raw


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


def _render_findings(
    name: str, text: str, renderer: Renderer
) -> tuple[int | None, int | None, list[Finding]]:
    """Render one source; return exact counts and any render / node / edge budget findings."""
    try:
        svg = renderer.svg(text)
    except Exception as exc:  # any renderer failure is a per-file finding, never a crash
        detail = " ".join(str(exc).split()) or type(exc).__name__
        if len(detail) > MAX_RENDER_ERROR_DETAIL:
            detail = detail[:MAX_RENDER_ERROR_DETAIL] + "..."
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


def _orphan_results(sources: Sequence[Path], drawings: Sequence[Path]) -> list[FileResult]:
    source_names = {p.name for p in sources}
    results: list[FileResult] = []
    for path in drawings:
        diagram_id = _stem(path.name, EXCALIDRAW_SUFFIX)
        sibling = diagram_id + SOURCE_SUFFIX
        if sibling in source_names:
            continue
        finding = Finding(
            path.name,
            CODE_EXCALIDRAW_ORPHAN,
            f"{path.name!r} has no sibling {sibling!r}; author in mermaid first",
        )
        results.append(FileResult(path.name, diagram_id, None, None, (finding,)))
    return results


def check_dir(target: Path, *, renderer: Renderer | None = None) -> Report:
    """Check every top-level ``*.mmd`` in ``target``; raise :class:`GateError` if it cannot run.

    With ``renderer``, the render anchors (garbage must raise, known-good must render) run
    **before** any file is listed or read; a failing anchor aborts the whole run. Unreadable,
    symlinked or oversized sources abort it too (exit 2: the gate cannot run safely).
    """
    if renderer is not None and not renderer_mod.anchor_ok(renderer):
        raise GateError(MSG_ANCHOR_BROKEN)
    if not target.is_dir():
        raise GateError(MSG_TARGET_NOT_DIR.format(path=target.as_posix()))
    sources, drawings = discover(target)
    if not sources:
        raise GateError(MSG_NO_SOURCES.format(path=target.as_posix()))

    duplicates = _duplicate_findings(sources)
    results: list[FileResult] = []
    for path in sources:
        result = check_source(path.name, read_source(path), renderer=renderer)
        extra = duplicates.get(path.name)
        if extra:
            result = FileResult(
                result.file, result.id, result.nodes, result.edges, (*extra, *result.findings)
            )
        results.append(result)
    results.extend(_orphan_results(sources, drawings))
    results.sort(key=lambda r: r.file)
    return Report(target, tuple(results), rendered=renderer is not None)
