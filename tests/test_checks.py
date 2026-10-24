"""Tests for ``checks.check_dir`` and the optional ``renderer`` wrapper (plan sections 3.2-3.5, 5).

Behaviour already proven through ``cli.main`` (good fixture, over-budget render counts, empty
dir, anchor-before-files) lives in ``test_cli.py`` only. mermaidx is never required: a
:class:`tests.fakes.FakeRenderer` stands in, except for one calibration test that runs the real
pinned renderer when the ``render`` extra happens to be installed.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import types
from pathlib import Path

import pytest

from show_your_work import checks, renderer
from show_your_work.checks import FileResult, GateError, Report, check_dir, check_source
from show_your_work.constants import (
    CODE_ABS_PATH,
    CODE_BUDGET_EDGE_LABEL,
    CODE_BUDGET_EDGES,
    CODE_BUDGET_NODE_LABEL,
    CODE_BUDGET_NODES,
    CODE_BUDGET_SUBGRAPHS,
    CODE_EXCALIDRAW_ORPHAN,
    CODE_HEADER_MISSING_ALT,
    CODE_HEADER_MISSING_NOT_SHOWN,
    CODE_HEADER_MISSING_SOURCES,
    CODE_HEADER_MISSING_SUMMARY,
    CODE_HEADER_MISSING_TITLE,
    CODE_ID_DUPLICATE,
    CODE_ID_GRAMMAR,
    CODE_NON_ASCII,
    CODE_RENDER_ERROR,
    CODE_SUBGRAPH_UNBALANCED,
    EXIT_FINDINGS,
    EXIT_OK,
    GARBAGE,
    KNOWN_GOOD,
    MAX_EDGE_LABEL,
    MAX_EDGES,
    MAX_NODE_LABEL,
    MAX_NODES,
    MAX_RENDER_ERROR_DETAIL,
    MAX_SOURCE_BYTES,
    MAX_SUBGRAPHS,
)
from tests.fakes import OVER_COUNTS, FakeRenderer, svg_with

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"
OVER = FIXTURES / "over-budget"
GARBAGE_DIR = FIXTURES / "garbage"

DRIVE = "C"  # joined with ":/" only at runtime; never a literal absolute path in the repo

#: Source-checkable over-budget fixtures -> the exact codes each must produce without a renderer.
OVER_SOURCE_CODES: dict[str, list[str]] = {
    "edge-label-41.mmd": [CODE_BUDGET_EDGE_LABEL],
    "edges-21.mmd": [],
    "node-label-61.mmd": [CODE_BUDGET_NODE_LABEL],
    "nodes-15.mmd": [],
    "subgraphs-3.mmd": [CODE_BUDGET_SUBGRAPHS],
    "unbalanced-end.mmd": [CODE_SUBGRAPH_UNBALANCED],
}

#: Wall-clock bound per pathological input. The pre-fix regexes needed 35 s for 4 KB (cubic)
#: and 6.5 s for 32 KB (quadratic); these inputs are 16x-64x larger, so any super-linear
#: regression blows far past the bound while linear matching takes milliseconds.
REDOS_BOUND_SECONDS = 1.0
REDOS_SIZE = 64 * 1024


def _diagram(title: str = "Tiny Flow", body: str = "    A[One] --> B[Two]\n") -> str:
    return (
        f"%% {title}\n"
        "%% Summary: Two steps joined by one arrow.\n"
        "%% Sources:\n"
        "%%   - docs/example.md\n"
        "%% Not shown: everything else\n"
        "%% Alt: Two boxes side by side joined by a single arrow.\n"
        "\n"
        "flowchart LR\n" + body
    )


def _write(path: Path, text: str) -> Path:
    path.write_bytes(text.encode("utf-8"))
    return path


def _codes(result: FileResult) -> list[str]:
    return [f.code for f in result.findings]


def _by_file(report: Report) -> dict[str, FileResult]:
    return {r.file: r for r in report.files}


# --- renderer wrapper -----------------------------------------------------------------------


def test_svg_counts_reads_nodes_and_edges() -> None:
    assert renderer.svg_counts(svg_with(5, 7)) == (5, 7)


def test_svg_counts_falls_back_to_link_ids_when_no_edge_classes() -> None:
    svg = '<g class="node"></g><path id="L_A_B_0"/><path id="L-B-C-0"/>'
    assert renderer.svg_counts(svg) == (1, 2)


def test_anchor_ok_requires_garbage_to_raise_and_known_good_to_render() -> None:
    fake = FakeRenderer()
    assert renderer.anchor_ok(fake) is True
    assert fake.calls == [GARBAGE, KNOWN_GOOD]
    assert renderer.anchor_ok(FakeRenderer(accept_garbage=True)) is False


def test_anchor_rejects_a_renderer_that_raises_on_everything() -> None:
    class _AlwaysRaises:
        def svg(self, source: str) -> str:
            raise RuntimeError("broken")

    assert renderer.anchor_ok(_AlwaysRaises()) is False


def test_anchor_rejects_a_known_good_render_with_no_nodes() -> None:
    fake = FakeRenderer({"flowchart LR": (0, 0)})  # KNOWN_GOOD's first line is its "title"
    assert renderer.anchor_ok(fake) is False


def test_load_returns_none_when_mermaidx_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mermaidx", None)  # makes the import raise ImportError
    assert renderer.load() is None


def test_load_reports_a_broken_install_distinctly(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(name: str) -> types.ModuleType:
        raise OSError("native library missing")

    monkeypatch.setattr(renderer, "importlib", types.SimpleNamespace(import_module=_boom))
    with pytest.raises(renderer.RendererLoadError, match="OSError: native library missing"):
        renderer.load()


def test_load_wraps_mermaidx_render_svg(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class _Diagram:
        def __init__(self, source: str) -> None:
            self.source = source

        def svg(self) -> str:
            seen.append(self.source)
            return svg_with(1, 0)

    module = types.ModuleType("mermaidx")
    module.render = _Diagram  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mermaidx", module)
    loaded = renderer.load()
    assert isinstance(loaded, renderer.MermaidxRenderer)
    assert loaded.svg("flowchart LR\n  A") == svg_with(1, 0)
    assert seen == ["flowchart LR\n  A"]


def test_real_mermaidx_is_calibrated_when_installed() -> None:
    """Known-garbage raises and known shapes count exactly (skips without the render extra)."""
    pytest.importorskip("mermaidx")
    real = renderer.load()
    assert real is not None
    assert renderer.anchor_ok(real) is True
    files = _by_file(check_dir(OVER, renderer=real))
    for title_file, title in (
        ("nodes-15.mmd", "Fifteen Nodes"),
        ("edges-21.mmd", "Twenty One Edges"),
    ):
        assert (files[title_file].nodes, files[title_file].edges) == OVER_COUNTS[title]
    assert _by_file(check_dir(GOOD, renderer=real))["minimal.mmd"].passed


# --- fixtures -------------------------------------------------------------------------------


def test_over_budget_source_checks_name_exact_codes() -> None:
    report = check_dir(OVER)
    assert {r.file: _codes(r) for r in report.files} == OVER_SOURCE_CODES
    assert all(r.nodes is None and r.edges is None for r in report.files)
    assert report.exit_code == EXIT_FINDINGS


def test_garbage_fixture_is_the_embedded_anchor_and_fails_every_gate() -> None:
    assert (GARBAGE_DIR / "garbage.mmd").read_bytes().decode("ascii") == GARBAGE
    result = check_dir(GARBAGE_DIR, renderer=FakeRenderer()).files[0]
    assert _codes(result) == [
        CODE_HEADER_MISSING_TITLE,
        CODE_HEADER_MISSING_SUMMARY,
        CODE_HEADER_MISSING_SOURCES,
        CODE_HEADER_MISSING_NOT_SHOWN,
        CODE_HEADER_MISSING_ALT,
        CODE_SUBGRAPH_UNBALANCED,
        CODE_RENDER_ERROR,
    ]


def test_fixtures_are_lf_ascii() -> None:
    for folder in (GOOD, OVER, GARBAGE_DIR):
        for path in folder.glob("*.mmd"):
            raw = path.read_bytes()
            assert b"\r" not in raw, path.name
            assert raw.isascii(), path.name


# --- budget boundaries ----------------------------------------------------------------------


def test_budget_limits_are_inclusive(tmp_path: Path) -> None:
    node_label = "n" * MAX_NODE_LABEL
    edge_label = "e" * MAX_EDGE_LABEL
    body = f"    A[{node_label}] -->|{edge_label}| B[Two]\n"
    _write(tmp_path / "edge.mmd", _diagram("At The Limit", body))
    fake = FakeRenderer({"At The Limit": (MAX_NODES, MAX_EDGES)})
    report = check_dir(tmp_path, renderer=fake)
    assert report.files[0].findings == ()
    over = FakeRenderer({"At The Limit": (MAX_NODES + 1, MAX_EDGES + 1)})
    assert _codes(check_dir(tmp_path, renderer=over).files[0]) == [
        CODE_BUDGET_NODES,
        CODE_BUDGET_EDGES,
    ]


def test_subgraphs_at_the_limit_pass(tmp_path: Path) -> None:
    lanes = "".join(
        f"    subgraph lane{i}\n        N{i}[Step]\n    end\n" for i in range(MAX_SUBGRAPHS)
    )
    _write(tmp_path / "lanes.mmd", _diagram(body=lanes))
    assert check_dir(tmp_path).files[0].findings == ()


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ('    A["' + "q" * (MAX_NODE_LABEL + 1) + '"] --> B\n', CODE_BUDGET_NODE_LABEL),
        ("    A[" + "q" * (MAX_NODE_LABEL + 1) + "] --> B\n", CODE_BUDGET_NODE_LABEL),
        ('    A -->|"' + "q" * (MAX_EDGE_LABEL + 1) + '"| B\n', CODE_BUDGET_EDGE_LABEL),
        ('    A -- "' + "q" * (MAX_EDGE_LABEL + 1) + '" --> B\n', CODE_BUDGET_EDGE_LABEL),
        ('    A -. "' + "q" * (MAX_EDGE_LABEL + 1) + '" .-> B\n', CODE_BUDGET_EDGE_LABEL),
    ],
)
def test_every_label_regex_is_applied(tmp_path: Path, body: str, code: str) -> None:
    _write(tmp_path / "labels.mmd", _diagram(body=body))
    assert _codes(check_dir(tmp_path).files[0]) == [code]


@pytest.mark.parametrize("arrow", [("--", "-->"), ("-.", ".->")], ids=["solid", "dotted"])
def test_multi_line_quoted_edge_label_is_measured(tmp_path: Path, arrow: tuple[str, str]) -> None:
    """Spec 3.3 quoted captures cross newlines; a wrapped edge label is still budgeted."""
    half = "q" * (MAX_EDGE_LABEL // 2 + 1)
    body = f'    A {arrow[0]} "{half}\n{half}" {arrow[1]} B\n'
    _write(tmp_path / "wrapped.mmd", _diagram(body=body))
    assert _codes(check_dir(tmp_path).files[0]) == [CODE_BUDGET_EDGE_LABEL]


def test_empty_pipe_pair_is_not_a_label(tmp_path: Path) -> None:
    body = "    A -->|| B\n    B -->|ok| C\n"
    _write(tmp_path / "empty.mmd", _diagram(body=body))
    assert check_dir(tmp_path).files[0].findings == ()


def test_quoted_edge_label_quotes_do_not_count(tmp_path: Path) -> None:
    body = '    A -->|"' + "q" * MAX_EDGE_LABEL + '"| B\n'
    _write(tmp_path / "quoted.mmd", _diagram(body=body))
    assert check_dir(tmp_path).files[0].findings == ()


def test_pipes_inside_a_quoted_node_label_are_not_an_edge_label(tmp_path: Path) -> None:
    body = '    A["p|' + "q" * (MAX_EDGE_LABEL + 1) + '|r"] --> B\n'
    _write(tmp_path / "pipes.mmd", _diagram(body=body))
    assert check_dir(tmp_path).files[0].findings == ()


def test_subgraph_lines_inside_comments_are_ignored(tmp_path: Path) -> None:
    body = "    %% subgraph commented\n    A --> B\n"
    _write(tmp_path / "commented.mmd", _diagram(body=body))
    assert check_dir(tmp_path).files[0].findings == ()


@pytest.mark.parametrize(
    "hostile",
    [
        "|" + " " * REDOS_SIZE,  # was cubic: ``\s*`` vs lazy class vs ``\s*``
        "|" + "\t" * REDOS_SIZE,
        "[(" * (REDOS_SIZE // 2),  # was quadratic: every bracket of the run was a start
        "[" * REDOS_SIZE,
        "{" * REDOS_SIZE,
        '["' * (REDOS_SIZE // 2),
        "-- " + " " * REDOS_SIZE,
        '-- "' * (REDOS_SIZE // 4),
        "-. " + " " * REDOS_SIZE,
        "\n" * REDOS_SIZE,  # line-anchored subgraph / end regexes
        " \n" * (REDOS_SIZE // 2),
        # quoted captures cross newlines (spec 3.3), so newline-heavy unclosed shapes too
        '["' + "\n" * REDOS_SIZE,
        '["' + "x\n" * (REDOS_SIZE // 2),
        "[" + "\n" * REDOS_SIZE,
        '[\n"' * (REDOS_SIZE // 3),
        '["x"' + "\n" * REDOS_SIZE,
        '-- "' + "x\n" * (REDOS_SIZE // 2),
        '-- "x"' + "\n" * REDOS_SIZE,
        '-. "' * (REDOS_SIZE // 4),
        "||" * (REDOS_SIZE // 2),
    ],
    ids=lambda s: f"{s[:3]!r}x{len(s)}",
)
def test_label_and_subgraph_regexes_are_linear_on_hostile_input(hostile: str) -> None:
    raw = _diagram(body=hostile).encode("ascii")
    started = time.perf_counter()
    check_source("hostile.mmd", raw)
    elapsed = time.perf_counter() - started
    assert elapsed < REDOS_BOUND_SECONDS, f"{elapsed:.2f}s"


# --- identity, structure, file-level checks -------------------------------------------------


@pytest.mark.parametrize("stem", ["Upper", "under_score", "-leading", "a" * 65, "dot.ted"])
def test_bad_id_is_rejected_not_sanitized(tmp_path: Path, stem: str) -> None:
    _write(tmp_path / f"{stem}.mmd", _diagram())
    result = check_dir(tmp_path).files[0]
    assert result.id == stem
    assert _codes(result) == [CODE_ID_GRAMMAR]


def test_good_ids_pass(tmp_path: Path) -> None:
    for stem in ("a", "0-start", "release-checklist", "a" * 64):
        _write(tmp_path / f"{stem}.mmd", _diagram())
    assert check_dir(tmp_path).exit_code == EXIT_OK


def test_case_folded_duplicate_ids_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case variants cannot coexist on Windows, so discovery is faked to list both."""
    raw = _diagram().encode("ascii")
    listed = [tmp_path / "Flow.mmd", tmp_path / "flow.mmd", tmp_path / "other.mmd"]
    monkeypatch.setattr(checks, "discover", lambda target: (listed, []))
    monkeypatch.setattr(checks, "_read_source", lambda path: raw)
    files = _by_file(check_dir(tmp_path))
    assert _codes(files["flow.mmd"]) == [CODE_ID_DUPLICATE]
    assert set(_codes(files["Flow.mmd"])) == {CODE_ID_DUPLICATE, CODE_ID_GRAMMAR}
    assert "'Flow.mmd'" in files["flow.mmd"].findings[0].message
    assert files["other.mmd"].findings == ()


