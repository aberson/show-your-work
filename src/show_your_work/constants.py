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

#: Highest printable code point (``MIN_PRINTABLE`` is the lowest; ``MAX_ASCII``, DEL, is not one).
MAX_PRINTABLE: Final = MAX_ASCII - 1

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
# init templates (sections 3.6 and 4, design decision DD6)
# ---------------------------------------------------------------------------

#: The starter diagram ``init`` copies beside ``intro.md`` (``INTRO_NAME``).
EXAMPLE_NAME: Final = "example-workflow.mmd"

#: Package-relative directory of the shipped templates (the wheel force-includes the repo's
#: ``templates/workflows/`` here; see ``pyproject.toml``).
TEMPLATE_PACKAGE_PARTS: Final[tuple[str, ...]] = tuple(["templates", "workflows"])

#: A file proving a directory is this tool's source checkout (the editable-install fallback
#: looks for ``templates/workflows/`` only beside it).
SOURCE_CHECKOUT_MARKER: Final = "pyproject.toml"

#: Largest existing file ``init --force`` reads to look for a template marker; anything larger
#: is user content by definition and is never replaced.
MAX_TEMPLATE_BYTES: Final = 64 * 1024

#: Per-file outcome of ``init`` (human output and :class:`scaffold.ScaffoldAction`).
INIT_CREATED: Final = "created"
INIT_KEPT: Final = "kept"
INIT_REPLACED: Final = "replaced"

#: Exit 2 when TARGET (or a parent) cannot be created.
MSG_CANNOT_CREATE: Final = "cannot create {path}: {reason}"
#: Exit 2 when the installed package carries no templates (a broken install).
MSG_TEMPLATES_MISSING: Final = "init templates not found ({name}); reinstall show-your-work"
#: Printed when the scaffolded set fails check (exit 1; nothing is rendered).
MSG_INIT_CHECK_FAILED: Final = (
    "{failed} of {total} file(s) in {path} fail check; fix them, then run 'show-your-work render'"
)

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
#: Largest layout sidecar ``check`` will read; a larger one is a gate error (exit 2, section 2.4).
MAX_LAYOUT_BYTES: Final = 64 * 1024

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

#: ``init``'s templates, in copy order: file name -> the last-line marker that makes an existing
#: file of that name template-origin, so ``init --force`` may replace it (sections 3.3, DD6).
TEMPLATE_FILES: Final[Mapping[str, str]] = MappingProxyType(
    {INTRO_NAME: TEMPLATE_MARKER_MD, EXAMPLE_NAME: TEMPLATE_MARKER_MMD}
)

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
RASTER_LIGHT: Final = "light"
RASTER_DARK: Final = "dark"
RASTER_THEMES: Final[tuple[str, ...]] = tuple([RASTER_LIGHT, RASTER_DARK])
RASTER_FILE: Final = "{id}-{theme}.png"
#: The exact key set of one ``rasters.<theme>`` entry, in order (section 3.4).
RASTER_ENTRY_KEYS: Final[tuple[str, ...]] = tuple(["path", "sha256", "width", "height", "bytes"])
#: A SHA-256 hex digest is exactly this many lowercase hex characters (``SHA256_HEX_RE``).
SHA256_HEX_CHARS: Final = 64
#: The one regex built from a constant, so the hex length has one source (``SHA256_HEX_CHARS``).
SHA256_HEX_RE: Final = re.compile(rf"^[0-9a-f]{{{SHA256_HEX_CHARS}}}$")

# ---------------------------------------------------------------------------
# Frozen raster parameters for ``render --png`` (section 4 "PNG rendering", DD5). An unpinned
# value here silently changes every raster hash and dimension a downstream consumer declares.
# ---------------------------------------------------------------------------

