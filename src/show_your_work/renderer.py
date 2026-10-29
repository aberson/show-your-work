"""Thin optional wrapper over the pinned ``mermaidx`` renderer (plan sections 2, 5 and DD5).

``mermaidx`` lives in the optional ``render`` extra, so it is imported lazily: :func:`load`
returns ``None`` when it is not importable and the stdlib verbs never touch it. Every render path
must call :func:`anchor_ok` first: rendering the known-garbage fixture MUST raise (a renderer that
accepts garbage cannot be trusted to count anything -- the prototype spike's harness bug), and a
known-good diagram MUST render with at least one node (a renderer that raises on everything would
otherwise pass the garbage anchor and turn every file into a ``render-error``).

:func:`svg_counts` reads exact node / edge counts from the SVG mermaid produced, with the regexes
carried from the prototype ``counts_from_svg`` (defined once in :mod:`show_your_work.constants`).

Rasters (``render --png``, plan section 4): :func:`png` prepends the mermaid front-matter config
block (:func:`inject_config`: the mermaid ``theme`` and ``flowchart.wrappingWidth``) to a source
and calls the renderer's ``png`` with the frozen ``RASTER_SCALE`` and the opaque per-theme
background. :func:`raster_anchor_ok` repeats the garbage / known-good anchors through that raster
path, and :func:`png_size` reads a PNG's IHDR width / height (stdlib only). Before any
``png()`` call the canvas size is derived from the same renderer's SVG (:func:`raster_canvas`) and
checked against ``MAX_RASTER_SIDE`` / ``MAX_RASTER_PIXELS`` (:func:`check_canvas`).

House rasters (plan sections 2.6 Step 10 and 12.D): :func:`house_png` rasterizes a house SVG
through ``resvg_py`` (``HOUSE_RASTERIZER_MODULE``) at the same scale, on the theme's card fill,
with only the bundled DejaVu Sans Mono fonts (:func:`house_fonts`, read through
:mod:`importlib.resources`), and tags the result with the ``HOUSE_PNG_CHUNK`` ``tEXt`` chunk
directly after IHDR (:func:`tag_png`; :func:`is_house_png` reads the tag back).
:func:`house_anchor_ok` runs the house anchors: the garbage SVG must raise in the rasterizer
itself, and the known-good layout must rasterize to its canvas with name ink (:func:`name_ink`),
so a rasterizer that silently draws no text (a missing font) cannot pass.
"""

from __future__ import annotations

import contextlib
import importlib
import math
import struct
import zlib
from collections.abc import Iterator
from importlib import resources
from importlib.resources.abc import Traversable
from types import ModuleType
from typing import Any, Protocol

from show_your_work.constants import (
    FRONT_MATTER_TEMPLATE,
    GARBAGE,
    HOUSE_FONT_PACKAGE_PARTS,
    HOUSE_FONT_SHA256,
    HOUSE_GARBAGE_SVG,
    HOUSE_INK_BOX,
    HOUSE_INK_MIN_PIXELS,
    HOUSE_INK_TOLERANCE,
    HOUSE_KNOWN_GOOD,
    HOUSE_PALETTES,
    HOUSE_PNG_CHUNK,
    HOUSE_RASTER_BACKGROUNDS,
    HOUSE_RASTER_FONT_FAMILY,
    HOUSE_RASTERIZER_MODULE,
    KNOWN_GOOD,
    MAX_RASTER_PIXELS,
    MAX_RASTER_SIDE,
    MSG_RASTER_TOO_LARGE,
    MSG_SVG_NO_SIZE,
    PACKAGE,
    PALETTE_SPINE,
    PNG_BIT_DEPTH,
    PNG_BYTE_MASK,
    PNG_CHUNK_DATA_OFFSET,
    PNG_CHUNK_LENGTH_BYTES,
    PNG_CHUNK_OVERHEAD,
    PNG_CHUNK_TAG_OFFSET,
    PNG_COLOUR_CHANNELS,
    PNG_CRC_BYTES,
    PNG_FILTER_AVERAGE,
    PNG_FILTER_BYTES,
    PNG_FILTER_NONE,
    PNG_FILTER_PAETH,
    PNG_FILTER_SUB,
    PNG_FILTER_UP,
    PNG_HEADER_BYTES,
    PNG_IDAT_TAG,
    PNG_IHDR_END,
    PNG_IHDR_FIELDS,
    PNG_IHDR_TAG,
    PNG_IHDR_TAG_OFFSET,
    PNG_RGB_CHANNELS,
    PNG_SIGNATURE,
    PNG_WIDTH_OFFSET,
    RASTER_BACKGROUNDS,
    RASTER_MERMAID_THEMES,
    RASTER_SCALE,
    RASTER_THEMES,
    RASTER_WRAPPING_WIDTH,
    RENDERER_MODULE,
    SVG_EDGE_FALLBACK_RE,
    SVG_EDGE_RE,
    SVG_HEIGHT_ATTR_RE,
    SVG_NODE_RE,
    SVG_VIEWBOX_FIELDS,
    SVG_VIEWBOX_RE,
    SVG_WIDTH_ATTR_RE,
)
from show_your_work.layout import draw_svg, parse_layout

