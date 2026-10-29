"""``render --png`` (plan section 4 "PNG rendering", sections 3.1 / 3.4, Step 6).

Every behavioural test drives the production caller ``cli.main`` against a tmp copy of a fixture.
Three groups:

* fake-renderer tests (always run): the anchors gate everything, the frozen raster parameters
  reach ``png()``, the ``rasters`` index entries match the files on disk, orphan cleanup obeys the
  section 4 safety rules, and ``--png`` without mermaidx exits 2 with the documented message;
* real-mermaidx tests (skipped with a reason when the optional ``render`` extra is absent): the
  PNG dimensions follow from the same renderer's SVG viewBox at scale 2, and the corner pixel is
  the opaque theme background;
* house rasters (plan Phase 2 Step 10): the bundled fonts, the ``tEXt`` provenance tag, the
  anchor gating and the index provenance and alt rules run with a fake rasterizer (always); the real
  resvg-py pixels, the name ink, the missing-font and garbage anchors and the raster entry-point
  spies skip with a reason when the ``render`` extra is absent.

PNG headers, chunks and pixels are parsed here with literal offsets and a stdlib un-filter (not
``renderer.png_size`` / ``renderer.name_ink``), so a drifted production parser fails these tests
instead of grading itself.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import struct
import sys
import types
import zlib
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from show_your_work import constants, render, renderer
from show_your_work.checks import GateError
from show_your_work.cli import main
from show_your_work.constants import (
    EXIT_FINDINGS,
    EXIT_GATE,
    EXIT_OK,
    GARBAGE,
    HOUSE_GUTTER,
    HOUSE_INK_BOX,
    HOUSE_INK_MIN_PIXELS,
    HOUSE_INK_TOLERANCE,
    HOUSE_MARGIN,
    HOUSE_PALETTES,
    HOUSE_PILL_HEIGHT,
    HOUSE_PILL_WIDTH,
    HOUSE_TOP,
    KNOWN_GOOD,
    MSG_ANCHOR_BROKEN,
    MSG_HOUSE_ANCHOR_BROKEN,
    MSG_PNG_RENDERER_BROKEN,
    MSG_PNG_RENDERER_MISSING,
    PALETTE_SPINE,
    PNG_BYTE_MASK,
    PNG_CRC_BYTES,
    PNG_IDAT_TAG,
    PNG_IHDR_END,
    PNG_IHDR_TAG,
    PNG_RGB_CHANNELS,
    PNG_TEXT_TAG,
    RASTER_LIGHT,
    RASTER_SCALE,
    RASTER_THEMES,
)
from show_your_work.layout import draw_svg, parse_layout
from tests import fakes
from tests.fakes import FAKE_PNG_SIZE, FakeRenderer, layout_project, without_front_matter

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"
REALISTIC = FIXTURES / "realistic"
#: The Step 10 house fixture: a two-lane sidecar diagram beside a sidecar-less one.
PNG_FIXTURE = FIXTURES / "layout" / "png"
SIDECAR_ID = "house"
PLAIN_ID = "plain"
#: The house ``tEXt`` chunk data, keyword NUL text (asserted equal to the constant once).
HOUSE_TEXT_ANCHOR = b"show-your-work\x00house-v1"  # anchor: plan Step 10 tEXt payload
#: The bundled font file -> its SHA-256, regular then bold.
FONT_SHA256_ANCHOR = {  # anchor: plan Phase 2 section 2.6, Step 10 font hashes
    "DejaVuSansMono.ttf": "602ec86b8948cfcd956482fe64f94c36c867770149ef2f791d4613f443bcecb3",
    "DejaVuSansMono-Bold.ttf": "baada9a5172fe20886251aff0433fc38461912d5daf07287e7bee56620a8da96",
}
#: The SHA-256 of the bundled font licence.
FONT_LICENSE_SHA256_ANCHOR = (  # anchor: plan Phase 2 section 2.6, Step 10 licence hash
    "d75938dec098f06f0ac3c00853065d94f020be1c3c62ef1dc2975ba15b4d9b0e"
)
#: Raster theme -> the opaque house PNG background.
BACKGROUNDS_ANCHOR = {"light": "#FFFFFF", "dark": "#161C26"}  # anchor: plan section 2.5
#: The malformed SVG the house garbage anchor must fail to rasterize.
GARBAGE_SVG_ANCHOR = (  # anchor: plan Phase 2 section 2.6, Step 10 garbage SVG
    "<svg xmlns='http://www.w3.org/2000/svg'><g></svg>"
)
#: ``(minimum name-ink pixels, per-channel tolerance)`` of the house ink rule.
INK_RULE_ANCHOR = (20, 8)  # anchor: plan Phase 2 section 2.6, Step 10 ink rule
#: The name ink box of lane 0, node 0 at ``RASTER_SCALE``, as ``(left, top, right, bottom)``.
INK_BOX_ANCHOR = (68, 116, 440, 152)  # anchor: plan Phase 2 section 2.6, Step 10 ink box
#: ``(x, y, RGB)`` of the light spine pixel at the midpoint between the two lanes.
SPINE_PIXEL_ANCHOR = (480, 146, (51, 85, 224))  # anchor: plan Step 10 spine pixel
#: ``(x, y, RGB)`` of a light background pixel off the diagram: the white card fill.
BACKGROUND_PIXEL_ANCHOR = (2, 2, (255, 255, 255))  # anchor: plan Step 10 background pixel

EXPECTED_RASTER_KEYS = ["path", "sha256", "width", "height", "bytes"]
OPAQUE_HEX_RE = re.compile(r"^#[0-9a-f]{6}$")
VIEWBOX_RE = re.compile(r'viewBox="([^"]*)"')

HAVE_MERMAIDX = importlib.util.find_spec("mermaidx") is not None

needs_mermaidx = pytest.mark.skipif(
    not HAVE_MERMAIDX,
    reason="mermaidx (extra 'render') not installed; run uv sync --extra dev --extra render",
)

HAVE_RESVG = importlib.util.find_spec("resvg_py") is not None

needs_render_extra = pytest.mark.skipif(
    not (HAVE_MERMAIDX and HAVE_RESVG),
    reason=(
        "mermaidx / resvg-py (extra 'render') not installed; run uv sync --extra dev --extra render"
    ),
)


# --- helpers ---------------------------------------------------------------------------------


def _copy(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest)
    return dest


def _index(target: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((target / "workflows.json").read_text(encoding="utf-8"))
    return document


def _use(monkeypatch: pytest.MonkeyPatch, fake: object) -> None:
    monkeypatch.setattr(renderer, "load", lambda: fake)


def _ihdr(data: bytes) -> tuple[int, int, int]:
    """``(width, height, colour type)`` read with literal offsets."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    width, height = struct.unpack(">II", data[16:24])
    return width, height, data[25]