def test_excalidraw_orphan_fails_and_matched_sibling_passes(tmp_path: Path) -> None:
    _write(tmp_path / "flow.mmd", _diagram())
    _write(tmp_path / "flow.excalidraw.svg", "<svg/>")
    _write(tmp_path / "lost.excalidraw.svg", "<svg/>")
    report = check_dir(tmp_path)
    assert [r.file for r in report.files] == ["flow.mmd", "lost.excalidraw.svg"]
    orphan = report.files[1]
    assert (orphan.id, orphan.nodes, orphan.edges) == ("lost", None, None)
    assert _codes(orphan) == [CODE_EXCALIDRAW_ORPHAN]
    assert report.exit_code == EXIT_FINDINGS


def test_discovery_is_top_level_only(tmp_path: Path) -> None:
    shutil.copy(GOOD / "minimal.mmd", tmp_path / "minimal.mmd")
    for nested in ("rasters", "sub"):
        (tmp_path / nested).mkdir()
        shutil.copy(GARBAGE_DIR / "garbage.mmd", tmp_path / nested / "garbage.mmd")
    (tmp_path / "folder.mmd").mkdir()  # a directory named like a source is not a source
    _write(tmp_path / "notes.txt", "not a diagram")
    report = check_dir(tmp_path)
    assert [r.file for r in report.files] == ["minimal.mmd"]
    assert report.exit_code == EXIT_OK