__all__ = [
    "MermaidxRenderer",
    "RasterRenderer",
    "RasterTooLargeError",
    "Renderer",
    "RendererLoadError",
    "anchor_ok",
    "check_canvas",
    "house_anchor_ok",
    "house_fonts",
    "house_png",
    "inject_config",
    "is_house_png",
    "load",
    "name_ink",
    "png",
    "png_size",
    "raster_anchor_ok",
    "raster_canvas",
    "svg_counts",
    "tag_png",
]


class Renderer(Protocol):
    """Anything that turns mermaid source into an SVG string, raising on invalid source."""

    def svg(self, source: str) -> str: ...


class RasterRenderer(Renderer, Protocol):
    """A :class:`Renderer` that also rasterizes mermaid source to PNG bytes."""

    def png(self, source: str, *, scale: float, background: str) -> bytes: ...


class RendererLoadError(Exception):
    """The renderer is installed but failed to import or initialise (not merely absent)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MermaidxRenderer:
    """:class:`RasterRenderer` backed by ``mermaidx.render(source).svg()`` / ``.png()``.

    The most recent ``mermaidx`` diagram object is kept (one entry), so the raster path's
    ``svg(src)`` size probe followed by ``png(src)`` lays the diagram out once, not twice
    (mermaidx caches the SVG on the diagram object and rasterizes from it).
    """

    def __init__(self, module: ModuleType) -> None:
        self._module = module
        self._last: tuple[str, Any] | None = None

    def _diagram(self, source: str) -> Any:
        if self._last is not None and self._last[0] == source:
            return self._last[1]
        diagram = self._module.render(source)
        self._last = (source, diagram)
        return diagram

    def svg(self, source: str) -> str:
        # mermaidx renders lazily: the parse error surfaces from .svg(), not from render().
        result = self._diagram(source).svg()
        if not isinstance(result, str):
            raise TypeError(f"mermaidx returned {type(result).__name__}, expected str")
        return result

    def png(self, source: str, *, scale: float, background: str) -> bytes:
        result = self._diagram(source).png(scale=scale, background=background)
        if not isinstance(result, bytes):
            raise TypeError(f"mermaidx returned {type(result).__name__}, expected bytes")
        return result


def load() -> RasterRenderer | None:
    """Return a :class:`RasterRenderer` over ``mermaidx``, or ``None`` when it is not installed.

    Raises :class:`RendererLoadError` when the module exists but importing it fails for any other
    reason (a broken native dependency), so that case is not misreported as "not installed".
    """
    try:
        module = importlib.import_module(RENDERER_MODULE)
    except ImportError:
        return None
    except Exception as exc:
        raise RendererLoadError(f"{type(exc).__name__}: {exc}") from exc
    return MermaidxRenderer(module)


def anchor_ok(renderer: Renderer) -> bool:
    """``True`` only when garbage raises AND the known-good diagram renders with >= 1 node."""
    try:
        renderer.svg(GARBAGE)
    except Exception:
        pass
    else:
        return False
    try:
        nodes, _ = svg_counts(renderer.svg(KNOWN_GOOD))
    except Exception:
        return False
    return nodes > 0


def svg_counts(svg: str) -> tuple[int, int]:
    """Return ``(nodes, edges)`` counted from a rendered mermaid SVG."""
    nodes = len(SVG_NODE_RE.findall(svg))
    edges = len(SVG_EDGE_RE.findall(svg))
    if edges == 0:
        edges = len(SVG_EDGE_FALLBACK_RE.findall(svg))
    return nodes, edges


# --- rasters (section 4 "PNG rendering") -----------------------------------------------------


def inject_config(source: str, theme: str) -> str:
    """``source`` with the raster front-matter config block for ``theme`` prepended.

    ``theme`` is a raster theme (``light`` / ``dark``); a ``KeyError`` for anything else. The
    block must be the very first thing in the text (mermaid only reads front matter there); a
    checked source always starts with its ``%%`` header, so it never carries a block of its own.
    """
    block = FRONT_MATTER_TEMPLATE.format(
        theme=RASTER_MERMAID_THEMES[theme], wrapping_width=RASTER_WRAPPING_WIDTH
    )
    return block + source


class RasterTooLargeError(ValueError):
    """The raster canvas would exceed ``MAX_RASTER_SIDE`` / ``MAX_RASTER_PIXELS``."""


def _svg_root_tag(svg: str) -> str:
    """The opening ``<svg ...>`` tag (plain ``find``: no regex scan over the whole document)."""
    start = svg.find("<svg")
    end = svg.find(">", start) if start >= 0 else -1
    if end < 0:
        raise ValueError(MSG_SVG_NO_SIZE)
    return svg[start : end + 1]


def _dimension(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(MSG_SVG_NO_SIZE)
    return number


def raster_canvas(svg: str) -> tuple[int, int]:
    """The ``(width, height)`` in pixels a raster of ``svg`` at ``RASTER_SCALE`` will have.

    resvg (the rasterizer mermaidx uses) rounds the SVG's intrinsic size half-up to whole pixels
    and then applies the zoom. The intrinsic size is taken from the root ``viewBox``; an absolute
    numeric ``width`` / ``height`` attribute, when present and larger, wins (a conservative bound).
    ``ValueError`` when there is no usable ``viewBox``.
    """
    root = _svg_root_tag(svg)
    match = SVG_VIEWBOX_RE.search(root)
    if match is None:
        raise ValueError(MSG_SVG_NO_SIZE)
    parts = match.group(1).replace(",", " ").split()
    if len(parts) != SVG_VIEWBOX_FIELDS:
        raise ValueError(MSG_SVG_NO_SIZE)
    sizes = [_dimension(parts[2]), _dimension(parts[3])]
    for axis, pattern in enumerate((SVG_WIDTH_ATTR_RE, SVG_HEIGHT_ATTR_RE)):
        attr = pattern.search(root)
        if attr is not None:
            sizes[axis] = max(sizes[axis], _dimension(attr.group(1)))
    width, height = (RASTER_SCALE * math.floor(size + 0.5) for size in sizes)
    return width, height


def check_canvas(width: int, height: int) -> None:
    """Raise :class:`RasterTooLargeError` when a canvas exceeds the raster caps."""
    if width > MAX_RASTER_SIDE or height > MAX_RASTER_SIDE or width * height > MAX_RASTER_PIXELS:
        raise RasterTooLargeError(MSG_RASTER_TOO_LARGE.format(width=width, height=height))


def png(renderer: RasterRenderer, source: str, theme: str) -> bytes:
    """Rasterize ``source`` for ``theme`` with the frozen scale and the opaque theme background.

    The canvas size is computed from the same renderer's SVG and checked against the caps
    BEFORE ``png()`` runs: an oversized canvas (e.g. a ``%%{init}%%`` directive inflating fonts
    and spacing) would otherwise make the native rasterizer abort the whole process.
    """
    injected = inject_config(source, theme)
    check_canvas(*raster_canvas(renderer.svg(injected)))
    return renderer.png(injected, scale=RASTER_SCALE, background=RASTER_BACKGROUNDS[theme])


def png_size(data: bytes) -> tuple[int, int]:
    """``(width, height)`` from a PNG's IHDR chunk; ``ValueError`` when ``data`` is not a PNG."""
    if (
        len(data) < PNG_HEADER_BYTES
        or not data.startswith(PNG_SIGNATURE)
        or data[PNG_IHDR_TAG_OFFSET:PNG_WIDTH_OFFSET] != PNG_IHDR_TAG
    ):
        raise ValueError("not a PNG (bad signature or missing IHDR)")
    width, height = struct.unpack(">II", data[PNG_WIDTH_OFFSET:PNG_HEADER_BYTES])
    if width == 0 or height == 0:
        raise ValueError(f"PNG has an empty canvas ({width}x{height})")
    return int(width), int(height)


