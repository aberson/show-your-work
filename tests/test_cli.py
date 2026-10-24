"""CLI contract (plan section 4): usage errors exit 64; ``check`` exits 0/1/2 with table or JSON."""

from __future__ import annotations

import builtins
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from show_your_work import __version__, renderer
from show_your_work.checks import FileResult, Finding, Report
from show_your_work.cli import format_table, main
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
    CODE_HEADER_UNKNOWN_LABEL,
    CODE_NON_ASCII,
    CODE_RENDER_ERROR,
    CODE_SUBGRAPH_UNBALANCED,
    COUNTS_NOTE,
    EXIT_FINDINGS,
    EXIT_GATE,
    EXIT_OK,
    EXIT_USAGE,
    GARBAGE,
    KNOWN_GOOD,
    MAX_NODE_LABEL,
    MAX_SOURCE_BYTES,
    MSG_ANCHOR_BROKEN,
    MSG_NO_DEFAULT_TARGET,
    MSG_NO_SOURCES,
    MSG_RENDERER_BROKEN,
    MSG_RENDERER_MISSING,
    REPORT_SCHEMA,
)
from tests.fakes import OVER_COUNTS, FakeRenderer


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["bogus"],
        ["check", "--bogus"],
        ["check", "a", "b"],
    ],
)
def test_usage_errors_exit_64_with_usage(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == EXIT_USAGE
    err = capsys.readouterr().err
    assert err.startswith("usage: show-your-work")
    assert "error:" in err


def test_help_and_version_exit_0(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == EXIT_OK
    assert "usage: show-your-work" in capsys.readouterr().out
    assert main(["--version"]) == EXIT_OK
    assert capsys.readouterr().out.strip() == f"show-your-work {__version__}"


def _console_script() -> Path:
    scripts = Path(sys.executable).parent
    found = shutil.which("show-your-work", path=str(scripts))
    if found is None:
        pytest.fail(f"console script 'show-your-work' not installed next to {sys.executable}")
    return Path(found)


@pytest.mark.parametrize("argv", [[], ["check", "--bogus"]])
def test_console_script_usage_errors_exit_64(argv: list[str]) -> None:
    proc = subprocess.run(
        [str(_console_script()), *argv],
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert proc.returncode == EXIT_USAGE
    assert proc.stderr.startswith("usage: show-your-work")


# --- check verb (plan section 4), always through the production caller main() ----------------

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"
OVER = FIXTURES / "over-budget"
BAD_HEADERS = FIXTURES / "bad-headers"
DRIVE = "C"  # joined with ":/" only at runtime; never a literal absolute path in the repo

SOURCE_BUDGET_CODES = {
    CODE_BUDGET_NODE_LABEL,
    CODE_BUDGET_EDGE_LABEL,
    CODE_BUDGET_SUBGRAPHS,
    CODE_SUBGRAPH_UNBALANCED,
}


def _json(capsys: pytest.CaptureFixture[str]) -> tuple[dict[str, Any], str]:
    captured = capsys.readouterr()
    document: dict[str, Any] = json.loads(captured.out)
    return document, captured.err


def _rows(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["file"]: row for row in document["files"]}


def _codes(row: dict[str, Any]) -> list[str]:
    return [f["code"] for f in row["findings"]]


def _use_renderer(monkeypatch: pytest.MonkeyPatch, fake: FakeRenderer | None) -> None:
    monkeypatch.setattr(renderer, "load", lambda: fake)


def test_check_good_exits_0_with_table_note_and_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["check", str(GOOD)]) == EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["FILE", "NODES", "EDGES", "RESULT"]
    assert lines[1].split() == ["minimal.mmd", "-", "-", "pass"]
    assert COUNTS_NOTE in lines
    assert lines[-1] == (
        "1/1 pass (budget: <=14 nodes, <=20 edges, node label <=60, edge label <=40, <=2 subgraphs)"
    )


def test_check_over_budget_json_names_every_source_budget_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["check", str(OVER), "--json"]) == EXIT_FINDINGS
    document, err = _json(capsys)
    assert list(document) == ["schema", "target", "verb", "files", "summary", "exit_code"]
    assert document["schema"] == REPORT_SCHEMA
    assert document["verb"] == "check"
    assert document["exit_code"] == EXIT_FINDINGS
    assert document["target"] == OVER.resolve().as_posix()
    codes = {f["code"] for row in document["files"] for f in row["findings"]}
    assert codes == SOURCE_BUDGET_CODES
    rows = _rows(document)
    assert [r["file"] for r in document["files"]] == sorted(rows)
    for name in ("nodes-15.mmd", "edges-21.mmd"):
        assert rows[name]["nodes"] is None
        assert rows[name]["edges"] is None
        assert rows[name]["pass"] is True
    for row in document["files"]:
        assert list(row) == ["file", "id", "pass", "nodes", "edges", "findings"]
    assert document["summary"] == {
        "total": 6,
        "passed": 2,
        "failed": 4,
        "rendered": False,
        "budget": {"nodes": 14, "edges": 20, "node_label": 60, "edge_label": 40, "subgraphs": 2},
    }
    assert COUNTS_NOTE in err  # stdout stays pure JSON; the NOTE is never dropped


def test_check_flags_a_multi_line_quoted_node_label_over_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Spec 3.3's quoted node-label pattern crosses newlines; a wrapped long label still fails."""
    half = "w" * (MAX_NODE_LABEL // 2 + 1)
    text = (GOOD / "minimal.mmd").read_text(encoding="utf-8")
    wrapped = text.replace("B[Draft]", f'B["{half}\n{half}"]', 1)
    assert wrapped != text
    (tmp_path / "wrapped.mmd").write_bytes(wrapped.encode("ascii"))
    assert main(["check", str(tmp_path), "--json"]) == EXIT_FINDINGS
    rows = _rows(_json(capsys)[0])
    assert _codes(rows["wrapped.mmd"]) == [CODE_BUDGET_NODE_LABEL]
    assert f"{2 * len(half) + 1} chars" in rows["wrapped.mmd"]["findings"][0]["message"]


def test_check_over_budget_human_shows_dash_counts_and_note(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["check", str(OVER)]) == EXIT_FINDINGS
    out = capsys.readouterr().out
    lines = out.splitlines()
    rows = {ln.split()[0]: ln.split() for ln in lines if ln.endswith(("pass", "FAIL"))}
    assert rows["nodes-15.mmd"] == ["nodes-15.mmd", "-", "-", "pass"]
    assert rows["edges-21.mmd"] == ["edges-21.mmd", "-", "-", "pass"]
    assert COUNTS_NOTE in lines
    for code in SOURCE_BUDGET_CODES:
        assert f"    {code}: " in out
    assert lines[-1].startswith("2/6 pass (budget: ")


def test_check_render_names_node_and_edge_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeRenderer(OVER_COUNTS)
    _use_renderer(monkeypatch, fake)
    assert main(["check", "--render", str(OVER), "--json"]) == EXIT_FINDINGS
    document, err = _json(capsys)
    rows = _rows(document)
    assert _codes(rows["nodes-15.mmd"]) == [CODE_BUDGET_NODES]
    assert _codes(rows["edges-21.mmd"]) == [CODE_BUDGET_EDGES]
    assert (rows["nodes-15.mmd"]["nodes"], rows["edges-21.mmd"]["edges"]) == (15, 21)
    assert document["summary"]["rendered"] is True
    assert COUNTS_NOTE not in err
    assert fake.calls[0] == GARBAGE


def test_check_render_human_table_shows_counts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_renderer(monkeypatch, FakeRenderer())
    assert main(["check", "--render", str(GOOD)]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.splitlines()[1].split() == ["minimal.mmd", "2", "1", "pass"]
    assert COUNTS_NOTE not in out


#: Every committed bad-headers fixture -> its exact finding codes through ``check``. The committed
#: ``absolute-path.mmd`` carries a ``<drive>`` placeholder, so it passes until a tmp copy makes it
#: real (next test); ``alt-after-sources.mmd`` is a valid header (the sticky-state regression).
BAD_HEADER_CODES: dict[str, list[str]] = {
    "absolute-path.mmd": [],
    "alt-after-sources.mmd": [],
    "missing-alt.mmd": [CODE_HEADER_MISSING_ALT],
    "missing-not-shown.mmd": [CODE_HEADER_MISSING_NOT_SHOWN],
    "missing-sources.mmd": [CODE_HEADER_MISSING_SOURCES],
    "missing-summary.mmd": [CODE_HEADER_MISSING_SUMMARY],
    "missing-title.mmd": [CODE_HEADER_MISSING_TITLE],
    "non-ascii.mmd": [CODE_NON_ASCII, CODE_NON_ASCII],  # whole file + the Summary field
    "unknown-label.mmd": [CODE_HEADER_UNKNOWN_LABEL],
}


def test_check_bad_headers_exits_1_naming_every_fixture(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["check", str(BAD_HEADERS), "--json"]) == EXIT_FINDINGS
    document, _ = _json(capsys)
    assert {row["file"]: _codes(row) for row in document["files"]} == BAD_HEADER_CODES
    failing = {name for name, codes in BAD_HEADER_CODES.items() if codes}
    assert {row["file"] for row in document["files"] if not row["pass"]} == failing
    assert document["summary"]["failed"] == len(failing)


def test_check_bad_headers_tmp_copy_fails_absolute_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The committed fixture carries a ``<drive>`` placeholder; a tmp copy makes it real."""
    copy = tmp_path / "bad-headers"
    shutil.copytree(BAD_HEADERS, copy)
    fixture = copy / "absolute-path.mmd"
    fixture.write_bytes(fixture.read_bytes().replace(b"<drive>", DRIVE.encode("ascii")))
    assert main(["check", str(copy), "--json"]) == EXIT_FINDINGS
    rows = _rows(_json(capsys)[0])
    assert _codes(rows["absolute-path.mmd"]) == [CODE_ABS_PATH]


def test_check_empty_dir_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(tmp_path)]) == EXIT_GATE
    assert MSG_NO_SOURCES.format(path=tmp_path.resolve().as_posix()) in capsys.readouterr().err


def test_check_gate_error_json_report_carries_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check", str(tmp_path), "--json"]) == EXIT_GATE
    document, err = _json(capsys)
    assert document["exit_code"] == EXIT_GATE
    assert document["files"] == []
    assert "no *.mmd files" in err


def _spy_on_io(monkeypatch: pytest.MonkeyPatch, root: Path) -> list[str]:
    """Record every OS-level open/stat of a path inside ``root`` and every listing of ``root``.

    Patched at the ``os`` / ``io`` / ``builtins`` layer that every ``pathlib`` read, listing and
    stat goes through, so the proof does not depend on which private helper ``checks`` uses.
    """
    seen: list[str] = []
    base = os.path.normcase(str(root.resolve()))

    def _inside(path: object, include_root: bool) -> bool:
        if isinstance(path, int):  # a file descriptor, not a path
            return False
        try:
            text = os.path.normcase(os.path.abspath(os.fspath(path)))  # type: ignore[call-overload]
        except TypeError:
            return False
        return text.startswith(base + os.sep) or (include_root and text == base)

    def _wrap(module: object, name: str, include_root: bool) -> None:
        original = getattr(module, name)

        def spy(*args: Any, **kwargs: Any) -> Any:
            path = args[0] if args else kwargs.get("path", kwargs.get("file", "."))
            if _inside(path, include_root):
                seen.append(f"{name}({path})")
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, spy)

    for module, name in ((builtins, "open"), (io, "open"), (os, "open"), (os, "stat")):
        _wrap(module, name, include_root=False)
    for name in ("scandir", "listdir"):
        _wrap(os, name, include_root=True)
    return seen


def test_io_spy_can_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard is not vacuous: a plain check run is seen listing TARGET and opening the file."""
    shutil.copy(GOOD / "minimal.mmd", tmp_path / "minimal.mmd")
    seen = _spy_on_io(monkeypatch, tmp_path)
    assert main(["check", str(tmp_path), "--json"]) == EXIT_OK
    assert any(call.startswith(("scandir(", "listdir(")) for call in seen)
    assert any(call.startswith("open(") and "minimal.mmd" in call for call in seen)


@pytest.mark.parametrize("accept_garbage", [True, False], ids=["garbage-accepted", "good-fails"])
def test_check_render_broken_anchor_exits_2_before_touching_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    accept_garbage: bool,
) -> None:
    shutil.copy(GOOD / "minimal.mmd", tmp_path / "minimal.mmd")
    if accept_garbage:
        fake = FakeRenderer(accept_garbage=True)
    else:  # garbage raises, but so does the known-good diagram
        fake = FakeRenderer(fail_titles=frozenset({"flowchart LR"}))
    _use_renderer(monkeypatch, fake)
    seen = _spy_on_io(monkeypatch, tmp_path)
    assert main(["check", "--render", str(tmp_path), "--json"]) == EXIT_GATE
    document, err = _json(capsys)
    assert seen == []
    assert MSG_ANCHOR_BROKEN in err
    assert (document["exit_code"], document["files"]) == (EXIT_GATE, [])
    assert fake.calls == ([GARBAGE] if accept_garbage else [GARBAGE, KNOWN_GOOD])


def test_check_render_without_mermaidx_exits_2_with_documented_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "mermaidx", None)  # the real load() then finds no renderer
    assert main(["check", "--render", str(GOOD)]) == EXIT_GATE
    err = capsys.readouterr().err
    assert err.strip() == f"show-your-work check: {MSG_RENDERER_MISSING}"
    assert main(["check", "--render", str(GOOD), "--json"]) == EXIT_GATE
    document, err = _json(capsys)
    assert (document["exit_code"], document["summary"]["rendered"]) == (EXIT_GATE, True)
    assert MSG_RENDERER_MISSING in err


def test_check_render_with_broken_mermaidx_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _broken() -> None:
        raise renderer.RendererLoadError("OSError: native library missing")

    monkeypatch.setattr(renderer, "load", _broken)
    assert main(["check", "--render", str(GOOD)]) == EXIT_GATE
    err = capsys.readouterr().err
    assert MSG_RENDERER_BROKEN.format(reason="OSError: native library missing") in err


def test_check_unreadable_source_exits_2_with_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copy(GOOD / "minimal.mmd", tmp_path / "minimal.mmd")
    real_open = Path.open

    def _locked(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "minimal.mmd":
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _locked)
    assert main(["check", str(tmp_path), "--json"]) == EXIT_GATE
    document, err = _json(capsys)
    assert (document["exit_code"], document["files"]) == (EXIT_GATE, [])
    assert "cannot read" in err
    assert "minimal.mmd: Permission denied" in err


def test_check_unlistable_target_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _denied(self: Path) -> Any:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "iterdir", _denied)
    assert main(["check", str(tmp_path)]) == EXIT_GATE
    err = capsys.readouterr().err
    assert f"cannot read {tmp_path.resolve().as_posix()}: Permission denied" in err