def test_non_ascii_file_is_flagged_with_its_line(tmp_path: Path) -> None:
    _write(tmp_path / "accent.mmd", _diagram(body="    A[Café] --> B\n"))
    result = check_dir(tmp_path).files[0]
    assert _codes(result) == [CODE_NON_ASCII]
    assert "line 9" in result.findings[0].message


def test_users_path_anywhere_is_abs_path(tmp_path: Path) -> None:
    home = "Users"  # assembled at runtime: no literal home path in the public repo
    body = f"    A[{DRIVE}:/{home}/someone/notes] --> B\n"
    _write(tmp_path / "leak.mmd", _diagram(body=body))
    assert _codes(check_dir(tmp_path).files[0]) == [CODE_ABS_PATH]


def test_absolute_source_from_bad_headers_fixture_fails(tmp_path: Path) -> None:
    text = (FIXTURES / "bad-headers" / "absolute-path.mmd").read_text(encoding="utf-8")
    _write(tmp_path / "absolute-path.mmd", text.replace("<drive>", DRIVE))
    assert _codes(check_dir(tmp_path).files[0]) == [CODE_ABS_PATH]


def test_crlf_source_checks_like_lf(tmp_path: Path) -> None:
    lf = (GOOD / "minimal.mmd").read_text(encoding="utf-8")
    _write(tmp_path / "minimal.mmd", lf.replace("\n", "\r\n"))
    fake = FakeRenderer()
    assert check_dir(tmp_path, renderer=fake).exit_code == EXIT_OK
    assert "\r" not in fake.calls[-1]