def raster_anchor_ok(renderer: RasterRenderer) -> bool:
    """The anchors again, through the raster path, for every theme: garbage must raise and the
    known-good diagram must rasterize to a non-empty PNG."""
    for theme in RASTER_THEMES:
        try:
            png(renderer, GARBAGE, theme)
        except Exception:
            pass
        else:
            return False
        try:
            png_size(png(renderer, KNOWN_GOOD, theme))
        except Exception:
            return False
    return True


# --- house rasters (plan sections 2.6 Step 10 and 12.D) -------------------------------------


def house_fonts() -> tuple[Traversable, ...]:
    """The bundled font files in ``HOUSE_FONT_SHA256`` order, via :mod:`importlib.resources`."""
    fonts = resources.files(PACKAGE).joinpath(*HOUSE_FONT_PACKAGE_PARTS)
    return tuple(fonts.joinpath(name) for name in HOUSE_FONT_SHA256)


def _rasterize_svg(svg: str, theme: str) -> bytes:
    """``svg`` through resvg at ``RASTER_SCALE`` on the theme's house background (plan section 2.5).

    Only the bundled fonts draw text; system fonts are skipped. No canvas check, no tag.
    """
    module = importlib.import_module(HOUSE_RASTERIZER_MODULE)
    with contextlib.ExitStack() as stack:
        font_files = [str(stack.enter_context(resources.as_file(font))) for font in house_fonts()]
        result = module.svg_to_bytes(
            svg_string=svg,
            zoom=RASTER_SCALE,
            background=HOUSE_RASTER_BACKGROUNDS[theme],
            skip_system_fonts=True,
            font_files=font_files,
            monospace_family=HOUSE_RASTER_FONT_FAMILY,
        )
    if not isinstance(result, bytes):
        raise TypeError(
            f"{HOUSE_RASTERIZER_MODULE} returned {type(result).__name__}, expected bytes"
        )
    return result