def test_check_oversized_source_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "huge.mmd").write_bytes(b"%" * (MAX_SOURCE_BYTES + 1))
    assert main(["check", str(tmp_path)]) == EXIT_GATE
    assert f"larger than {MAX_SOURCE_BYTES} bytes" in capsys.readouterr().err


def test_check_excalidraw_orphan_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shutil.copy(GOOD / "minimal.mmd", tmp_path / "minimal.mmd")
    (tmp_path / "minimal.excalidraw.svg").write_bytes(b"<svg/>")
    (tmp_path / "ghost.excalidraw.svg").write_bytes(b"<svg/>")
    assert main(["check", str(tmp_path), "--json"]) == EXIT_FINDINGS
    rows = _rows(_json(capsys)[0])
    assert set(rows) == {"minimal.mmd", "ghost.excalidraw.svg"}
    assert rows["minimal.mmd"]["pass"] is True
    assert rows["ghost.excalidraw.svg"]["id"] == "ghost"
    assert _codes(rows["ghost.excalidraw.svg"]) == [CODE_EXCALIDRAW_ORPHAN]


def test_check_default_target_missing_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["check"]) == EXIT_GATE
    expected = MSG_NO_DEFAULT_TARGET.format(
        path=(tmp_path / "docs" / "workflows").resolve().as_posix()
    )
    assert expected in capsys.readouterr().err