def _chunks(data: bytes) -> list[tuple[bytes, bytes, int]]:
    """``(tag, data, crc)`` of every chunk, read with literal offsets."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    chunks: list[tuple[bytes, bytes, int]] = []
    pos = 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        chunks.append((data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length], crc))
        pos += 12 + length
    return chunks


def _decode(data: bytes) -> tuple[int, list[bytes]]:
    """Return ``(channels, rows)``: every pixel row of an 8-bit RGB / RGBA PNG, un-filtered.

    The rows are un-filtered with the PNG specification's five predictors (stdlib only).
    """
    width, height, colour_type = _ihdr(data)
    channels = {2: 3, 6: 4}[colour_type]
    stride = width * channels
    raw = zlib.decompress(b"".join(body for tag, body, _ in _chunks(data) if tag == PNG_IDAT_TAG))
    rows: list[bytes] = []
    prior = bytes(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1 : start + 1 + stride])
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prior[i]
            c = prior[i - channels] if i >= channels else 0
            pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
            paeth = a if pa <= pb and pa <= pc else b if pb <= pc else c
            line[i] = (line[i] + (0, a, b, (a + b) // 2, paeth)[kind]) & PNG_BYTE_MASK
        prior = bytes(line)
        rows.append(prior)
    return channels, rows


def _corner_pixel(data: bytes) -> tuple[int, ...]:
    """The top-left pixel, all channels."""
    channels, rows = _decode(data)
    return tuple(rows[0][:channels])


def _round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


# --- constants (the frozen raster parameters, section 4 / DD5) ----------------------------


def test_raster_constants_have_one_source_of_truth() -> None:
    """Production modules share the constants objects (not equal copies). The values themselves
    are asserted where they matter: at ``png()`` (below) and in the real rendered pixels."""
    assert tuple(constants.RASTER_BACKGROUNDS) == constants.RASTER_THEMES
    assert tuple(constants.RASTER_MERMAID_THEMES) == constants.RASTER_THEMES
    assert vars(renderer)["RASTER_BACKGROUNDS"] is constants.RASTER_BACKGROUNDS
    assert vars(renderer)["RASTER_MERMAID_THEMES"] is constants.RASTER_MERMAID_THEMES
    assert vars(render)["RASTER_THEMES"] is constants.RASTER_THEMES


def test_inject_config_block() -> None:
    source = (GOOD / "minimal.mmd").read_text(encoding="utf-8")
    for theme, mermaid_theme in (("light", "default"), ("dark", "dark")):
        injected = renderer.inject_config(source, theme)
        assert injected.endswith(source)
        block = injected[: -len(source)]
        assert block.startswith("---\nconfig:\n") and block.endswith("\n---\n")
        assert f"  theme: {mermaid_theme}\n" in block
        assert "  flowchart:\n    wrappingWidth: 400\n" in block
    assert "---" not in source  # the committed source stays config-free


# --- the parameters reach png() through the production caller ------------------------------


def test_png_receives_injected_config_scale_and_opaque_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _copy(GOOD, tmp_path / "good")
    source = (target / "minimal.mmd").read_text(encoding="utf-8")
    fake = FakeRenderer()
    _use(monkeypatch, fake)
    assert main(["render", str(target), "--png"]) == EXIT_OK

    diagram_calls = [c for c in fake.png_calls if without_front_matter(c[0]) == source]
    assert len(diagram_calls) == 2
    backgrounds = []
    for injected, scale, background in diagram_calls:
        assert "wrappingWidth: 400" in injected
        assert injected.startswith("---\nconfig:\n")
        assert scale == 2
        assert background != "transparent" and OPAQUE_HEX_RE.fullmatch(background)
        backgrounds.append(background)
    assert backgrounds == ["#ffffff", "#333333"]
    themes = [re.search(r"theme: (\w+)", c[0]).group(1) for c in diagram_calls]  # type: ignore[union-attr]
    assert themes == ["default", "dark"]


def test_mermaidx_renderer_forwards_scale_and_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real wrapper passes ``scale`` / ``background`` to mermaidx's ``Diagram.png``, and the
    canvas probe (``svg``) plus ``png`` share ONE mermaidx diagram object (one layout)."""
    seen: list[tuple[str, dict[str, Any]]] = []
    created: list[str] = []

    class _Diagram:
        def __init__(self, source: str) -> None:
            self.source = source
            created.append(source)

        def svg(self) -> str:
            return '<svg viewBox="0 0 10 10"></svg>'

        def png(self, **kwargs: Any) -> bytes:
            seen.append((self.source, kwargs))
            return b"png-bytes"

    module = types.ModuleType("mermaidx")
    module.render = _Diagram  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mermaidx", module)
    loaded = renderer.load()
    assert loaded is not None
    assert renderer.png(loaded, "flowchart LR\n  A --> B\n", "dark") == b"png-bytes"
    [(source, kwargs)] = seen
    assert kwargs == {"scale": 2, "background": "#333333"}
    assert "wrappingWidth: 400" in source and "theme: dark" in source
    assert created == [source]


# --- the raster canvas cap (checked before png()) ------------------------------------------


@pytest.mark.parametrize(
    ("svg", "expected"),
    [
        (
            '<svg id="a" width="100%" viewBox="-8 -17.3 530.34375 81.9375" style="max-width: 9px">',
            (1060, 164),
        ),
        ('<svg viewBox="0 0 100.5 100.4">', (202, 200)),  # half-up, not banker's rounding
        ('<svg viewBox="0,0,10,10" width="300" height="40px">', (600, 80)),  # larger attr wins
        ('<svg viewBox="0 0 10 10" stroke-width="900" max-width="900">', (20, 20)),
    ],
    ids=["mermaid-shape", "half-up", "absolute-attrs", "lookalike-attrs"],
)
def test_raster_canvas(svg: str, expected: tuple[int, int]) -> None:
    assert renderer.raster_canvas(svg + "<g/></svg>") == expected