def test_render_error_is_a_per_file_finding(tmp_path: Path) -> None:
    _write(tmp_path / "broken.mmd", _diagram("Broken Flow"))
    _write(tmp_path / "fine.mmd", _diagram("Fine Flow"))
    fake = FakeRenderer(fail_titles=frozenset({"Broken Flow"}))
    files = _by_file(check_dir(tmp_path, renderer=fake))
    assert _codes(files["broken.mmd"]) == [CODE_RENDER_ERROR]
    assert (files["broken.mmd"].nodes, files["broken.mmd"].edges) == (None, None)
    assert files["fine.mmd"].passed


def test_render_error_detail_is_bounded() -> None:
    class _Loud:
        def svg(self, source: str) -> str:
            raise RuntimeError("x" * (MAX_RENDER_ERROR_DETAIL * 3))

    result = check_source("loud.mmd", _diagram().encode("ascii"), renderer=_Loud())
    message = result.findings[0].message
    assert message.endswith("...")
    assert len(message) < MAX_RENDER_ERROR_DETAIL + 50


def test_anchors_run_first_then_every_file(tmp_path: Path) -> None:
    shutil.copy(GOOD / "minimal.mmd", tmp_path / "minimal.mmd")
    fake = FakeRenderer()
    check_dir(tmp_path, renderer=fake)
    assert fake.calls[:2] == [GARBAGE, KNOWN_GOOD]
    assert len(fake.calls) == 3