def tag_png(data: bytes) -> bytes:
    """``data`` with ``HOUSE_PNG_CHUNK`` inserted directly after its IHDR chunk.

    ``ValueError`` when ``data`` is not a PNG.
    """
    png_size(data)
    return data[:PNG_IHDR_END] + HOUSE_PNG_CHUNK + data[PNG_IHDR_END:]


def house_png(svg: str, theme: str) -> bytes:
    """The house SVG ``svg`` rasterized for ``theme`` and tagged by :func:`tag_png`.

    As in :func:`png`, the canvas is checked against the raster caps BEFORE the native rasterizer
    runs.
    """
    check_canvas(*raster_canvas(svg))
    return tag_png(_rasterize_svg(svg, theme))


def _chunks(data: bytes) -> Iterator[tuple[bytes, bytes]]:
    """``(tag, chunk)`` for each complete chunk after the signature; a truncated one ends the walk.

    ``chunk`` is the whole chunk: length, tag, data and CRC.
    """
    position = len(PNG_SIGNATURE)
    while position + PNG_CHUNK_OVERHEAD <= len(data):
        (length,) = struct.unpack(">I", data[position : position + PNG_CHUNK_LENGTH_BYTES])
        end = position + PNG_CHUNK_OVERHEAD + length
        if end > len(data):
            return
        tag = data[position + PNG_CHUNK_TAG_OFFSET : position + PNG_CHUNK_DATA_OFFSET]
        yield tag, data[position:end]
        position = end


def is_house_png(data: bytes) -> bool:
    """``True`` only when ``HOUSE_PNG_CHUNK`` appears, byte for byte, before the first ``IDAT``.

    Anything unreadable (no PNG signature, no complete ``IDAT`` chunk) counts as untagged.
    """
    if not data.startswith(PNG_SIGNATURE):
        return False
    tagged = False
    for tag, chunk in _chunks(data):
        if tag == PNG_IDAT_TAG:
            return tagged
        tagged = tagged or chunk == HOUSE_PNG_CHUNK
    return False


def _paeth(left: int, up: int, up_left: int) -> int:
    """The PNG Paeth predictor."""
    estimate = left + up - up_left
    to_left, to_up, to_up_left = abs(estimate - left), abs(estimate - up), abs(estimate - up_left)
    if to_left <= to_up and to_left <= to_up_left:
        return left
    return up if to_up <= to_up_left else up_left


