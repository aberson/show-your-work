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
import struct
import zlib
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
#: Minimum layout ``alt`` length in characters (a sidecar ``alt`` must not be empty).
MIN_ALT: Final = 1

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
#: A diagram opts into the house layout with ``<id>.layout.json`` directly beside ``<id>.mmd``.
LAYOUT_SUFFIX: Final = ".layout.json"

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

#: The importable package whose data (``init`` templates, bundled fonts) ``importlib.resources``
#: reads.
PACKAGE: Final = "show_your_work"

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
#: The IHDR fields after width / height, in order: bit depth, colour type, compression, filter
#: and interlace method (one byte each, from ``PNG_HEADER_BYTES``).
PNG_IHDR_FIELDS: Final = 5
#: The big-endian length word opening every chunk.
PNG_CHUNK_LENGTH_BYTES: Final = 4
#: Every chunk tag is four ASCII bytes, like ``PNG_IHDR_TAG``.
PNG_CHUNK_TAG_BYTES: Final = len(PNG_IHDR_TAG)
#: The CRC closing every chunk.
PNG_CRC_BYTES: Final = 4
#: Byte offsets inside one chunk: the tag follows the big-endian length, the data follows the tag.
PNG_CHUNK_TAG_OFFSET: Final = PNG_CHUNK_LENGTH_BYTES
#: See ``PNG_CHUNK_TAG_OFFSET``.
PNG_CHUNK_DATA_OFFSET: Final = PNG_CHUNK_TAG_OFFSET + PNG_CHUNK_TAG_BYTES
#: Bytes a chunk adds around its data: the length, the tag and the CRC.
PNG_CHUNK_OVERHEAD: Final = PNG_CHUNK_DATA_OFFSET + PNG_CRC_BYTES
#: The IHDR chunk's data is always 13 bytes: width, height and the ``PNG_IHDR_FIELDS`` bytes.
PNG_IHDR_DATA_BYTES: Final = 13
#: The byte offset just past the IHDR chunk (signature, then the whole first chunk).
PNG_IHDR_END: Final = len(PNG_SIGNATURE) + PNG_CHUNK_OVERHEAD + PNG_IHDR_DATA_BYTES
#: The tag of an image-data chunk; a ``tEXt`` chunk before the first one is header metadata.
PNG_IDAT_TAG: Final = b"IDAT"
#: The only sample depth the house name-ink check decodes.
PNG_BIT_DEPTH: Final = 8
#: Decodable colour type -> channels per pixel: truecolour (RGB) and truecolour with alpha (RGBA).
PNG_COLOUR_CHANNELS: Final[Mapping[int, int]] = MappingProxyType({2: 3, 6: 4})
#: The RGB channels of a pixel, compared with a ``#rrggbb`` colour (alpha is ignored).
PNG_RGB_CHANNELS: Final = 3
#: Every pixel row starts with one filter-type byte (one of the ``PNG_FILTER_*`` types).
PNG_FILTER_BYTES: Final = 1
#: The PNG row filter types, each row's first byte.
PNG_FILTER_NONE: Final = 0
#: See ``PNG_FILTER_NONE``.
PNG_FILTER_SUB: Final = 1
#: See ``PNG_FILTER_NONE``.
PNG_FILTER_UP: Final = 2
#: See ``PNG_FILTER_NONE``.
PNG_FILTER_AVERAGE: Final = 3
#: See ``PNG_FILTER_NONE``.
PNG_FILTER_PAETH: Final = 4
#: Keeps the low 8 bits of an un-filtered sample (filter arithmetic is modulo 256).
PNG_BYTE_MASK: Final = 0xFF
#: The tag of a Latin-1 text chunk (``keyword``, NUL, ``text``).
PNG_TEXT_TAG: Final = b"tEXt"

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
#: ``README_ALT_LABEL`` for a diagram with a layout sidecar: the header Alt describes the mermaid
#: source, while the house drawing above it carries the layout ``alt`` (Phase 2 section 2.5).
README_ALT_SOURCE_LABEL: Final = "**Alt (mermaid source):**"
README_NOT_SHOWN_LABEL: Final = "**Deliberately not shown:**"
README_DRAWN_FROM: Final = "Drawn from:"
README_SOURCE_LABEL: Final = "Source:"
#: The README marker's opening: the HTML comment start that precedes ``GENERATED_MARKER``.
README_MARKER_OPEN: Final = "<!-- "
#: The indent of every README marker continuation line (aligned under ``GENERATED_MARKER``).
README_MARKER_INDENT: Final = " " * len(README_MARKER_OPEN)
#: The README marker's closing sentence plus the HTML comment end, after the provenance sentence.
README_MARKER_CLOSE: Final = "Edit those and re-run; edits here are overwritten. -->"
#: The README marker's continuation lines for a sidecar-less folder (Phase 1 bytes): the
#: provenance sentence, then ``README_MARKER_CLOSE``. Each is written on its own line after
#: ``README_MARKER_INDENT``; the lines hold no newline or indent of their own.
README_MARKER_LINES: Final[tuple[str, ...]] = (
    f"Produced by `{GENERATOR_NAME} render` from {INTRO_NAME} and the {SOURCE_SUFFIX}",
    f"files in this directory. {README_MARKER_CLOSE}",
)
#: ``README_MARKER_LINES`` when the folder holds at least one sidecar.
README_LAYOUT_MARKER_LINES: Final[tuple[str, ...]] = (
    f"Produced by `{GENERATOR_NAME} render` from {INTRO_NAME}, the {SOURCE_SUFFIX} files and "
    f"their {LAYOUT_SUFFIX} sidecars in this directory.",
    README_MARKER_CLOSE,
)
#: The sentence the ``## Regenerating`` footer adds when the folder holds at least one sidecar.
README_LAYOUT_REGEN: Final = (
    f"The house-style SVGs in `{RASTERS_DIR}/` are generated from the `*{LAYOUT_SUFFIX}` "
    "sidecars by the same command."
)
#: The README embed of a sidecar diagram, in place of its bare mermaid fence. ``{dark}`` /
#: ``{light}`` are the house SVG paths, ``{alt}`` the HTML-escaped layout ``alt`` and ``{fence}``
#: the complete mermaid fence; the blank lines inside ``<details>`` let GitHub render the fence.
README_PICTURE_TEMPLATE: Final = (
    "<picture>\n"
    '  <source media="(prefers-color-scheme: dark)" srcset="{dark}">\n'
    '  <img alt="{alt}" src="{light}">\n'
    "</picture>\n"
    "\n"
    "<details><summary>Mermaid source</summary>\n"
    "\n"
    "{fence}"
    "\n"
    "</details>\n"
)
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
#: Exit 2 when the house raster anchors fail: the garbage SVG rasterized, or the known-good
#: layout did not rasterize to its canvas with name ink.
MSG_HOUSE_ANCHOR_BROKEN: Final = (
    "house raster anchor broken: the garbage SVG must raise and the known-good layout must "
    "rasterize with name ink in its bundled font; no house raster can be trusted"
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
#: Exit 2 when a house SVG path in ``rasters/`` holds something ``render`` did not generate (a
#: link, a directory or a file without ``HOUSE_SVG_MARKER`` as its first child); it is never
#: overwritten. ``{generator}`` is ``GENERATOR_NAME``.
MSG_SVG_NOT_GENERATED: Final = (
    "refusing to overwrite {path}: not generated by {generator} (no generator marker)"
)

# ---------------------------------------------------------------------------
# Layout sidecar contract (Phase 2 section 2.4)
# ---------------------------------------------------------------------------

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

#: Bounds (section 2.4, including its text-fit limits). ``alt`` reuses ``MIN_ALT``/``MAX_ALT``.
MIN_LAYOUT_LANES: Final = 2
MAX_LAYOUT_LANES: Final = 6
MIN_LANE_NODES: Final = 1
MAX_LANE_NODES: Final = 4
#: Minimum string lengths in characters (``sub`` may be empty; the other strings may not).
MIN_LANE_LABEL: Final = 1
MAX_LANE_LABEL: Final = 16
MIN_NODE_NAME: Final = 1
MAX_NODE_NAME: Final = 22
MIN_NODE_SUB: Final = 0
MAX_NODE_SUB: Final = 27
MIN_BRANCH_LABEL: Final = 1
MAX_BRANCH_LABEL: Final = 14
MIN_REJOIN_LABEL: Final = 1
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
# House SVG engine (Phase 2 section 2.5). Every number is px; an unpinned value here silently
# changes every committed house SVG.
# ---------------------------------------------------------------------------

#: ``W``: the width of every pill (and so of every lane).
HOUSE_PILL_WIDTH: Final = 200
#: ``NH``: the height of every pill.
HOUSE_PILL_HEIGHT: Final = 46
#: ``G``: the gutter between two lanes; a spine elbow turns at its middle.
HOUSE_GUTTER: Final = 40
#: ``M``: lane 0's x, and the same margin right of the last lane.
HOUSE_MARGIN: Final = 20
#: ``TOP``: the y of node 0 in every lane.
HOUSE_TOP: Final = 50
#: ``ROWH``: node ``k`` sits at ``HOUSE_TOP + k * HOUSE_ROW_PITCH``.
HOUSE_ROW_PITCH: Final = 60
#: ``SIDE_DROP``: a side node sits this much lower than its row.
HOUSE_SIDE_DROP: Final = 22
#: Canvas height below the lowest pill bottom when the layout has no rejoin.
HOUSE_BOTTOM_PAD: Final = 26
#: With a rejoin: its baseline sits this far below the lowest pill bottom ...
HOUSE_REJOIN_DROP: Final = 24
#: ... and the canvas ends this far below that baseline.
HOUSE_REJOIN_PAD: Final = 22
#: The rejoin ends this far below the bottom of its target pill.
HOUSE_REJOIN_END_GAP: Final = 2
#: The rejoin label's baseline sits this far above the rejoin baseline.
HOUSE_REJOIN_LABEL_RISE: Final = 6
#: The card is the canvas inset by this much on every side.
HOUSE_CARD_INSET: Final = 6
#: Corner radius of the card.
HOUSE_CARD_RADIUS: Final = 16
#: Corner radius of a pill.
HOUSE_PILL_RADIUS: Final = 10
#: Baseline y of every lane label (its x is the lane x).
HOUSE_LANE_LABEL_Y: Final = 34
#: Down links (and the rejoin's vertical segments) run at the pill's centre x ...
HOUSE_LINK_DX: Final = HOUSE_PILL_WIDTH // 2
#: ... except in a lane with a loop, where the down links move left to this offset ...
HOUSE_LOOP_LINK_DX: Final = 80
#: ... and the loop's up-arrow runs at this offset.
HOUSE_LOOP_DX: Final = 120
#: A branch label starts this far right of its link and this far below the gap's middle.
HOUSE_BRANCH_LABEL_DX: Final = 8
#: See ``HOUSE_BRANCH_LABEL_DX``.
HOUSE_BRANCH_LABEL_DY: Final = 4
#: Pill text starts this far right of the pill's left edge.
HOUSE_TEXT_DX: Final = 14
#: Baseline of a node name below the pill top (with a sub line below it) ...
HOUSE_NAME_DY: Final = 21
#: ... of the sub line ...
HOUSE_SUB_DY: Final = 38
#: ... and of a name whose sub is empty (vertically centred alone).
HOUSE_NAME_ONLY_DY: Final = 28
#: Stroke widths of the spine, the down links and loop, the pills, the card and the rejoin.
HOUSE_SPINE_STROKE: Final = 2.4
#: See ``HOUSE_SPINE_STROKE``.
HOUSE_LINK_STROKE: Final = 1.7
#: See ``HOUSE_SPINE_STROKE``.
HOUSE_PILL_STROKE: Final = 1.4
#: See ``HOUSE_SPINE_STROKE``.
HOUSE_CARD_STROKE: Final = 1.5
#: See ``HOUSE_SPINE_STROKE``.
HOUSE_REJOIN_STROKE: Final = 1.6
#: The dash pattern of side pills and the rejoin.
HOUSE_DASH: Final = "5 4"
#: The attribute that dashes a side pill and the rejoin path (with its leading space).
HOUSE_DASH_ATTR: Final = f' stroke-dasharray="{HOUSE_DASH}"'
#: Font sizes: lane labels, node names, and every small text (sub, branch and rejoin labels).
HOUSE_LANE_LABEL_SIZE: Final = 11
#: See ``HOUSE_LANE_LABEL_SIZE``.
HOUSE_NAME_SIZE: Final = 13
#: See ``HOUSE_LANE_LABEL_SIZE``.
HOUSE_SMALL_SIZE: Final = 10.5
#: The font weight of lane labels and node names.
HOUSE_BOLD: Final = 600
#: The attribute that sets ``HOUSE_BOLD`` on a lane label or node name (with its leading space).
HOUSE_BOLD_ATTR: Final = f' font-weight="{HOUSE_BOLD}"'
#: The attribute that leaves a path unfilled (with its leading space).
HOUSE_NO_FILL_ATTR: Final = ' fill="none"'
#: The attribute that centres the rejoin label on its x (with its leading space).
HOUSE_TEXT_MIDDLE_ATTR: Final = ' text-anchor="middle"'
#: The letter spacing of lane labels.
HOUSE_LANE_LABEL_SPACING: Final = ".12em"
#: The family name the bundled PNG fonts declare (``HOUSE_FONT_SHA256``); a house PNG maps
#: ``monospace`` to it.
HOUSE_RASTER_FONT_FAMILY: Final = "DejaVu Sans Mono"
#: The root ``font-family``: browser monospace fallbacks, then the bundled PNG font.
HOUSE_FONT_FAMILY: Final = (
    f"ui-monospace,'Cascadia Code','SF Mono',Consolas,'{HOUSE_RASTER_FONT_FAMILY}',monospace"
)
#: The SVG namespace of the root element.
SVG_NAMESPACE: Final = "http://www.w3.org/2000/svg"
#: How every house SVG begins: the root ``<svg`` start tag up to its first attribute value.
HOUSE_SVG_ROOT_START: Final = f'<svg xmlns="{SVG_NAMESPACE}" '
#: The first child of every house SVG, on the line after the root start tag; only a file holding
#: it exactly there is ever overwritten or deleted as an orphan. It names no id: ids may contain
#: ``--``, which XML forbids inside a comment.
HOUSE_SVG_MARKER: Final = f"<!-- generated by {GENERATOR_NAME} render; do not edit -->"
#: Element ids of the link arrowhead, the spine arrowhead and the pill drop shadow.
HOUSE_LINK_ARROW_ID: Final = "ar"
#: See ``HOUSE_LINK_ARROW_ID``.
HOUSE_SPINE_ARROW_ID: Final = "ars"
#: See ``HOUSE_LINK_ARROW_ID``.
HOUSE_SHADOW_ID: Final = "sh"
#: The fixed ``<defs>`` block (as in the reference SVGs); ``{link}``, ``{spine}`` and ``{shadow}``
#: are the theme's palette colours.
HOUSE_SVG_DEFS_TEMPLATE: Final = (
    "<defs>\n"
    f'  <marker id="{HOUSE_LINK_ARROW_ID}" markerWidth="9" markerHeight="9" refX="7.5" '
    'refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 Z" fill="{link}"/></marker>\n'
    f'  <marker id="{HOUSE_SPINE_ARROW_ID}" markerWidth="10" markerHeight="10" refX="7.5" '
    'refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 Z" fill="{spine}"/></marker>\n'
    f'  <filter id="{HOUSE_SHADOW_ID}" x="-20%" y="-45%" width="140%" height="190%">'
    '<feDropShadow dx="0" dy="2" stdDeviation="4" flood-color="{shadow}"/></filter>\n'
    "</defs>\n"
)
#: The last line of every house SVG: the root end tag.
HOUSE_SVG_END: Final = "</svg>"

#: Palette token names (section 2.5 table): card fill (also the pill fill), card stroke, link
#: (links, pill stroke, rejoin, link arrow), spine (spine, spine arrow, node name), muted (sub,
#: lane label, side name, branch label, rejoin label) and the shadow flood colour.
PALETTE_CARD_FILL: Final = "card_fill"
#: See ``PALETTE_CARD_FILL``.
PALETTE_CARD_STROKE: Final = "card_stroke"
#: See ``PALETTE_CARD_FILL``.
PALETTE_LINK: Final = "link"
#: See ``PALETTE_CARD_FILL``.
PALETTE_SPINE: Final = "spine"
#: See ``PALETTE_CARD_FILL``.
PALETTE_MUTED: Final = "muted"
#: See ``PALETTE_CARD_FILL``.
PALETTE_SHADOW: Final = "shadow"
#: Theme (``RASTER_THEMES``) -> token -> concrete colour. Each theme is a separate file with
#: concrete colours (no CSS variables), so no colour of one theme appears in the other.
HOUSE_PALETTES: Final[Mapping[str, Mapping[str, str]]] = MappingProxyType(
    {
        RASTER_LIGHT: MappingProxyType(
            {
                PALETTE_CARD_FILL: "#FFFFFF",
                PALETTE_CARD_STROKE: "#D4DBE8",
                PALETTE_LINK: "#C0C9DA",
                PALETTE_SPINE: "#3355E0",
                PALETTE_MUTED: "#6E778C",
                PALETTE_SHADOW: "rgba(20,26,40,.16)",
            }
        ),
        RASTER_DARK: MappingProxyType(
            {
                PALETTE_CARD_FILL: "#161C26",
                PALETTE_CARD_STROKE: "#29313F",
                PALETTE_LINK: "#384153",
                PALETTE_SPINE: "#6E9BFF",
                PALETTE_MUTED: "#9AA5B8",
                PALETTE_SHADOW: "rgba(0,0,0,.5)",
            }
        ),
    }
)

#: Every house SVG ends with this suffix (``rasters/<id>-<theme>.svg``, beside the PNGs).
HOUSE_SVG_SUFFIX: Final = ".svg"
#: A house SVG's file name inside ``RASTERS_DIR``.
HOUSE_SVG_FILE: Final = "{id}-{theme}.svg"
#: The theme endings (``-light.svg``, ``-dark.svg``) that make a ``RASTERS_DIR`` file a house SVG
#: orphan candidate (it is deleted only when it also has ``HOUSE_SVG_MARKER`` as its first child).
HOUSE_SVG_SUFFIXES: Final[tuple[str, ...]] = tuple(
    f"-{theme}{HOUSE_SVG_SUFFIX}" for theme in RASTER_THEMES
)

# ---------------------------------------------------------------------------
# House PNG rasters for ``render --png`` (Phase 2 section 2.6 Step 10; decisions in section
# 12.D). An unpinned value here silently changes every house raster hash.
# ---------------------------------------------------------------------------

#: Importable name of the SVG rasterizer of house PNGs (extra ``render``; the pin lives in
#: pyproject).
HOUSE_RASTERIZER_MODULE: Final = "resvg_py"
#: Package-relative directory of the bundled fonts and their licence (``PACKAGE`` data).
HOUSE_FONT_PACKAGE_PARTS: Final[tuple[str, ...]] = tuple(["fonts"])
#: Bundled font file -> its pinned SHA-256: regular, then bold (``HOUSE_BOLD`` text). These fonts
#: and no others draw a house PNG (system fonts are skipped).
HOUSE_FONT_SHA256: Final[Mapping[str, str]] = MappingProxyType(
    {
        "DejaVuSansMono.ttf": "602ec86b8948cfcd956482fe64f94c36c867770149ef2f791d4613f443bcecb3",
        "DejaVuSansMono-Bold.ttf": (
            "baada9a5172fe20886251aff0433fc38461912d5daf07287e7bee56620a8da96"
        ),
    }
)
#: The DejaVu licence shipped beside the fonts (``HOUSE_FONT_PACKAGE_PARTS``).
HOUSE_FONT_LICENSE: Final = "LICENSE-DejaVu.txt"
#: The pinned SHA-256 of ``HOUSE_FONT_LICENSE``.
HOUSE_FONT_LICENSE_SHA256: Final = (
    "d75938dec098f06f0ac3c00853065d94f020be1c3c62ef1dc2975ba15b4d9b0e"
)
#: Raster theme -> the OPAQUE background of a house PNG: the theme's card fill (Phase 2 section
#: 2.5).
HOUSE_RASTER_BACKGROUNDS: Final[Mapping[str, str]] = MappingProxyType(
    {theme: palette[PALETTE_CARD_FILL] for theme, palette in HOUSE_PALETTES.items()}
)
#: House garbage anchor: rasterizing this malformed SVG MUST raise.
HOUSE_GARBAGE_SVG: Final = "<svg xmlns='http://www.w3.org/2000/svg'><g></svg>"
#: House known-good anchor: the two-lane layout of the Step 10 fixture
#: ``tests/fixtures/layout/png/house.layout.json`` (lane 0 ``out`` and lane 1 ``in`` are both node
#: 0). Its house SVG MUST rasterize to ``RASTER_SCALE`` x its viewBox with name ink in
#: ``HOUSE_INK_BOX`` for every theme, so a rasterizer that draws no text cannot pass.
HOUSE_KNOWN_GOOD: Final = (
    '{"schema":"show-your-work.layout.v1",'
    '"source_sha256":"64d5f3fe5d2d984cde17e1fce590e41f44f5d615eca112771f4b800a66bdd06f",'
    '"alt":"Two columns joined by a straight spine: draft and review, then build and ship.",'
    '"lanes":[{"label":"PLAN","in":0,"out":0,"nodes":['
    '{"name":"Draft plan","sub":"one page","side":false,"branch_label":""},'
    '{"name":"Review plan","sub":"two readers","side":false,"branch_label":""}]},'
    '{"label":"BUILD","in":0,"out":1,"nodes":['
    '{"name":"Build step","sub":"tests first","side":false,"branch_label":""},'
    '{"name":"Ship release","sub":"","side":false,"branch_label":""}]}],'
    '"loops":[],"rejoin":null}'
)
#: The name ink box reaches this far below the name baseline (DejaVu descenders).
HOUSE_INK_DESCENT: Final = 5
#: The name ink box of lane 0, node 0, at scale 1 as ``(left, top, right, bottom)`` (right and
#: bottom exclusive): from the name's x to the pill's right edge, and from one name size above
#: the name baseline to ``HOUSE_INK_DESCENT`` below it. Node 0 is never a side node.
HOUSE_INK_BOX: Final[tuple[int, ...]] = tuple(
    [
        HOUSE_MARGIN + HOUSE_TEXT_DX,
        HOUSE_TOP + HOUSE_NAME_DY - HOUSE_NAME_SIZE,
        HOUSE_MARGIN + HOUSE_PILL_WIDTH,
        HOUSE_TOP + HOUSE_NAME_DY + HOUSE_INK_DESCENT,
    ]
)
#: At least this many ``HOUSE_INK_BOX`` pixels must be the name colour (a raster drawn with
#: no usable font has no text at all).
HOUSE_INK_MIN_PIXELS: Final = 20
#: A pixel is the name colour when every channel is within this much of it.
HOUSE_INK_TOLERANCE: Final = 8
#: The ``tEXt`` keyword and text that tag a house PNG.
HOUSE_PNG_KEYWORD: Final = GENERATOR_NAME
#: See ``HOUSE_PNG_KEYWORD``.
HOUSE_PNG_TEXT: Final = "house-v1"
#: The data of the tagging ``tEXt`` chunk: keyword, NUL separator, text.
HOUSE_PNG_TEXT_DATA: Final = f"{HOUSE_PNG_KEYWORD}\0{HOUSE_PNG_TEXT}".encode("latin-1")
#: The complete tagging chunk (length, ``tEXt``, data, CRC), inserted directly after IHDR. A PNG
#: is a house PNG only when this exact chunk appears before its first ``IDAT``.
HOUSE_PNG_CHUNK: Final = (
    struct.pack(">I", len(HOUSE_PNG_TEXT_DATA))
    + PNG_TEXT_TAG
    + HOUSE_PNG_TEXT_DATA
    + struct.pack(">I", zlib.crc32(PNG_TEXT_TAG + HOUSE_PNG_TEXT_DATA))
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