# --- gate conditions (exit 2 through the CLI is covered in test_cli.py) ---------------------


def test_only_orphan_drawings_cannot_run(tmp_path: Path) -> None:
    _write(tmp_path / "lost.excalidraw.svg", "<svg/>")
    with pytest.raises(GateError, match=r"no \*\.mmd files"):
        check_dir(tmp_path)


def test_missing_or_file_target_cannot_run(tmp_path: Path) -> None:
    with pytest.raises(GateError, match="not a directory"):
        check_dir(tmp_path / "absent")
    afile = _write(tmp_path / "file.mmd", _diagram())
    with pytest.raises(GateError, match="not a directory"):
        check_dir(afile)


def test_source_at_the_size_cap_is_read(tmp_path: Path) -> None:
    text = _diagram()
    padded = text + "\n" * (MAX_SOURCE_BYTES - len(text))
    _write(tmp_path / "big.mmd", padded)
    assert (tmp_path / "big.mmd").stat().st_size == MAX_SOURCE_BYTES
    assert check_dir(tmp_path).exit_code == EXIT_OK


def test_oversized_source_cannot_run(tmp_path: Path) -> None:
    text = _diagram()
    _write(tmp_path / "huge.mmd", text + "\n" * (MAX_SOURCE_BYTES + 1 - len(text)))
    with pytest.raises(GateError, match=f"larger than {MAX_SOURCE_BYTES} bytes"):
        check_dir(tmp_path)


def test_symlinked_source_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic on every host: ``is_symlink`` is faked for one entry."""
    _write(tmp_path / "linked.mmd", _diagram())
    real = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "linked.mmd" or real(self))
    with pytest.raises(GateError, match="refusing to read symlinked source"):
        check_dir(tmp_path)


def test_real_symlink_to_outside_target_is_refused(tmp_path: Path) -> None:
    outside = _write(tmp_path / "outside.txt", _diagram())
    target = tmp_path / "workflows"
    target.mkdir()
    try:
        os.symlink(outside, target / "escape.mmd")
    except (OSError, NotImplementedError) as exc:  # Windows without symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    with pytest.raises(GateError, match="refusing to read symlinked source"):
        check_dir(target)
