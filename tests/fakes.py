"""A stand-in renderer for tests: mermaidx is an optional extra the default gate never installs."""

from __future__ import annotations

from collections.abc import Mapping

from show_your_work.constants import COMMENT_PREFIX, GARBAGE

#: Counts returned for a diagram whose title is not in the fake's table.
DEFAULT_COUNTS = (2, 1)

#: Render counts for the over-budget fixtures, keyed by title: the real mermaidx 0.9.5 counts,
#: recorded at build time (and re-checked by the real-renderer calibration test when installed).
OVER_COUNTS: dict[str, tuple[int, int]] = {
    "Fifteen Nodes": (15, 14),
    "Twenty One Edges": (7, 21),
}


def svg_with(nodes: int, edges: int) -> str:
    """A minimal SVG that the section 3.3 count regexes read as ``nodes`` / ``edges``."""
    return (
        "<svg>"
        + '<g class="node default" id="n"></g>' * nodes
        + '<path class="edge-thickness-normal edge-pattern-solid flowchart-link"/>' * edges
        + "</svg>"
    )


def title_of(source: str) -> str:
    first = source.replace("\r\n", "\n").split("\n", 1)[0]
    return first.removeprefix(COMMENT_PREFIX).strip()


class FakeRenderer:
    """Records every call; raises on the garbage anchor unless ``accept_garbage``.

    ``counts`` maps a diagram title to the ``(nodes, edges)`` its SVG carries; ``fail_titles``
    lists titles whose render raises (a per-file ``render-error``).
    """

    def __init__(
        self,
        counts: Mapping[str, tuple[int, int]] | None = None,
        *,
        accept_garbage: bool = False,
        fail_titles: frozenset[str] = frozenset(),
    ) -> None:
        self.counts = dict(counts or {})
        self.accept_garbage = accept_garbage
        self.fail_titles = fail_titles
        self.calls: list[str] = []

    def svg(self, source: str) -> str:
        self.calls.append(source)
        if source == GARBAGE and not self.accept_garbage:
            raise RuntimeError("Parse error on line 2: not a diagram")
        title = title_of(source)
        if title in self.fail_titles:
            raise RuntimeError(f"Parse error in {title}")
        return svg_with(*self.counts.get(title, DEFAULT_COUNTS))