#: Size multiplier passed to ``png(scale=...)``: a 2x render of the diagram's natural size.
RASTER_SCALE: Final = 2
#: ``flowchart.wrappingWidth`` injected into every raster render; without it mermaidx breaks long
#: tokens mid-word (spike section "Fidelity").
RASTER_WRAPPING_WIDTH: Final = 400
#: Raster theme -> the mermaid ``theme`` injected for it (keys match ``RASTER_THEMES``).
RASTER_MERMAID_THEMES: Final[Mapping[str, str]] = MappingProxyType(
    {RASTER_LIGHT: "default", RASTER_DARK: "dark"}
)
#: Raster theme -> the OPAQUE background passed to ``png(background=...)``; never transparent.
RASTER_BACKGROUNDS: Final[Mapping[str, str]] = MappingProxyType(
    {
        RASTER_LIGHT: "#ffffff",
        # Dark = mermaid's dark-theme `background` variable: confirmed in the mermaidx 0.9.5 bundle,
        # mermaidx/assets/mermaid.js (mermaid 11.16.0), dark Theme class `this.background="#333"`.
        RASTER_DARK: "#333333",
    }
)
#: The mermaid front-matter config block prepended to a source for a raster render only (the
#: committed sources stay config-free). ``{theme}`` / ``{wrapping_width}`` are filled per render.
FRONT_MATTER_TEMPLATE: Final = (
    "---\nconfig:\n  theme: {theme}\n  flowchart:\n    wrappingWidth: {wrapping_width}\n---\n"
)
#: Every raster file ends with this suffix; orphan cleanup considers only regular files that
#: end with it, directly inside ``RASTERS_DIR`` (never recursing, never following a symlink).
RASTER_SUFFIX: Final = ".png"
#: The 8-byte PNG signature, followed by the IHDR chunk (length, ``IHDR`` tag, width, height).
PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
PNG_IHDR_TAG: Final = b"IHDR"
#: Byte offsets inside a PNG: the IHDR chunk tag, then the big-endian width / height words.
PNG_IHDR_TAG_OFFSET: Final = 12
PNG_WIDTH_OFFSET: Final = 16
PNG_HEADER_BYTES: Final = 24

#: Raster canvas caps, in pixels AFTER ``RASTER_SCALE``, checked against the SVG-derived size
#: BEFORE ``png()`` runs. A budget-passing diagram can still carry a ``%%{init}%%`` directive that
#: inflates fonts / spacing; the native rasterizer then tries a multi-hundred-GB allocation and
#: aborts the whole process instead of raising. Far above any legible 14-node diagram (the
#: realistic fixtures top out near 2900 x 1700 at 2x).
MAX_RASTER_SIDE: Final = 16_384
MAX_RASTER_PIXELS: Final = 50_000_000
#: The root ``<svg>`` tag's ``viewBox="min-x min-y width height"`` (searched in the tag only).
SVG_VIEWBOX_RE: Final = re.compile(r'\sviewBox="([^"]*)"')
SVG_VIEWBOX_FIELDS: Final = 4
#: Absolute numeric ``width`` / ``height`` attributes on the root tag (``100%`` never matches;
#: the leading whitespace keeps ``stroke-width`` / ``max-width`` from matching).
SVG_WIDTH_ATTR_RE: Final = re.compile(r'\swidth="([0-9]+(?:\.[0-9]+)?)(?:px)?"')
SVG_HEIGHT_ATTR_RE: Final = re.compile(r'\sheight="([0-9]+(?:\.[0-9]+)?)(?:px)?"')

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
#: ``render --png`` removed an orphan raster (section 4 safety rules).
STATE_DELETED: Final = "deleted"

#: ``build_index`` refuses a naive or non-UTC ``generated_at`` (section 3.4).
MSG_STAMP_NOT_UTC: Final = "generated_at must carry a UTC offset of exactly zero"