def _unfilter(line: bytearray, prior: bytes, kind: int, step: int) -> None:
    """Undo one row's filter ``kind`` in place; ``step`` is the bytes per pixel."""
    if kind == PNG_FILTER_NONE:
        return
    for index, value in enumerate(line):
        left = line[index - step] if index >= step else 0
        if kind == PNG_FILTER_SUB:
            predicted = left
        elif kind == PNG_FILTER_UP:
            predicted = prior[index]
        elif kind == PNG_FILTER_AVERAGE:
            predicted = (left + prior[index]) // 2
        elif kind == PNG_FILTER_PAETH:
            up_left = prior[index - step] if index >= step else 0
            predicted = _paeth(left, prior[index], up_left)
        else:
            raise ValueError(f"PNG row filter {kind} is not defined")
        line[index] = (value + predicted) & PNG_BYTE_MASK


def _pixel_rows(data: bytes, count: int) -> tuple[int, list[bytes]]:
    """``(channels, rows)``: the first ``count`` pixel rows of ``data``, un-filtered.

    Only what a house PNG needs is decoded (8-bit RGB or RGBA, not interlaced); anything else, or
    image data too short for ``count`` rows, raises ``ValueError``. Only the needed rows are
    decompressed.
    """
    width, height = png_size(data)
    depth, colour, _, _, interlace = data[PNG_HEADER_BYTES : PNG_HEADER_BYTES + PNG_IHDR_FIELDS]
    channels = PNG_COLOUR_CHANNELS.get(colour)
    if depth != PNG_BIT_DEPTH or channels is None or interlace:
        raise ValueError(f"cannot decode PNG (bit depth {depth}, colour type {colour})")
    wanted = min(count, height)
    stride = width * channels
    compressed = b"".join(
        chunk[PNG_CHUNK_DATA_OFFSET:-PNG_CRC_BYTES]
        for tag, chunk in _chunks(data)
        if tag == PNG_IDAT_TAG
    )
    row_bytes = PNG_FILTER_BYTES + stride
    raw = zlib.decompressobj().decompress(compressed, wanted * row_bytes)
    if len(raw) < wanted * row_bytes:
        raise ValueError("PNG image data is truncated")
    rows: list[bytes] = []
    prior = bytes(stride)
    for row_index in range(wanted):
        start = row_index * row_bytes
        line = bytearray(raw[start + PNG_FILTER_BYTES : start + row_bytes])
        _unfilter(line, prior, raw[start], channels)
        prior = bytes(line)
        rows.append(prior)
    return channels, rows


def name_ink(data: bytes, colour: str) -> int:
    """How many pixels of ``HOUSE_INK_BOX`` (at ``RASTER_SCALE``) are ``colour`` (``#rrggbb``).

    A pixel counts when each RGB channel is within ``HOUSE_INK_TOLERANCE`` of ``colour``;
    ``ValueError`` when ``data`` cannot be decoded or is narrower than the box.
    """
    left, top, right, bottom = (RASTER_SCALE * edge for edge in HOUSE_INK_BOX)
    channels, rows = _pixel_rows(data, bottom)
    ink_rgb = bytes.fromhex(colour.removeprefix("#"))
    count = 0
    for row in rows[top:bottom]:
        for x in range(left, right):
            pixel = row[x * channels : x * channels + PNG_RGB_CHANNELS]
            pairs = zip(pixel, ink_rgb, strict=True)
            count += all(abs(have - want) <= HOUSE_INK_TOLERANCE for have, want in pairs)
    return count


def house_anchor_ok() -> bool:
    """Check the house anchors for every theme.

    ``HOUSE_GARBAGE_SVG`` must raise in the rasterizer itself, and ``HOUSE_KNOWN_GOOD`` must
    rasterize to its canvas with at least ``HOUSE_INK_MIN_PIXELS`` of name ink.
    """
    known_good = parse_layout(HOUSE_KNOWN_GOOD.encode("ascii"))
    for theme in RASTER_THEMES:
        try:
            _rasterize_svg(HOUSE_GARBAGE_SVG, theme)
        except Exception:
            pass
        else:
            return False
        svg = draw_svg(known_good, theme)
        try:
            data = house_png(svg, theme)
            if png_size(data) != raster_canvas(svg):
                return False
            ink_count = name_ink(data, HOUSE_PALETTES[theme][PALETTE_SPINE])
        except Exception:
            return False
        if ink_count < HOUSE_INK_MIN_PIXELS:
            return False
    return True
