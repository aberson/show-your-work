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
"""

from __future__ import annotations

import importlib
import math
import struct
from types import ModuleType
from typing import Any, Protocol

from show_your_work.constants import (
    FRONT_MATTER_TEMPLATE,
    GARBAGE,
    KNOWN_GOOD,
    MAX_RASTER_PIXELS,
    MAX_RASTER_SIDE,
    MSG_RASTER_TOO_LARGE,
    MSG_SVG_NO_SIZE,
    PNG_HEADER_BYTES,
    PNG_IHDR_TAG,
    PNG_IHDR_TAG_OFFSET,
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

__all__ = [
    "MermaidxRenderer",
    "RasterRenderer",
    "RasterTooLargeError",
    "Renderer",
    "RendererLoadError",
    "anchor_ok",
    "check_canvas",
    "inject_config",
    "load",
    "png",
    "png_size",
    "raster_anchor_ok",
    "raster_canvas",
    "svg_counts",
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
