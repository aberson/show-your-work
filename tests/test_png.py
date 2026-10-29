"""``render --png`` (plan section 4 "PNG rendering", sections 3.1 / 3.4, Step 6).

Every behavioural test drives the production caller ``cli.main`` against a tmp copy of a fixture.
Two groups:

* fake-renderer tests (always run): the anchors gate everything, the frozen raster parameters
  reach ``png()``, the ``rasters`` index entries match the files on disk, orphan cleanup obeys the
  section 4 safety rules, and ``--png`` without mermaidx exits 2 with the documented message;
* real-mermaidx tests (skipped with a reason when the optional ``render`` extra is absent): the
  PNG dimensions follow from the same renderer's SVG viewBox at scale 2, and the corner pixel is
  the opaque theme background.

PNG headers and pixels are parsed here with literal offsets (not ``renderer.png_size``), so a
drifted production parser fails these tests instead of grading itself.
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
    KNOWN_GOOD,
    MSG_ANCHOR_BROKEN,
    MSG_PNG_RENDERER_BROKEN,
    MSG_PNG_RENDERER_MISSING,
)
from tests import fakes
from tests.fakes import FAKE_PNG_SIZE, FakeRenderer, without_front_matter

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"
REALISTIC = FIXTURES / "realistic"

EXPECTED_RASTER_KEYS = ["path", "sha256", "width", "height", "bytes"]
OPAQUE_HEX_RE = re.compile(r"^#[0-9a-f]{6}$")
VIEWBOX_RE = re.compile(r'viewBox="([^"]*)"')

HAVE_MERMAIDX = importlib.util.find_spec("mermaidx") is not None

needs_mermaidx = pytest.mark.skipif(
    not HAVE_MERMAIDX,
    reason="mermaidx (extra 'render') not installed; run uv sync --extra dev --extra render",
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


def _corner_pixel(data: bytes) -> tuple[int, ...]:
    """The top-left pixel. Every PNG filter leaves pixel (0, 0) of row 0 unchanged (each
    predictor sees zeros there), so the raw bytes after the filter byte are the pixel."""
    _, _, colour_type = _ihdr(data)
    channels = {2: 3, 6: 4}[colour_type]
    idat = b""
    pos = 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        if tag == b"IDAT":
            idat += data[pos + 8 : pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(idat)
    return tuple(raw[1 : 1 + channels])


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