@pytest.mark.parametrize(
    "svg",
    [
        "<svg><g/></svg>",
        '<svg viewBox="0 0 10"></svg>',
        '<svg viewBox="0 0 inf 10"></svg>',
        '<svg viewBox="0 0 -5 10"></svg>',
        "<g/>",
    ],
    ids=["no-viewbox", "three-fields", "infinite", "negative", "no-root"],
)
def test_raster_canvas_refuses_an_unbounded_svg(svg: str) -> None:
    with pytest.raises(ValueError):
        renderer.raster_canvas(svg)


def test_check_canvas_caps_side_and_total() -> None:
    side, total = constants.MAX_RASTER_SIDE, constants.MAX_RASTER_PIXELS
    renderer.check_canvas(side, total // side)
    for width, height in ((side + 1, 1), (1, side + 1), (side, total // side + 1)):
        with pytest.raises(renderer.RasterTooLargeError):
            renderer.check_canvas(width, height)


def test_oversized_canvas_exits_2_before_png_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A budget-passing diagram whose SVG is huge (e.g. inflated by a ``%%{init}%%`` directive)
    never reaches the native rasterizer, which would abort the whole process."""
    target = _copy(GOOD, tmp_path / "good")
    before = fakes.snapshot(target)
    fake = FakeRenderer(viewboxes={"Minimal Example": "0 0 50000 50000"})
    _use(monkeypatch, fake)
    assert main(["render", str(target), "--png"]) == EXIT_GATE
    err = capsys.readouterr().err
    assert "cannot rasterize minimal (light): canvas would be 100000x100000 px" in err
    assert fakes.snapshot(target) == before
    for source, _, _ in fake.png_calls:  # only the anchors were rasterized
        assert without_front_matter(source) in (GARBAGE, KNOWN_GOOD)


# --- anchors gate everything ---------------------------------------------------------------


@pytest.mark.parametrize(
    "fake",
    [FakeRenderer(accept_garbage=True), FakeRenderer(accept_garbage_png=True)],
    ids=["svg-garbage-renders", "png-garbage-renders"],
)
def test_broken_anchor_exits_2_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake: FakeRenderer,
) -> None:
    target = _copy(GOOD, tmp_path / "good")
    (target / "rasters").mkdir()
    (target / "rasters" / "stray.png").write_bytes(b"stray")
    before = fakes.snapshot(target)
    _use(monkeypatch, fake)
    assert main(["render", str(target), "--png"]) == EXIT_GATE
    assert MSG_ANCHOR_BROKEN in capsys.readouterr().err
    assert fakes.snapshot(target) == before
    assert not (target / "README.md").exists()
    # No diagram was ever rasterized: only anchor sources reached png().
    for source, _, _ in fake.png_calls:
        assert without_front_matter(source) in (GARBAGE, KNOWN_GOOD)


def test_svg_anchor_runs_before_any_file_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _copy(GOOD, tmp_path / "good")
    fake = FakeRenderer(accept_garbage=True)
    _use(monkeypatch, fake)
    assert main(["render", str(target), "--png"]) == EXIT_GATE
    assert fake.calls == [GARBAGE]
    assert fake.png_calls == []


def test_raster_failure_after_anchors_exits_2_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _copy(REALISTIC, tmp_path / "realistic")
    before = fakes.snapshot(target)
    _use(monkeypatch, FakeRenderer(fail_png_titles=frozenset({"Recipe Scaling Pipeline"})))
    assert main(["render", str(target), "--png"]) == EXIT_GATE
    assert "cannot rasterize recipe-scaling-pipeline (light)" in capsys.readouterr().err
    assert fakes.snapshot(target) == before  # the other four rendered in memory, none written


def test_failing_set_with_png_exits_1_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _copy(GOOD, tmp_path / "good")
    (target / "broken.mmd").write_bytes(b"%% Broken\n%% Summary: no other fields\n\nflowchart LR\n")
    before = fakes.snapshot(target)
    fake = FakeRenderer()
    _use(monkeypatch, fake)
    assert main(["render", str(target), "--png"]) == EXIT_FINDINGS
    assert fakes.snapshot(target) == before
    assert fake.png_calls == []


# --- mermaidx absent / broken --------------------------------------------------------------


def test_png_without_mermaidx_exits_2_with_documented_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "mermaidx", None)  # the real load() then finds no renderer
    target = _copy(GOOD, tmp_path / "good")
    before = fakes.snapshot(target)
    assert main(["render", str(target), "--png"]) == EXIT_GATE
    err = capsys.readouterr().err
    assert err.strip() == f"show-your-work render: {MSG_PNG_RENDERER_MISSING}"
    assert "--png needs the optional 'render' extra (uv sync --extra render)" in err
    assert main(["render", str(target), "--png", "--json"]) == EXIT_GATE
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert (document["exit_code"], document["verb"]) == (EXIT_GATE, "render")
    assert MSG_PNG_RENDERER_MISSING in captured.err
    assert fakes.snapshot(target) == before


def test_png_with_broken_mermaidx_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _broken() -> None:
        raise renderer.RendererLoadError("OSError: native library missing")

    monkeypatch.setattr(renderer, "load", _broken)
    target = _copy(GOOD, tmp_path / "good")
    assert main(["render", str(target), "--png"]) == EXIT_GATE
    expected = MSG_PNG_RENDERER_BROKEN.format(reason="OSError: native library missing")
    assert expected in capsys.readouterr().err


# --- written rasters and the index ---------------------------------------------------------


def test_png_writes_rasters_and_fills_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _copy(GOOD, tmp_path / "good")
    _use(monkeypatch, FakeRenderer())
    assert main(["render", str(target), "--png"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "rasters/minimal-light.png: written" in out
    assert "rasters/minimal-dark.png: written" in out

    item = _index(target)["items"][0]
    assert list(item["rasters"]) == ["light", "dark"]
    width, height = FAKE_PNG_SIZE
    for theme, entry in item["rasters"].items():
        assert list(entry) == EXPECTED_RASTER_KEYS
        assert entry["path"] == f"rasters/minimal-{theme}.png"
        data = (target / entry["path"]).read_bytes()
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
        assert entry["bytes"] == len(data)
        assert (entry["width"], entry["height"]) == _ihdr(data)[:2] == (width * 2, height * 2)
    light, dark = (item["rasters"][t]["sha256"] for t in ("light", "dark"))
    assert light != dark


def test_png_rerun_changes_no_bytes_and_check_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _copy(GOOD, tmp_path / "good")
    _use(monkeypatch, FakeRenderer())
    assert main(["render", str(target), "--png"]) == EXIT_OK
    before = fakes.snapshot(target)
    capsys.readouterr()
    assert main(["render", str(target), "--png"]) == EXIT_OK
    assert "rasters/minimal-dark.png: up to date" in capsys.readouterr().out
    assert fakes.snapshot(target) == before
    assert main(["render", str(target), "--png", "--check"]) == EXIT_OK
    # A plain render keeps the entries (both files exist) and --check without --png agrees.
    assert main(["render", str(target)]) == EXIT_OK
    assert main(["render", str(target), "--check"]) == EXIT_OK
    assert fakes.snapshot(target) == before


def test_png_check_reports_a_changed_raster_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _copy(GOOD, tmp_path / "good")
    _use(monkeypatch, FakeRenderer())
    assert main(["render", str(target), "--png"]) == EXIT_OK
    (target / "rasters" / "minimal-dark.png").write_bytes(b"tampered")
    (target / "rasters" / "stray.png").write_bytes(b"stray")
    before = fakes.snapshot(target)
    capsys.readouterr()
    assert main(["render", str(target), "--png", "--check"]) == EXIT_FINDINGS
    captured = capsys.readouterr()
    assert "rasters/minimal-dark.png: stale" in captured.out
    assert "rasters/stray.png: stale" in captured.out
    assert "rasters/minimal-light.png: up to date" in captured.out
    assert "--png" in captured.err  # the hint names the command that fixes it
    assert fakes.snapshot(target) == before
    # Without --png, --check masks rasters (host-dependent bytes) and passes.
    assert main(["render", str(target), "--check"]) == EXIT_OK


def _assert_index_matches_disk(target: Path) -> None:
    """The invariant: every published raster entry names a file whose bytes it describes."""
    for item in _index(target)["items"]:
        for entry in (item["rasters"] or {}).values():
            data = (target / entry["path"]).read_bytes()
            assert (entry["sha256"], entry["bytes"]) == (
                hashlib.sha256(data).hexdigest(),
                len(data),
            )


@pytest.mark.parametrize("same_length", [True, False], ids=["same-length", "other-length"])
def test_plain_render_nulls_an_entry_whose_file_changed_on_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    same_length: bool,
) -> None:
    """Section 3.4 preserved-or-null, strictly: a raster changed on disk is not re-published
    under its old hash. Plain render nulls the item (and touches no raster file); --png heals."""
    target = _copy(GOOD, tmp_path / "good")
    _use(monkeypatch, FakeRenderer())
    assert main(["render", str(target), "--png"]) == EXIT_OK
    light = target / "rasters" / "minimal-light.png"
    original = light.read_bytes()
    tampered = bytes([original[0] ^ 1]) + original[1:] if same_length else b"tampered"
    light.write_bytes(tampered)
    capsys.readouterr()

    assert main(["render", str(target)]) == EXIT_OK
    assert "workflows.json: written" in capsys.readouterr().out
    assert _index(target)["items"][0]["rasters"] is None
    assert light.read_bytes() == tampered  # plain render never rewrites or deletes rasters
    assert main(["render", str(target), "--check"]) == EXIT_OK

    assert main(["render", str(target), "--png"]) == EXIT_OK
    assert _index(target)["items"][0]["rasters"] is not None
    assert light.read_bytes() == original
    _assert_index_matches_disk(target)


def test_interrupted_png_write_is_detected_and_heals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failure after one new raster is written (exit 2) leaves new PNG + old index; the next
    ``render --png --check`` reports it, a plain ``render`` nulls the mismatched entry, and the
    index never keeps claiming a hash the file no longer has."""
    target = _copy(GOOD, tmp_path / "good")
    _use(monkeypatch, FakeRenderer())
    assert main(["render", str(target), "--png"]) == EXIT_OK
    old_index = (target / "workflows.json").read_bytes()
    monkeypatch.setattr(fakes, "FAKE_PNG_SIZE", (7, 5))  # the next render produces new bytes

    real_write = render.write_atomic

    def _fail_on_dark(path: Path, text: str | bytes, *, mode: int | None = None) -> None:
        if path.name == "minimal-dark.png":
            raise GateError("cannot write minimal-dark.png: disk full")
        real_write(path, text, mode=mode)

    monkeypatch.setattr(render, "write_atomic", _fail_on_dark)
    assert main(["render", str(target), "--png"]) == EXIT_GATE
    assert "disk full" in capsys.readouterr().err
    monkeypatch.setattr(render, "write_atomic", real_write)
    assert (target / "workflows.json").read_bytes() == old_index  # index not written

    assert main(["render", str(target), "--png", "--check"]) == EXIT_FINDINGS
    assert main(["render", str(target)]) == EXIT_OK
    assert _index(target)["items"][0]["rasters"] is None
    _assert_index_matches_disk(target)
    assert main(["render", str(target), "--check"]) == EXIT_OK

    assert main(["render", str(target), "--png"]) == EXIT_OK
    _assert_index_matches_disk(target)
    assert _ihdr((target / "rasters" / "minimal-dark.png").read_bytes())[:2] == (14, 10)


def test_orphans_are_deleted_only_after_the_index_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _copy(GOOD, tmp_path / "good")
    _use(monkeypatch, FakeRenderer())
    assert main(["render", str(target), "--png"]) == EXIT_OK
    stray = target / "rasters" / "stray.png"
    stray.write_bytes(b"stray")
    (target / "minimal.mmd").write_bytes((target / "minimal.mmd").read_bytes() + b"%% edit\n")

    real_write = render.write_atomic

    def _fail_on_index(path: Path, text: str | bytes, *, mode: int | None = None) -> None:
        if path.name == "workflows.json":
            raise GateError("cannot write workflows.json: disk full")
        real_write(path, text, mode=mode)

    monkeypatch.setattr(render, "write_atomic", _fail_on_index)
    assert main(["render", str(target), "--png"]) == EXIT_GATE
    assert stray.read_bytes() == b"stray"  # deletion is the last step, never reached
    monkeypatch.setattr(render, "write_atomic", real_write)
    assert main(["render", str(target), "--png"]) == EXIT_OK
    assert not stray.exists()


# --- orphan cleanup safety rules (section 4) -----------------------------------------------


def test_orphan_cleanup_deletes_only_top_level_regular_pngs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _copy(GOOD, tmp_path / "good")
    rasters = target / "rasters"
    (rasters / "sub").mkdir(parents=True)
    (rasters / "stray.png").write_bytes(b"stray")
    (rasters / "notes.txt").write_bytes(b"keep me")
    (rasters / "sub" / "x.png").write_bytes(b"nested")
    (rasters / "dir.png").mkdir()  # a directory named like a PNG
    (rasters / "Upper.PNG").write_bytes(b"not the *.png suffix")
    _use(monkeypatch, FakeRenderer())
    assert main(["render", str(target), "--png"]) == EXIT_OK
    assert "rasters/stray.png: deleted" in capsys.readouterr().out

    assert not (rasters / "stray.png").exists()
    assert (rasters / "notes.txt").read_bytes() == b"keep me"
    assert (rasters / "sub" / "x.png").read_bytes() == b"nested"
    assert (rasters / "dir.png").is_dir()
    assert (rasters / "Upper.PNG").read_bytes() == b"not the *.png suffix"
    assert sorted(p.name for p in rasters.iterdir()) == sorted(
        ["Upper.PNG", "dir.png", "minimal-dark.png", "minimal-light.png", "notes.txt", "sub"]
    )


def test_orphan_cleanup_removes_a_dropped_diagrams_rasters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _copy(REALISTIC, tmp_path / "realistic")
    _use(monkeypatch, FakeRenderer())
    assert main(["render", str(target), "--png"]) == EXIT_OK
    assert len(list((target / "rasters").iterdir())) == 10
    (target / "kitchen-ticket-flow.mmd").unlink()
    assert main(["render", str(target), "--png"]) == EXIT_OK
    names = sorted(p.name for p in (target / "rasters").iterdir())
    assert len(names) == 8 and not any(n.startswith("kitchen-ticket-flow") for n in names)
    referenced = {
        entry["path"] for item in _index(target)["items"] for entry in item["rasters"].values()
    }
    assert referenced == {f"rasters/{n}" for n in names}


def test_orphan_cleanup_never_follows_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    target = _copy(GOOD, tmp_path / "good")
    (target / "rasters").mkdir()
    link = target / "rasters" / "link.png"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")
    _use(monkeypatch, FakeRenderer())
    assert main(["render", str(target), "--png"]) == EXIT_OK
    assert link.is_symlink()
    assert outside.read_bytes() == b"outside"


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_rasters_that_is_not_a_plain_directory_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    target = _copy(GOOD, tmp_path / "good")
    if kind == "file":
        (target / "rasters").write_bytes(b"not a dir")
    else:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "stray.png").write_bytes(b"stray")
        try:
            os.symlink(elsewhere, target / "rasters", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"cannot create symlinks here: {exc}")
    _use(monkeypatch, FakeRenderer())
    assert main(["render", str(target), "--png"]) == EXIT_GATE
    assert "not a plain directory" in capsys.readouterr().err
    assert not (target / "README.md").exists()
    if kind == "symlink":
        assert (tmp_path / "elsewhere" / "stray.png").read_bytes() == b"stray"


def test_rasters_junction_is_refused_and_its_target_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ``rasters`` junction (``fakes.junction``) is refused like a symlink."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "stray.png").write_bytes(b"stray")
    target = _copy(GOOD, tmp_path / "good")
    fakes.junction(target / "rasters", elsewhere)
    _use(monkeypatch, FakeRenderer())
    assert main(["render", str(target), "--png"]) == EXIT_GATE
    assert "not a plain directory" in capsys.readouterr().err
    assert sorted(p.name for p in elsewhere.iterdir()) == ["stray.png"]
    assert not (target / "README.md").exists()


# --- the real renderer (optional extra) ----------------------------------------------------


@needs_mermaidx
def test_real_png_dimensions_follow_the_svg_viewbox_at_scale_2(tmp_path: Path) -> None:
    """IHDR = 2 x the SVG's viewBox size rounded half-up to whole pixels.

    resvg (mermaidx's rasterizer) first rounds the SVG's intrinsic size to integers, then applies
    the zoom, so the exact relation is ``2 * round_half_up(viewBox)``. Measured on the good
    fixture: viewBox width 530.34375 gives 1060 (``round(2 * 530.34375)`` would be 1061).
    """
    target = _copy(GOOD, tmp_path / "good")
    assert main(["render", str(target), "--png"]) == EXIT_OK
    real = renderer.load()
    assert real is not None
    source = (target / "minimal.mmd").read_text(encoding="utf-8")
    index_item = _index(target)["items"][0]
    for theme in ("light", "dark"):
        data = (target / "rasters" / f"minimal-{theme}.png").read_bytes()
        width, height, _ = _ihdr(data)
        match = VIEWBOX_RE.search(real.svg(renderer.inject_config(source, theme)))
        assert match is not None
        _, _, vb_width, vb_height = (float(v) for v in match.group(1).split())
        assert (width, height) == (2 * _round_half_up(vb_width), 2 * _round_half_up(vb_height))
        assert width != _round_half_up(vb_width)  # the scale really applied (not a 1x render)
        # The pre-png() canvas cap predicts exactly the canvas resvg produced.
        assert renderer.raster_canvas(match.string) == (width, height)
        entry = index_item["rasters"][theme]
        assert (entry["width"], entry["height"]) == (width, height)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
        assert entry["bytes"] == len(data)


@needs_mermaidx
def test_real_png_corner_pixel_is_the_opaque_theme_background(tmp_path: Path) -> None:
    target = _copy(GOOD, tmp_path / "good")
    assert main(["render", str(target), "--png"]) == EXIT_OK
    expected = {"light": (0xFF, 0xFF, 0xFF), "dark": (0x33, 0x33, 0x33)}
    for theme, rgb in expected.items():
        pixel = _corner_pixel((target / "rasters" / f"minimal-{theme}.png").read_bytes())
        assert pixel[:3] == rgb
        if len(pixel) == 4:
            assert pixel[3] == 255  # opaque, never transparent


@needs_mermaidx
def test_real_png_is_deterministic_and_garbage_raises_through_png(tmp_path: Path) -> None:
    real = renderer.load()
    assert real is not None
    assert renderer.raster_anchor_ok(real) is True
    with pytest.raises(Exception):  # noqa: B017 - mermaidx raises a plain RuntimeError
        renderer.png(real, GARBAGE, "dark")
    target = _copy(GOOD, tmp_path / "good")
    assert main(["render", str(target), "--png"]) == EXIT_OK
    before = fakes.snapshot(target)
    assert main(["render", str(target), "--png"]) == EXIT_OK
    assert fakes.snapshot(target) == before
    assert main(["render", str(target), "--png", "--check"]) == EXIT_OK


# --- house rasters (plan Phase 2 section 2.6, Step 10; decisions in plan section 12.D) --------


def _use_house(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer, "_rasterize_svg", fakes.house_rasterizer)


def _item(target: Path, diagram_id: str) -> dict[str, Any]:
    item: dict[str, Any] = next(i for i in _index(target)["items"] if i["id"] == diagram_id)
    return item


def _rgb(colour: str) -> tuple[int, ...]:
    return tuple(bytes.fromhex(colour.removeprefix("#")))


def _near(pixel: tuple[int, ...], colour: str) -> bool:
    """Every RGB channel of ``pixel`` within the plan's tolerance of ``colour`` (``#rrggbb``)."""
    return all(
        abs(have - want) <= HOUSE_INK_TOLERANCE
        for have, want in zip(pixel, _rgb(colour), strict=True)
    )


def _pixel(rows: list[bytes], channels: int, x: int, y: int) -> tuple[int, ...]:
    """The RGB channels of pixel ``(x, y)``."""
    return tuple(rows[y][x * channels : x * channels + PNG_RGB_CHANNELS])


def _ink(data: bytes, colour: str) -> int:
    """Pixels of the scaled ``HOUSE_INK_BOX`` within the tolerance of ``colour``."""
    channels, rows = _decode(data)
    left, top, right, bottom = (RASTER_SCALE * edge for edge in HOUSE_INK_BOX)
    return sum(
        _near(_pixel(rows, channels, x, y), colour)
        for y in range(top, bottom)
        for x in range(left, right)
    )


def _tagged(data: bytes) -> bool:
    """The literal house ``tEXt`` chunk (correct CRC) comes before the first ``IDAT``."""
    for tag, body, crc in _chunks(data):
        if tag == PNG_IDAT_TAG:
            return False
        if (tag, body) == (PNG_TEXT_TAG, HOUSE_TEXT_ANCHOR):
            return crc == zlib.crc32(tag + body)
    return False


def _assert_rasters(target: Path, diagram_id: str, *, house: bool) -> None:
    """Assert ``diagram_id`` has both raster entries, each tagged exactly when ``house``.

    Each entry has the exact keys and a hash equal to its file.
    """
    _assert_index_matches_disk(target)
    rasters = _item(target, diagram_id)["rasters"]
    assert list(rasters) == list(RASTER_THEMES)
    for entry in rasters.values():
        assert list(entry) == EXPECTED_RASTER_KEYS
        assert _tagged((target / entry["path"]).read_bytes()) is house


def test_house_raster_constants_anchor_the_plan_values() -> None:
    """The pinned values of plan Phase 2 are anchored literally and shared by the renderer.

    Anchored: the font hashes, the backgrounds, the garbage SVG and ink rule, the ``tEXt``
    payload; the renderer holds the same objects (not equal copies).
    """
    assert dict(constants.HOUSE_FONT_SHA256) == FONT_SHA256_ANCHOR
    assert constants.HOUSE_FONT_LICENSE_SHA256 == FONT_LICENSE_SHA256_ANCHOR
    assert dict(constants.HOUSE_RASTER_BACKGROUNDS) == BACKGROUNDS_ANCHOR
    assert constants.HOUSE_GARBAGE_SVG == GARBAGE_SVG_ANCHOR
    assert (HOUSE_INK_MIN_PIXELS, HOUSE_INK_TOLERANCE) == INK_RULE_ANCHOR
    assert constants.HOUSE_PNG_TEXT_DATA == HOUSE_TEXT_ANCHOR
    assert tuple(RASTER_SCALE * edge for edge in HOUSE_INK_BOX) == INK_BOX_ANCHOR
    assert vars(renderer)["HOUSE_FONT_SHA256"] is constants.HOUSE_FONT_SHA256
    assert vars(renderer)["HOUSE_RASTER_BACKGROUNDS"] is constants.HOUSE_RASTER_BACKGROUNDS


def test_bundled_fonts_resolve_through_importlib_resources_and_match_their_pinned_hashes() -> None:
    fonts_dir = resources.files(constants.PACKAGE).joinpath(*constants.HOUSE_FONT_PACKAGE_PARTS)
    fonts = renderer.house_fonts()
    assert [font.name for font in fonts] == list(constants.HOUSE_FONT_SHA256)
    for font in fonts:
        assert font == fonts_dir.joinpath(font.name)
        digest = hashlib.sha256(font.read_bytes()).hexdigest()
        assert digest == constants.HOUSE_FONT_SHA256[font.name]
    licence = fonts_dir.joinpath(constants.HOUSE_FONT_LICENSE).read_bytes()
    assert hashlib.sha256(licence).hexdigest() == constants.HOUSE_FONT_LICENSE_SHA256


def test_tag_png_inserts_the_marker_chunk_right_after_ihdr_with_a_correct_crc() -> None:
    plain = fakes.solid_png(4, 2, "#ffffff")
    tagged = renderer.tag_png(plain)
    chunks = _chunks(tagged)
    plain_tags = [tag for tag, _, _ in _chunks(plain)]
    assert [tag for tag, _, _ in chunks] == [PNG_IHDR_TAG, PNG_TEXT_TAG, *plain_tags[1:]]
    assert chunks[1][1:] == (HOUSE_TEXT_ANCHOR, zlib.crc32(PNG_TEXT_TAG + HOUSE_TEXT_ANCHOR))
    assert tagged.replace(constants.HOUSE_PNG_CHUNK, b"") == plain
    assert _tagged(tagged) and renderer.is_house_png(tagged)
    assert not _tagged(plain) and not renderer.is_house_png(plain)


def _inserted(png: bytes, chunk: bytes) -> bytes:
    """``chunk`` inserted directly after the IHDR chunk (which ends at ``PNG_IHDR_END``)."""
    return png[:PNG_IHDR_END] + chunk + png[PNG_IHDR_END:]


def _truncated(png: bytes) -> bytes:
    """The tagged ``png`` cut off just before the marker chunk's CRC."""
    return renderer.tag_png(png)[: PNG_IHDR_END + len(constants.HOUSE_PNG_CHUNK) - PNG_CRC_BYTES]


def _after_idat(png: bytes) -> bytes:
    """The marker chunk moved to just before ``IEND`` (after the image data)."""
    iend = fakes.png_chunk(b"IEND", b"")
    return png.removesuffix(iend) + constants.HOUSE_PNG_CHUNK + iend


@pytest.mark.parametrize(
    ("build", "tagged"),
    [
        (renderer.tag_png, True),
        (_truncated, False),
        (_after_idat, False),
        (
            lambda png: _inserted(png, fakes.png_chunk(PNG_TEXT_TAG, HOUSE_TEXT_ANCHOR.upper())),
            False,
        ),
        (lambda png: _inserted(png, constants.HOUSE_PNG_CHUNK[:-1] + b"\x00"), False),
        (lambda png: b"tampered", False),
        (lambda png: b"", False),
    ],
    ids=["tagged", "truncated", "after-idat", "other-text", "bad-crc", "not-a-png", "empty"],
)
def test_a_png_counts_as_tagged_only_with_the_exact_chunk_before_the_first_idat(
    build: Any, tagged: bool
) -> None:
    data = build(fakes.solid_png(4, 2, "#ffffff"))
    assert renderer.is_house_png(data) is tagged


def test_the_known_good_house_anchor_is_the_step_10_fixture_layout(tmp_path: Path) -> None:
    known_good = parse_layout(constants.HOUSE_KNOWN_GOOD.encode("ascii"))
    assert known_good == parse_layout((PNG_FIXTURE / f"{SIDECAR_ID}.layout.json").read_bytes())
    first, second = known_good.lanes
    assert first.out_node == second.in_node == 0
    assert not first.nodes[0].side
    assert main(["check", str(layout_project(tmp_path / "proj", PNG_FIXTURE))]) == EXIT_OK


@pytest.mark.parametrize(
    ("removed", "expected"),
    [((), ["mermaid", "house"]), ((PLAIN_ID,), ["house"]), ((SIDECAR_ID,), ["mermaid"])],
    ids=["both", "sidecar-only", "sidecar-less-only"],
)
def test_png_runs_each_raster_anchor_only_for_a_path_the_folder_uses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    removed: tuple[str, ...],
    expected: list[str],
) -> None:
    """Each raster anchor runs only for a raster path the folder uses (plan section 12.D).

    The house anchors run only with a sidecar diagram, the mermaid raster anchors only with a
    sidecar-less one.
    """
    target = layout_project(tmp_path / "proj", PNG_FIXTURE)
    for diagram_id in removed:
        for path in target.glob(f"{diagram_id}.*"):
            path.unlink()
    calls: list[str] = []
    house_anchor_ok, raster_anchor_ok = renderer.house_anchor_ok, renderer.raster_anchor_ok

    def _house() -> bool:
        calls.append("house")
        return house_anchor_ok()

    def _mermaid(rasterizer: renderer.RasterRenderer) -> bool:
        calls.append("mermaid")
        return raster_anchor_ok(rasterizer)

    monkeypatch.setattr(renderer, "house_anchor_ok", _house)
    monkeypatch.setattr(renderer, "raster_anchor_ok", _mermaid)
    _use(monkeypatch, FakeRenderer())
    _use_house(monkeypatch)
    assert main(["render", str(target), "--png"]) == EXIT_OK
    assert calls == expected


def test_adding_or_removing_a_sidecar_nulls_rasters_until_png_and_alt_follows_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The raster provenance and alt rules (plan section 12.D) hold after a plain ``render`` and
    after ``--png``.

    A sidecar diagram's ``alt`` is the layout ``alt`` exactly while its ``rasters`` are house
    entries.
    """
    target = layout_project(tmp_path / "proj", PNG_FIXTURE)
    sidecar = target / f"{SIDECAR_ID}.layout.json"
    sidecar_bytes = sidecar.read_bytes()
    layout_alt = parse_layout(sidecar_bytes).alt
    sidecar.unlink()
    _use(monkeypatch, FakeRenderer())
    _use_house(monkeypatch)

    assert main(["render", str(target), "--png"]) == EXIT_OK  # mermaid PNGs, no sidecar yet
    _assert_rasters(target, SIDECAR_ID, house=False)
    header_alt = _item(target, SIDECAR_ID)["alt"]
    assert header_alt != layout_alt

    sidecar.write_bytes(sidecar_bytes)
    assert main(["render", str(target)]) == EXIT_OK
    item = _item(target, SIDECAR_ID)
    assert (item["rasters"], item["alt"]) == (None, header_alt)
    _assert_rasters(target, PLAIN_ID, house=False)  # the sidecar-less neighbour is untouched

    for command in (["render", str(target), "--png"], ["render", str(target)]):
        assert main(command) == EXIT_OK
        _assert_rasters(target, SIDECAR_ID, house=True)
        assert _item(target, SIDECAR_ID)["alt"] == layout_alt

    sidecar.unlink()
    assert main(["render", str(target)]) == EXIT_OK
    item = _item(target, SIDECAR_ID)
    assert (item["rasters"], item["alt"]) == (None, header_alt)
    assert main(["render", str(target), "--png"]) == EXIT_OK
    _assert_rasters(target, SIDECAR_ID, house=False)
    assert _item(target, SIDECAR_ID)["alt"] == header_alt


@pytest.fixture(scope="module")
def house_rasters(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One real ``render --png`` of the Step 10 fixture, shared read-only by the pixel tests."""
    target = layout_project(tmp_path_factory.mktemp("house"), PNG_FIXTURE)
    assert main(["render", str(target), "--png"]) == EXIT_OK
    return target


@needs_render_extra
def test_real_house_pngs_are_twice_the_viewbox_tagged_and_identical_across_runs(
    house_rasters: Path, tmp_path: Path
) -> None:
    again = layout_project(tmp_path / "proj", PNG_FIXTURE)
    assert main(["render", str(again), "--png"]) == EXIT_OK
    for theme in RASTER_THEMES:
        name = f"{SIDECAR_ID}-{theme}"
        data = (house_rasters / "rasters" / f"{name}.png").read_bytes()
        svg = (house_rasters / "rasters" / f"{name}.svg").read_text(encoding="utf-8")
        match = VIEWBOX_RE.search(svg)
        assert match is not None
        _, _, vb_width, vb_height = (int(v) for v in match.group(1).split())
        assert _ihdr(data)[:2] == (RASTER_SCALE * vb_width, RASTER_SCALE * vb_height)
        assert _tagged(data)
        assert (again / "rasters" / f"{name}.png").read_bytes() == data
    assert main(["render", str(again), "--png", "--check"]) == EXIT_OK


@needs_render_extra
def test_real_light_house_png_has_the_spine_at_its_midpoint_on_a_white_card(
    house_rasters: Path,
) -> None:
    channels, rows = _decode((house_rasters / "rasters" / f"{SIDECAR_ID}-light.png").read_bytes())
    spine = HOUSE_PALETTES[RASTER_LIGHT][PALETTE_SPINE]
    x = RASTER_SCALE * (HOUSE_MARGIN + HOUSE_PILL_WIDTH + HOUSE_GUTTER // 2)
    y = RASTER_SCALE * (HOUSE_TOP + HOUSE_PILL_HEIGHT // 2)
    assert (x, y, _rgb(spine)) == SPINE_PIXEL_ANCHOR
    assert _near(_pixel(rows, channels, x, y), spine)
    bx, by, card = BACKGROUND_PIXEL_ANCHOR
    assert card == _rgb(constants.HOUSE_RASTER_BACKGROUNDS[RASTER_LIGHT])
    background = _pixel(rows, channels, bx, by)
    assert background == card
    assert not _near(background, spine)  # red on garbage: the matcher rejects the background


@needs_render_extra
def test_real_house_png_has_name_ink_and_a_missing_font_fails_the_house_anchor(
    house_rasters: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spine = HOUSE_PALETTES[RASTER_LIGHT][PALETTE_SPINE]
    name = f"{SIDECAR_ID}-light"
    assert _ink((house_rasters / "rasters" / f"{name}.png").read_bytes(), spine) >= (
        HOUSE_INK_MIN_PIXELS
    )
    assert renderer.house_anchor_ok() is True

    monkeypatch.setattr(renderer, "house_fonts", lambda: (tmp_path / "missing.ttf",))
    svg = (house_rasters / "rasters" / f"{name}.svg").read_text(encoding="utf-8")
    assert _ink(renderer.house_png(svg, RASTER_LIGHT), spine) < HOUSE_INK_MIN_PIXELS
    assert renderer.house_anchor_ok() is False
    target = layout_project(tmp_path / "proj", PNG_FIXTURE)
    before = fakes.snapshot(target)
    assert main(["render", str(target), "--png"]) == EXIT_GATE
    assert MSG_HOUSE_ANCHOR_BROKEN in capsys.readouterr().err
    assert fakes.snapshot(target) == before


@needs_render_extra
def test_real_png_rasterizes_a_sidecar_less_diagram_through_mermaid_beside_a_house_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spies on both raster entry points to prove each diagram takes its own path.

    Every call is recorded with its call-site tag. The mermaid anchor renders are set aside by
    their sources; the house anchor renders by their "anchor" tag (the known-good anchor IS the
    fixture layout, so its SVG equals the diagram's).
    """
    calls: list[tuple[str, str, str]] = []
    site = "diagram"
    png, house_png, house_anchor_ok = renderer.png, renderer.house_png, renderer.house_anchor_ok

    def _png(rasterizer: renderer.RasterRenderer, source: str, theme: str) -> bytes:
        calls.append(("mermaid", source, theme))
        return png(rasterizer, source, theme)

    def _house_png(svg: str, theme: str) -> bytes:
        calls.append((site, svg, theme))
        return house_png(svg, theme)

    def _house_anchor() -> bool:
        nonlocal site
        site = "anchor"
        try:
            return house_anchor_ok()
        finally:
            site = "diagram"

    def _tagged(tag: str) -> list[tuple[str, str]]:
        return [(text, theme) for site_tag, text, theme in calls if site_tag == tag]

    monkeypatch.setattr(renderer, "png", _png)
    monkeypatch.setattr(renderer, "house_png", _house_png)
    monkeypatch.setattr(renderer, "house_anchor_ok", _house_anchor)
    target = layout_project(tmp_path / "proj", PNG_FIXTURE)
    assert main(["render", str(target), "--png"]) == EXIT_OK

    plain = (target / f"{PLAIN_ID}.mmd").read_text(encoding="utf-8")
    rasters = target / "rasters"
    house = [(rasters / f"{SIDECAR_ID}-{t}.svg").read_text(encoding="utf-8") for t in RASTER_THEMES]
    expected_house = list(zip(house, RASTER_THEMES, strict=True))
    assert [c for c in _tagged("mermaid") if c[0] not in (GARBAGE, KNOWN_GOOD)] == [
        (plain, theme) for theme in RASTER_THEMES
    ]
    assert _tagged("diagram") == expected_house
    assert _tagged("anchor") == expected_house  # the anchors ran, on the known-good layout
    _assert_rasters(target, SIDECAR_ID, house=True)
    _assert_rasters(target, PLAIN_ID, house=False)


@needs_render_extra
def test_a_garbage_house_anchor_that_does_not_raise_exits_2_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(ValueError):  # resvg itself rejects the real garbage SVG
        renderer._rasterize_svg(constants.HOUSE_GARBAGE_SVG, RASTER_LIGHT)
    known_good = parse_layout(constants.HOUSE_KNOWN_GOOD.encode("ascii"))
    monkeypatch.setattr(renderer, "HOUSE_GARBAGE_SVG", draw_svg(known_good, RASTER_LIGHT))
    target = layout_project(tmp_path / "proj", PNG_FIXTURE)
    before = fakes.snapshot(target)
    assert main(["render", str(target), "--png"]) == EXIT_GATE
    assert MSG_HOUSE_ANCHOR_BROKEN in capsys.readouterr().err
    assert fakes.snapshot(target) == before
    assert not (target / "README.md").exists()
