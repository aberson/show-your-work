"""Shared test support: a stand-in renderer (mermaidx is an optional extra the default gate never
installs) and the ``check --json`` document readers."""

from __future__ import annotations

import json
import struct
import zlib
from collections.abc import Mapping
from typing import Any

from show_your_work.constants import COMMENT_PREFIX, GARBAGE

#: Counts returned for a diagram whose title is not in the fake's table.
DEFAULT_COUNTS = (2, 1)

#: Natural (scale 1) canvas of every fake raster; ``png(scale=s)`` returns ``s`` times this.
FAKE_PNG_SIZE = (6, 4)

#: Render counts for the over-budget fixtures, keyed by title: the real mermaidx 0.9.5 counts,
#: recorded at build time (and re-checked by the real-renderer calibration test when installed).
OVER_COUNTS: dict[str, tuple[int, int]] = {
    "Fifteen Nodes": (15, 14),
    "Twenty One Edges": (7, 21),
}


#: The viewBox of every fake SVG: ``FAKE_PNG_SIZE`` (the raster canvas probe reads it).
FAKE_VIEWBOX = f"0 0 {FAKE_PNG_SIZE[0]} {FAKE_PNG_SIZE[1]}"


def svg_with(nodes: int, edges: int, viewbox: str = FAKE_VIEWBOX) -> str:
    """A minimal SVG that the section 3.3 count regexes read as ``nodes`` / ``edges``."""
    return (
        f'<svg viewBox="{viewbox}">'
        + '<g class="node default" id="n"></g>' * nodes
        + '<path class="edge-thickness-normal edge-pattern-solid flowchart-link"/>' * edges
        + "</svg>"
    )


def title_of(source: str) -> str:
    first = source.replace("\r\n", "\n").split("\n", 1)[0]
    return first.removeprefix(COMMENT_PREFIX).strip()


def without_front_matter(source: str) -> str:
    """``source`` minus a leading ``---`` ... ``---`` front-matter block, if any."""
    if not source.startswith("---\n"):
        return source
    _, sep, rest = source[4:].partition("\n---\n")
    return rest if sep else source


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def solid_png(width: int, height: int, background: str) -> bytes:
    """A real, valid RGB PNG of ``width`` x ``height`` filled with ``#rrggbb``."""
    rgb = bytes.fromhex(background.removeprefix("#"))
    rows = b"".join(b"\x00" + rgb * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(rows))
        + _chunk(b"IEND", b"")
    )


class FakeRenderer:
    """Records every call; raises on the garbage anchor unless ``accept_garbage``.

    ``counts`` maps a diagram title to the ``(nodes, edges)`` its SVG carries; ``fail_titles``
    lists titles whose render raises (a per-file ``render-error``). ``png`` records
    ``(source, scale, background)`` in ``png_calls`` and returns a solid PNG of
    ``FAKE_PNG_SIZE * scale`` in the background colour; it raises on the garbage anchor unless
    ``accept_garbage_png`` and for titles in ``fail_png_titles``.

    A source carrying the raster front-matter block is a raster-path render: for it ``svg``
    honours ``accept_garbage_png`` (not ``accept_garbage``), and ``viewboxes`` (title -> viewBox
    string) overrides the SVG's viewBox, so a test can fake an oversized canvas.
    """

    def __init__(
        self,
        counts: Mapping[str, tuple[int, int]] | None = None,
        *,
        accept_garbage: bool = False,
        fail_titles: frozenset[str] = frozenset(),
        accept_garbage_png: bool = False,
        fail_png_titles: frozenset[str] = frozenset(),
        viewboxes: Mapping[str, str] | None = None,
    ) -> None:
        self.counts = dict(counts or {})
        self.accept_garbage = accept_garbage
        self.fail_titles = fail_titles
        self.accept_garbage_png = accept_garbage_png
        self.fail_png_titles = fail_png_titles
        self.viewboxes = dict(viewboxes or {})
        self.calls: list[str] = []
        self.png_calls: list[tuple[str, float, str]] = []

    def svg(self, source: str) -> str:
        self.calls.append(source)
        body = without_front_matter(source)
        raster_path = body != source
        accepts = self.accept_garbage_png if raster_path else self.accept_garbage
        if body == GARBAGE and not accepts:
            raise RuntimeError("Parse error on line 2: not a diagram")
        title = title_of(body)
        if title in self.fail_titles:
            raise RuntimeError(f"Parse error in {title}")
        viewbox = self.viewboxes.get(title, FAKE_VIEWBOX)
        return svg_with(*self.counts.get(title, DEFAULT_COUNTS), viewbox=viewbox)

    def png(self, source: str, *, scale: float, background: str) -> bytes:
        self.png_calls.append((source, scale, background))
        body = without_front_matter(source)
        if body == GARBAGE and not self.accept_garbage_png:
            raise RuntimeError("Parse error on line 2: not a diagram")
        title = title_of(body)
        if title in self.fail_png_titles:
            raise RuntimeError(f"raster failed for {title}")
        width, height = FAKE_PNG_SIZE
        return solid_png(int(width * scale), int(height * scale), background)


def json_document(out: str) -> dict[str, Any]:
    """The ``check --json`` document printed as ``out`` (a captured stdout)."""
    document: dict[str, Any] = json.loads(out)
    return document


def rows_by_file(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The ``files`` rows of a ``check --json`` document, keyed by file name."""
    return {row["file"]: row for row in document["files"]}


def finding_codes(row: dict[str, Any]) -> list[str]:
    """The finding codes of one ``check --json`` row, in report order."""
    return [f["code"] for f in row["findings"]]
