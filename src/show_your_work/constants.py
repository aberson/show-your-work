"""The ONE home for show-your-work's data-shape constants.

Budget numbers, header field names and bounds, the label / id / path regexes, finding codes,
schema strings, exit codes and the known-garbage render anchor live here and nowhere else
(``docs/plans/master_plan.md`` sections 3.3, 3.4 and 4; design decision DD1). Every other module
imports from this one; ``tests/test_header.py`` scans the rest of the package with ``ast`` to
fail CI if any of these names is re-defined elsewhere.

Collections are built with ``tuple(...)`` / ``frozenset(...)`` calls so each is a distinct runtime
object: an identity (``is``) test against an importer's binding then proves the importer shares
this object rather than holding an equal copy.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

# ---------------------------------------------------------------------------
# Exit codes (section 4, appendix 12.B)
# ---------------------------------------------------------------------------

EXIT_OK: Final = 0
EXIT_FINDINGS: Final = 1
EXIT_GATE: Final = 2
EXIT_USAGE: Final = 64

# ---------------------------------------------------------------------------
# Header contract (section 3.3)
# ---------------------------------------------------------------------------

#: A leading UTF-8 byte-order mark; readers strip one before parsing (the whole-file ASCII
#: check still reports it as ``non-ascii``).
BOM: Final = "\ufeff"

#: Prefix that marks a mermaid comment line; the header is the leading block of these.
COMMENT_PREFIX: Final = "%%"

#: Prefix that marks a ``Sources:`` bullet continuation (after the comment prefix is stripped).
BULLET_PREFIX: Final = "-"

FIELD_TITLE: Final = "title"
FIELD_SUMMARY: Final = "summary"
FIELD_SOURCES: Final = "sources"
FIELD_NOT_SHOWN: Final = "not_shown"
FIELD_ALT: Final = "alt"

#: The five required header fields, in canonical order (also the ``Header`` dataclass order).
HEADER_FIELDS: Final[tuple[str, ...]] = tuple(
    [FIELD_TITLE, FIELD_SUMMARY, FIELD_SOURCES, FIELD_NOT_SHOWN, FIELD_ALT]
)

LABEL_SUMMARY: Final = "Summary"
LABEL_SOURCES: Final = "Sources"
LABEL_NOT_SHOWN: Final = "Not shown"
LABEL_ALT: Final = "Alt"

#: Recognised label (case-folded) -> field name. Any other label is a hard error.
HEADER_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        LABEL_SUMMARY.casefold(): FIELD_SUMMARY,
        LABEL_SOURCES.casefold(): FIELD_SOURCES,
        LABEL_NOT_SHOWN.casefold(): FIELD_NOT_SHOWN,
        LABEL_ALT.casefold(): FIELD_ALT,
    }
)

MAX_TITLE: Final = 120
MAX_SUMMARY: Final = 160
MAX_NOT_SHOWN: Final = 400
MAX_ALT: Final = 240

#: Field -> maximum length in characters. ``sources`` has no length bound (entry count >= 1).
HEADER_BOUNDS: Final[Mapping[str, int]] = MappingProxyType(
    {
        FIELD_TITLE: MAX_TITLE,
        FIELD_SUMMARY: MAX_SUMMARY,
        FIELD_NOT_SHOWN: MAX_NOT_SHOWN,
        FIELD_ALT: MAX_ALT,
    }
)

#: Minimum number of ``Sources:`` entries.
MIN_SOURCES: Final = 1

#: A label line, matched on the text after ``%%``: the prototype ``LABEL_RE`` plus a required
#: space-or-end after the colon, so an unbulleted ``https://...`` source is never a label.
LABEL_RE: Final = re.compile(r"^([A-Za-z][A-Za-z ]{0,30}):(\s|$)")

#: A machine-absolute path in a ``Sources:`` entry (rejected, section 3.3).
ABS_PATH_RE: Final = re.compile(r"^[A-Za-z]:[\\/]")

#: A machine-absolute ``Users`` path anywhere in a file (carried from ``check_diagrams.py``).
USERS_PATH_RE: Final = re.compile(r"(?i)[a-z]:[\\/]+Users")

#: Highest byte / code point value allowed (the whole file MUST be ASCII).
MAX_ASCII: Final = 127

#: Code points below this are C0 control characters (``MAX_ASCII``, DEL, is one too).
MIN_PRINTABLE: Final = 0x20

# ---------------------------------------------------------------------------
# Diagram identity (section 3.2)
# ---------------------------------------------------------------------------

ID_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SOURCE_SUFFIX: Final = ".mmd"
EXCALIDRAW_SUFFIX: Final = ".excalidraw.svg"

# ---------------------------------------------------------------------------
# Template markers (section 3.3)
# ---------------------------------------------------------------------------

TEMPLATE_MARKER_MMD: Final = "%% show-your-work:template"
TEMPLATE_MARKER_MD: Final = "<!-- show-your-work:template -->"

# ---------------------------------------------------------------------------
# Legibility budget (section 3.3, values from check_diagrams.py)
# ---------------------------------------------------------------------------

MAX_NODES: Final = 14
MAX_EDGES: Final = 20
MAX_NODE_LABEL: Final = 60
MAX_EDGE_LABEL: Final = 40
MAX_SUBGRAPHS: Final = 2

#: The budget as reported in the ``--json`` summary (section 4 key names, deterministic order).
BUDGET: Final[Mapping[str, int]] = MappingProxyType(
    {
        "nodes": MAX_NODES,
        "edges": MAX_EDGES,
        "node_label": MAX_NODE_LABEL,
        "edge_label": MAX_EDGE_LABEL,
        "subgraphs": MAX_SUBGRAPHS,
    }
)

#: The human summary line (section 4); ``{passed}`` / ``{total}`` are filled per run.
SUMMARY_LINE: Final = (
    f"{{passed}}/{{total}} pass (budget: <={MAX_NODES} nodes, <={MAX_EDGES} edges, "
    f"node label <={MAX_NODE_LABEL}, edge label <={MAX_EDGE_LABEL}, "
    f"<={MAX_SUBGRAPHS} subgraphs)"
)

#: Printed whenever ``check`` runs without ``--render``: node/edge counts are never silently passed.
COUNTS_NOTE: Final = (
    "NOTE: node/edge counts are exact only under --render (mermaidx); "
    "without it they show '-' and the node/edge budget is not checked"
)

#: A ``render-error`` message keeps at most this many characters of the renderer's error text.
MAX_RENDER_ERROR_DETAIL: Final = 200

#: Shown in the human table for a count that was not rendered (``null`` in ``--json``).
COUNT_NOT_RENDERED: Final = "-"

#: Importable name of the optional pinned renderer (extra ``render``; the pin lives in pyproject).
RENDERER_MODULE: Final = "mermaidx"

#: Largest ``.mmd`` file ``check`` will read; a larger one makes the gate exit 2 (section 4: the
#: gate cannot run safely on it) rather than reading an unbounded attacker-sized file.
MAX_SOURCE_BYTES: Final = 256 * 1024

#: Known-good render anchor: rendering this MUST succeed and yield at least one node, so a
#: renderer that raises on everything cannot pass the garbage anchor (good + garbage calibration).
KNOWN_GOOD: Final = "flowchart LR\n    A[Start] --> B[End]\n"

#: Known-garbage render anchor: rendering this MUST raise (carried verbatim).
GARBAGE: Final = (
    "flowchart LR\n    A[unclosed --> ((( )))\n    ->> %%%% not a diagram\n    subgraph\n"
)

#: SVG count regexes (from ``check_diagrams.py:counts_from_svg``).
SVG_NODE_RE: Final = re.compile(r'class="[^"]*\bnode\b[^"]*"')
SVG_EDGE_RE: Final = re.compile(r'class="[^"]*\b(?:edge-thickness|flowchart-link)\b[^"]*"')
SVG_EDGE_FALLBACK_RE: Final = re.compile(r'\bid="L[_-]')

#: Source label-length regexes, applied to the body (non-``%%`` lines). These are the
#: ``check_diagrams.py`` originals (plan section 3.3) with two linear-time changes: the quoted
#: node-label pattern is tried only from the FIRST bracket of a run (the lookbehind; any match
#: must consume the whole run anyway, so results are identical -- without it a run of ``[(`` is
#: quadratic), and the pipe edge label is one line-bounded capture (the original's ``\s*`` /
#: lazy-class / ``\s*`` overlap backtracked cubically on ``|`` + spaces). Quoted captures still
#: cross newlines, as in the originals: a multi-line quoted label is measured. Captured text is
#: stripped of whitespace and quotes (``LABEL_STRIP_CHARS``) before its length is compared.
NODE_LABEL_RES: Final[tuple[re.Pattern[str], ...]] = tuple(
    [
        re.compile(r'(?<![\[\(\{])[\[\(\{]+\s*"([^"]*)"\s*[\]\)\}]+'),
        re.compile(r'\[([^"\[\]\n]+)\]'),
    ]
)
EDGE_LABEL_RES: Final[tuple[re.Pattern[str], ...]] = tuple(
    [
        re.compile(r"\|([^|\n]+)\|"),
        re.compile(r'--\s+"([^"]+)"\s+-->'),
        re.compile(r'-\.\s+"([^"]+)"\s+\.->'),
    ]
)
#: Characters stripped from both ends of a captured label before measuring it.
LABEL_STRIP_CHARS: Final = ' \t"'
SUBGRAPH_RE: Final = re.compile(r"^[ \t]*subgraph\b", re.M)
SUBGRAPH_END_RE: Final = re.compile(r"^[ \t]*end[ \t]*$", re.M)

# ---------------------------------------------------------------------------
# Schema strings (sections 3.4 and 4)
# ---------------------------------------------------------------------------

INDEX_SCHEMA: Final = "show-your-work.workflows.v1"
REPORT_SCHEMA: Final = "show-your-work.report.v1"

# ---------------------------------------------------------------------------
# Generated outputs: README.md and the workflows.json index (sections 3.1, 3.4 and 4)
# ---------------------------------------------------------------------------

#: Hand-authored prose the generated README embeds verbatim (optional: omitted when absent).
INTRO_NAME: Final = "intro.md"
README_NAME: Final = "README.md"
INDEX_NAME: Final = "workflows.json"

#: The ``"generator"`` value is ``f"{GENERATOR_NAME} {__version__}"``.
GENERATOR_NAME: Final = "show-your-work"

#: Top-level ``workflows.json`` keys, in their fixed serialization order (section 3.4).
INDEX_KEYS: Final[tuple[str, ...]] = tuple(
    ["schema", "generator", "generated_at", "source_dir", "items"]
)
#: The EXACT key set of one ``items[]`` entry, in its fixed order (section 3.4). Unknown or
#: missing keys are a generator bug.
INDEX_ITEM_KEYS: Final[tuple[str, ...]] = tuple(
    [
        "id",
        "label",
        "summary",
        "alt",
        "not_shown",
        "sources",
        "source_path",
        "source_sha256",
        "engine",
        "rasters",
    ]
)
INDEX_GENERATED_AT: Final = "generated_at"
INDEX_ITEMS: Final = "items"
INDEX_ITEM_ID: Final = "id"
INDEX_ITEM_RASTERS: Final = "rasters"

#: ``engine`` is mermaid unless an ``<id>.excalidraw.svg`` sibling exists (section 3.5).
ENGINE_MERMAID: Final = "mermaid"
ENGINE_EXCALIDRAW: Final = "excalidraw"

#: The project root is the nearest ancestor of TARGET holding this file or directory.
GIT_MARKER: Final = ".git"
#: ``source_dir`` when TARGET has no ``.git`` ancestor (section 3.4).
NO_PROJECT_ROOT_DIR: Final = "."

#: Raster layout (section 3.1): ``rasters/<id>-<theme>.png``; one entry per theme, keyed in order.
RASTERS_DIR: Final = "rasters"
RASTER_THEMES: Final[tuple[str, ...]] = tuple(["light", "dark"])
RASTER_FILE: Final = "{id}-{theme}.png"
#: The exact key set of one ``rasters.<theme>`` entry, in order (section 3.4).
RASTER_ENTRY_KEYS: Final[tuple[str, ...]] = tuple(["path", "sha256", "width", "height", "bytes"])
SHA256_HEX_RE: Final = re.compile(r"^[0-9a-f]{64}$")

#: An existing generated file larger than this is never read back; it is simply stale.
MAX_GENERATED_BYTES: Final = 16 * 1024 * 1024

#: The leading marker of the generated README (section 3.1).
GENERATED_MARKER: Final = "GENERATED FILE - DO NOT EDIT"
README_TITLE: Final = "Workflow diagrams"
README_CONTENTS_HEADING: Final = "Contents"
README_REGEN_HEADING: Final = "Regenerating"
README_ALT_LABEL: Final = "**Alt:**"
README_NOT_SHOWN_LABEL: Final = "**Deliberately not shown:**"
README_DRAWN_FROM: Final = "Drawn from:"
README_SOURCE_LABEL: Final = "Source:"
MERMAID_FENCE_INFO: Final = "mermaid"
#: Shortest markdown code fence; a longer one is used when the body holds a backtick run.
MIN_FENCE: Final = 3
#: Characters GitHub keeps in a heading anchor besides letters and digits (spaces become ``-``).
ANCHOR_KEEP_CHARS: Final = "-_"
#: An ATX heading line in ``intro.md`` (its anchor occupies a slug in the README). Group 1 is
#: the raw content; the optional closing ``#`` sequence is stripped in code (``ATX_MARKER``),
#: because a lazy capture followed by optional trailing groups backtracks quadratically.
ATX_HEADING_RE: Final = re.compile(r"^ {0,3}#{1,6}(?:[ \t](.*))?$")
#: The ATX heading marker character (also the closing-sequence character).
ATX_MARKER: Final = "#"
#: A fenced-code delimiter line in ``intro.md`` (headings inside fences are not headings).
FENCE_LINE_RE: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})")
#: A setext underline (``===`` / ``---``); under a paragraph line it makes that paragraph a
#: heading, after a blank line a ``---`` is a thematic break instead.
SETEXT_UNDERLINE_RE: Final = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
#: A line that opens a list item or block quote (never part of a setext heading's paragraph).
BLOCK_START_RE: Final = re.compile(r"^ {0,3}(?:[-*+][ \t]|\d{1,9}[.)][ \t]|>)")
#: Indentation that makes a line outside a paragraph an indented code block.
INDENTED_CODE_RE: Final = re.compile(r"^(?: {4}|\t)")
#: An inline link or image; GitHub slugs a heading from its rendered text (group 1). Neither
#: class may cross its own opening delimiter (``[`` in the text, ``(`` / whitespace in the URL),
#: so each attempt stops at the next candidate and the scan stays linear on hostile input.
INLINE_LINK_RE: Final = re.compile(r"!?\[([^\[\]]*)\]\([^()\s]*\)")

#: Per-output outcome of ``render`` (human output and :class:`render.OutputStatus`).
STATE_WRITTEN: Final = "written"
STATE_CURRENT: Final = "up to date"
STATE_STALE: Final = "stale"

#: ``build_index`` refuses a naive or non-UTC ``generated_at`` (section 3.4).
MSG_STAMP_NOT_UTC: Final = "generated_at must carry a UTC offset of exactly zero"

#: Exit 2 when a generated file cannot be written.
MSG_CANNOT_WRITE: Final = "cannot write {path}: {reason}"
#: Exit 2 when ``intro.md`` is not valid UTF-8.
MSG_INTRO_NOT_UTF8: Final = "cannot read {path}: not valid UTF-8"
#: Exit 2 when a source changed between ``check`` and ``render`` and no longer parses.
MSG_SOURCE_CHANGED: Final = "source {path} changed during render and no longer passes check"
#: Exit 2 until ``render --png`` lands (build step 6).
MSG_PNG_UNAVAILABLE: Final = "--png is not available yet; run render without --png"
#: Printed when ``render`` refuses a failing set (exit 1, nothing written).
MSG_RENDER_REFUSED: Final = "refusing to render: {failed} of {total} file(s) fail check"
#: Printed per stale generated file under ``render --check``.
MSG_STALE: Final = "{name} is out of date; run '{command}'"

# ---------------------------------------------------------------------------
# CLI target resolution and gate messages (section 4)
# ---------------------------------------------------------------------------

#: Default TARGET, relative to cwd: ``./docs/workflows``.
DEFAULT_TARGET_PARTS: Final[tuple[str, ...]] = tuple(["docs", "workflows"])

#: Exit 2 when no TARGET is given and cwd has no ``docs/workflows`` (documented in section 4).
MSG_NO_DEFAULT_TARGET: Final = (
    "no docs/workflows at {path}; pass a target or run 'show-your-work init'"
)
#: Exit 2 when an explicit TARGET is missing or is not a directory.
MSG_TARGET_NOT_DIR: Final = "target is not a directory: {path}"
#: Exit 2 when TARGET holds no top-level ``*.mmd`` file.
MSG_NO_SOURCES: Final = "no *.mmd files in {path}"
#: Exit 2 for ``--render`` (and later ``--png``) when the optional renderer is not importable.
MSG_RENDERER_MISSING: Final = (
    "mermaidx is not installed; --render needs the optional 'render' extra (uv sync --extra render)"
)
#: Exit 2 when the render anchors fail (DD5): garbage rendered, or known-good did not render.
MSG_ANCHOR_BROKEN: Final = (
    "renderer anchor broken: the known-garbage fixture must raise and a known-good diagram "
    "must render; no render result can be trusted"
)
#: Exit 2 when the optional renderer is installed but fails to import or initialise.
MSG_RENDERER_BROKEN: Final = "mermaidx failed to load ({reason}); --render cannot run"
#: Exit 2 when TARGET or a source cannot be listed or read (permissions, locks, I/O errors).
MSG_CANNOT_READ: Final = "cannot read {path}: {reason}"
#: Exit 2 for a symlinked source: ``check`` never follows a link out of TARGET.
MSG_SYMLINK_SOURCE: Final = "refusing to read symlinked source {path}"
#: Exit 2 for a source larger than ``MAX_SOURCE_BYTES``.
MSG_SOURCE_TOO_LARGE: Final = (
    f"source {{path}} is larger than {MAX_SOURCE_BYTES} bytes; refusing to read it"
)

# ---------------------------------------------------------------------------
# Finding codes (section 4) -- stable strings, defined once
# ---------------------------------------------------------------------------

CODE_HEADER_MISSING_TITLE: Final = "header-missing-title"
CODE_HEADER_MISSING_SUMMARY: Final = "header-missing-summary"
CODE_HEADER_MISSING_SOURCES: Final = "header-missing-sources"
CODE_HEADER_MISSING_NOT_SHOWN: Final = "header-missing-not-shown"
CODE_HEADER_MISSING_ALT: Final = "header-missing-alt"
CODE_HEADER_BOUND: Final = "header-bound"
CODE_HEADER_UNKNOWN_LABEL: Final = "header-unknown-label"
CODE_HEADER_STRAY_LINE: Final = "header-stray-line"
CODE_ALT_REPEATS_TITLE: Final = "alt-repeats-title"
CODE_ALT_NOT_ASCII: Final = "alt-not-ascii"
CODE_ABS_PATH: Final = "abs-path"
CODE_NON_ASCII: Final = "non-ascii"
CODE_ID_GRAMMAR: Final = "id-grammar"
CODE_ID_DUPLICATE: Final = "id-duplicate"
CODE_EXCALIDRAW_ORPHAN: Final = "excalidraw-orphan"
CODE_BUDGET_NODES: Final = "budget-nodes"
CODE_BUDGET_EDGES: Final = "budget-edges"
CODE_BUDGET_NODE_LABEL: Final = "budget-node-label"
CODE_BUDGET_EDGE_LABEL: Final = "budget-edge-label"
CODE_BUDGET_SUBGRAPHS: Final = "budget-subgraphs"
CODE_SUBGRAPH_UNBALANCED: Final = "subgraph-unbalanced"
CODE_RENDER_ERROR: Final = "render-error"

#: Required field -> the finding code emitted when it is absent or empty.
MISSING_FIELD_CODES: Final[Mapping[str, str]] = MappingProxyType(
    {
        FIELD_TITLE: CODE_HEADER_MISSING_TITLE,
        FIELD_SUMMARY: CODE_HEADER_MISSING_SUMMARY,
        FIELD_SOURCES: CODE_HEADER_MISSING_SOURCES,
        FIELD_NOT_SHOWN: CODE_HEADER_MISSING_NOT_SHOWN,
        FIELD_ALT: CODE_HEADER_MISSING_ALT,
    }
)

#: Every stable finding code, in the section 4 order.
FINDING_CODES: Final[tuple[str, ...]] = tuple(
    [
        CODE_HEADER_MISSING_TITLE,
        CODE_HEADER_MISSING_SUMMARY,
        CODE_HEADER_MISSING_SOURCES,
        CODE_HEADER_MISSING_NOT_SHOWN,
        CODE_HEADER_MISSING_ALT,
        CODE_HEADER_BOUND,
        CODE_HEADER_UNKNOWN_LABEL,
        CODE_HEADER_STRAY_LINE,
        CODE_ALT_REPEATS_TITLE,
        CODE_ALT_NOT_ASCII,
        CODE_ABS_PATH,
        CODE_NON_ASCII,
        CODE_ID_GRAMMAR,
        CODE_ID_DUPLICATE,
        CODE_EXCALIDRAW_ORPHAN,
        CODE_BUDGET_NODES,
        CODE_BUDGET_EDGES,
        CODE_BUDGET_NODE_LABEL,
        CODE_BUDGET_EDGE_LABEL,
        CODE_BUDGET_SUBGRAPHS,
        CODE_SUBGRAPH_UNBALANCED,
        CODE_RENDER_ERROR,
    ]
)