#: Exit 2 when a generated file cannot be written.
MSG_CANNOT_WRITE: Final = "cannot write {path}: {reason}"
#: Exit 2 when ``intro.md`` is not valid UTF-8.
MSG_INTRO_NOT_UTF8: Final = "cannot read {path}: not valid UTF-8"
#: Exit 2 when a source changed between ``check`` and ``render`` and no longer parses.
MSG_SOURCE_CHANGED: Final = "source {path} changed during render and no longer passes check"
#: Exit 2 when a raster render fails or does not return a PNG (the anchors passed, so this is a
#: renderer fault, not a diagram finding).
MSG_RASTER_FAILED: Final = "cannot rasterize {id} ({theme}): {detail}"
#: The detail of ``MSG_RASTER_FAILED`` (exit 2, nothing written) for an over-cap canvas.
MSG_RASTER_TOO_LARGE: Final = (
    f"canvas would be {{width}}x{{height}} px (max {MAX_RASTER_SIDE} px per side, "
    f"{MAX_RASTER_PIXELS} px in total)"
)
#: The detail of ``MSG_RASTER_FAILED`` when the SVG carries no usable size to bound the canvas.
MSG_SVG_NO_SIZE: Final = "SVG has no usable viewBox; cannot bound the raster size"
#: Exit 2 when ``rasters`` exists but is a symlink or not a directory (never written through).
MSG_RASTERS_NOT_DIR: Final = "refusing to use {path}: not a plain directory"
#: Exit 2 when an orphan raster cannot be deleted.
MSG_CANNOT_DELETE: Final = "cannot delete {path}: {reason}"
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
#: Exit 2 for ``--render`` / ``--png`` when the optional renderer is not importable.
RENDERER_MISSING_TEMPLATE: Final = (
    "mermaidx is not installed; {flag} needs the optional 'render' extra (uv sync --extra render)"
)
MSG_RENDERER_MISSING: Final = RENDERER_MISSING_TEMPLATE.format(flag="--render")
MSG_PNG_RENDERER_MISSING: Final = RENDERER_MISSING_TEMPLATE.format(flag="--png")
#: Exit 2 when the render anchors fail (DD5): garbage rendered, or known-good did not render.
MSG_ANCHOR_BROKEN: Final = (
    "renderer anchor broken: the known-garbage fixture must raise and a known-good diagram "
    "must render; no render result can be trusted"
)
#: Exit 2 when the optional renderer is installed but fails to import or initialise.
RENDERER_BROKEN_TEMPLATE: Final = "mermaidx failed to load ({{reason}}); {flag} cannot run"
MSG_RENDERER_BROKEN: Final = RENDERER_BROKEN_TEMPLATE.format(flag="--render")
MSG_PNG_RENDERER_BROKEN: Final = RENDERER_BROKEN_TEMPLATE.format(flag="--png")
#: Exit 2 when TARGET or a source cannot be listed or read (permissions, locks, I/O errors).
MSG_CANNOT_READ: Final = "cannot read {path}: {reason}"
#: Exit 2 for a symlinked source: ``check`` never follows a link out of TARGET.
MSG_SYMLINK_SOURCE: Final = "refusing to read symlinked source {path}"
#: Exit 2 for a source larger than ``MAX_SOURCE_BYTES``.
MSG_SOURCE_TOO_LARGE: Final = (
    f"source {{path}} is larger than {MAX_SOURCE_BYTES} bytes; refusing to read it"
)
#: Exit 2 for a sidecar over ``MAX_LAYOUT_BYTES`` (the message also names the limit in KiB).
MSG_LAYOUT_TOO_LARGE: Final = (
    f"layout sidecar {{path}} is larger than {MAX_LAYOUT_BYTES} bytes "
    f"({MAX_LAYOUT_BYTES // 1024} KiB); refusing to read it"
)
#: Exit 2 for a symlinked sidecar: ``check`` never follows a link out of TARGET.
MSG_SYMLINK_LAYOUT: Final = "refusing to read symlinked layout sidecar {path}"
#: The hint closing every orphan finding (an ``.excalidraw`` drawing or a layout sidecar).
MSG_ORPHAN_HINT: Final = "author in mermaid first"

# ---------------------------------------------------------------------------
# Layout sidecar contract (Phase 2 section 2.4)
# ---------------------------------------------------------------------------

#: A diagram opts into the house layout with ``<id>.layout.json`` directly beside ``<id>.mmd``.
LAYOUT_SUFFIX: Final = ".layout.json"
#: The exact ``schema`` value of a sidecar.
LAYOUT_SCHEMA: Final = "show-your-work.layout.v1"