def test_check_default_target_is_cwd_docs_workflows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workflows = tmp_path / "docs" / "workflows"
    workflows.mkdir(parents=True)
    shutil.copy(GOOD / "minimal.mmd", workflows / "minimal.mmd")
    monkeypatch.chdir(tmp_path)
    assert main(["check", "--json"]) == EXIT_OK
    assert _json(capsys)[0]["target"] == workflows.resolve().as_posix()


def test_check_explicit_missing_target_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check", str(tmp_path / "absent")]) == EXIT_GATE
    assert "not a directory" in capsys.readouterr().err


def test_console_script_check_good_exits_0() -> None:
    proc = subprocess.run(
        [str(_console_script()), "check", str(GOOD)],
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert proc.returncode == EXIT_OK, proc.stderr
    assert "1/1 pass" in proc.stdout


def test_check_human_output_escapes_non_ascii(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = (GOOD / "minimal.mmd").read_text(encoding="utf-8")
    stray = text.replace("%% Summary:", "%% caf\u00e9 stray line\n%% Summary:", 1)
    (tmp_path / "accent.mmd").write_bytes(stray.encode("utf-8"))
    assert main(["check", str(tmp_path)]) == EXIT_FINDINGS
    out = capsys.readouterr().out
    assert out.isascii()
    assert r"caf\xe9" in out


ESC = chr(0x1B)


def test_check_human_output_escapes_control_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A renderer message echoing hostile source cannot inject terminal escapes."""
    title = f"Red {ESC}[31m Alert"
    text = (GOOD / "minimal.mmd").read_text(encoding="utf-8")
    hostile = text.replace("Minimal Example", title, 1)
    (tmp_path / "hostile.mmd").write_bytes(hostile.encode("ascii"))
    _use_renderer(monkeypatch, FakeRenderer(fail_titles=frozenset({title})))
    assert main(["check", "--render", str(tmp_path)]) == EXIT_FINDINGS
    out = capsys.readouterr().out
    assert CODE_RENDER_ERROR in out
    assert ESC not in out
    assert r"Red \x1b[31m Alert" in out


def test_format_table_escapes_control_characters_in_names_and_messages() -> None:
    name = f"bad{ESC}name.mmd"
    finding = Finding(name, CODE_RENDER_ERROR, f"line one{chr(13)}over{chr(0x7F)}write")
    report = Report(Path("."), (FileResult(name, "bad", None, None, (finding,)),), rendered=False)
    table = format_table(report)
    assert all(ch == "\n" or 0x20 <= ord(ch) < 0x7F for ch in table)
    assert r"bad\x1bname.mmd" in table
    assert r"line one\x0dover\x7fwrite" in table
