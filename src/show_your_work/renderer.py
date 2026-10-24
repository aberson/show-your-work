"""Thin optional wrapper over the pinned ``mermaidx`` renderer (plan sections 2, 5 and DD5).

``mermaidx`` lives in the optional ``render`` extra, so it is imported lazily: :func:`load`
returns ``None`` when it is not importable and the stdlib verbs never touch it. Every render path
must call :func:`anchor_ok` first: rendering the known-garbage fixture MUST raise (a renderer that
accepts garbage cannot be trusted to count anything -- the prototype spike's harness bug), and a
known-good diagram MUST render with at least one node (a renderer that raises on everything would
otherwise pass the garbage anchor and turn every file into a ``render-error``).

:func:`svg_counts` reads exact node / edge counts from the SVG mermaid produced, with the regexes
carried from the prototype ``counts_from_svg`` (defined once in :mod:`show_your_work.constants`).
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Protocol

from show_your_work.constants import (
    GARBAGE,
    KNOWN_GOOD,
    RENDERER_MODULE,
    SVG_EDGE_FALLBACK_RE,
    SVG_EDGE_RE,
    SVG_NODE_RE,
)

__all__ = [
    "MermaidxRenderer",
    "Renderer",
    "RendererLoadError",
    "anchor_ok",
    "load",
    "svg_counts",
]


class Renderer(Protocol):
    """Anything that turns mermaid source into an SVG string, raising on invalid source."""

    def svg(self, source: str) -> str: ...


class RendererLoadError(Exception):
    """The renderer is installed but failed to import or initialise (not merely absent)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MermaidxRenderer:
    """:class:`Renderer` backed by ``mermaidx.render(source).svg()``."""

    def __init__(self, module: ModuleType) -> None:
        self._module = module

    def svg(self, source: str) -> str:
        # mermaidx renders lazily: the parse error surfaces from .svg(), not from render().
        result = self._module.render(source).svg()
        if not isinstance(result, str):
            raise TypeError(f"mermaidx returned {type(result).__name__}, expected str")
        return result


def load() -> Renderer | None:
    """Return a :class:`Renderer` over ``mermaidx``, or ``None`` when it is not installed.

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