#: Sidecar field names (section 2.4), each defined once.
LAYOUT_SCHEMA_KEY: Final = "schema"
LAYOUT_SHA_KEY: Final = "source_sha256"
LAYOUT_ALT_KEY: Final = "alt"
LAYOUT_LANES_KEY: Final = "lanes"
LAYOUT_LOOPS_KEY: Final = "loops"
LAYOUT_REJOIN_KEY: Final = "rejoin"
LANE_LABEL_KEY: Final = "label"
LANE_IN_KEY: Final = "in"
LANE_OUT_KEY: Final = "out"
LANE_NODES_KEY: Final = "nodes"
NODE_NAME_KEY: Final = "name"
NODE_SUB_KEY: Final = "sub"
NODE_SIDE_KEY: Final = "side"
NODE_BRANCH_LABEL_KEY: Final = "branch_label"
LOOP_LANE_KEY: Final = "lane"
LOOP_FROM_KEY: Final = "from"
LOOP_TO_KEY: Final = "to"
REJOIN_FROM_LANE_KEY: Final = "from_lane"
REJOIN_FROM_NODE_KEY: Final = "from_node"
REJOIN_TO_LANE_KEY: Final = "to_lane"
REJOIN_TO_NODE_KEY: Final = "to_node"
REJOIN_LABEL_KEY: Final = "label"

#: The EXACT key set of each sidecar object (unknown or missing keys are ``layout-invalid``).
LAYOUT_KEYS: Final[tuple[str, ...]] = tuple(
    [
        LAYOUT_SCHEMA_KEY,
        LAYOUT_SHA_KEY,
        LAYOUT_ALT_KEY,
        LAYOUT_LANES_KEY,
        LAYOUT_LOOPS_KEY,
        LAYOUT_REJOIN_KEY,
    ]
)
LANE_KEYS: Final[tuple[str, ...]] = tuple(
    [LANE_LABEL_KEY, LANE_IN_KEY, LANE_OUT_KEY, LANE_NODES_KEY]
)
NODE_KEYS: Final[tuple[str, ...]] = tuple(
    [NODE_NAME_KEY, NODE_SUB_KEY, NODE_SIDE_KEY, NODE_BRANCH_LABEL_KEY]
)
LOOP_KEYS: Final[tuple[str, ...]] = tuple([LOOP_LANE_KEY, LOOP_FROM_KEY, LOOP_TO_KEY])
REJOIN_KEYS: Final[tuple[str, ...]] = tuple(
    [
        REJOIN_FROM_LANE_KEY,
        REJOIN_FROM_NODE_KEY,
        REJOIN_TO_LANE_KEY,
        REJOIN_TO_NODE_KEY,
        REJOIN_LABEL_KEY,
    ]
)

#: Bounds (section 2.4, including its text-fit limits). ``alt`` reuses ``MAX_ALT``.
MIN_LAYOUT_LANES: Final = 2
MAX_LAYOUT_LANES: Final = 6
MIN_LANE_NODES: Final = 1
MAX_LANE_NODES: Final = 4
MAX_LANE_LABEL: Final = 16
MAX_NODE_NAME: Final = 22
MAX_NODE_SUB: Final = 27
MAX_BRANCH_LABEL: Final = 14
MAX_REJOIN_LABEL: Final = 40
#: At most this many loops per lane.
MAX_LOOPS_PER_LANE: Final = 1
#: A lane label: uppercase letters, digits, space, ``+``, ``-``, ``/`` (length checked apart).
LANE_LABEL_RE: Final = re.compile(r"^[A-Z0-9 +/-]*$")
#: The human-readable wording of the characters ``LANE_LABEL_RE`` allows, used in the
#: ``layout-bound`` message. It must describe ``LANE_LABEL_RE`` exactly: change both together
#: (a test checks that they agree).
LANE_LABEL_CHARS_TEXT: Final = "uppercase letters, digits, space, '+', '-', '/'"
#: A ``layout-invalid`` detail (parser error text, unknown key list) keeps at most this many
#: characters (plus ``...``), so a hostile sidecar cannot flood the report.
MAX_LAYOUT_DETAIL: Final = 200
#: A sidecar integer token (an index) has at most this many digits; a longer one is
#: ``layout-invalid`` before it is ever converted, whatever the interpreter's int-string limit.
MAX_LAYOUT_INT_DIGITS: Final = 6

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
#: Layout sidecar codes (section 2.4): only ``layout-orphan`` is a row of its own.
CODE_LAYOUT_INVALID: Final = "layout-invalid"
CODE_LAYOUT_BOUND: Final = "layout-bound"
CODE_LAYOUT_STALE: Final = "layout-stale"
CODE_LAYOUT_ORPHAN: Final = "layout-orphan"

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
        CODE_LAYOUT_INVALID,
        CODE_LAYOUT_BOUND,
        CODE_LAYOUT_STALE,
        CODE_LAYOUT_ORPHAN,
    ]
)
