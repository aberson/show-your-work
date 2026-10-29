"""``init`` verb (plan sections 3.3, 3.6, 4 and design decision DD6): scaffold, never overwrite.

Behavioural tests drive the production caller ``cli.main`` against tmp directories. The template
tests read the repo's ``templates/workflows/`` directly, and one test builds the real wheel and runs
``init`` from the unpacked package (no source checkout on ``sys.path``), proving the templates
and the bundled house-raster fonts and their licence (plan Phase 2 Step 10) ship as package data
rather than only working from a checkout.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from show_your_work import constants, scaffold
from show_your_work.cli import main
from show_your_work.constants import (
    EXAMPLE_NAME,
    EXIT_FINDINGS,
    EXIT_GATE,
    EXIT_OK,
    INDEX_NAME,
    INIT_CREATED,
    INIT_KEPT,
    INIT_REPLACED,
    INTRO_NAME,
    MAX_EDGE_LABEL,
    MAX_EDGES,
    MAX_NODE_LABEL,
    MAX_NODES,
    MAX_SUBGRAPHS,
    MAX_TEMPLATE_BYTES,
    README_NAME,
    STATE_CURRENT,
    TEMPLATE_MARKER_MD,
    TEMPLATE_MARKER_MMD,
)
from show_your_work.render import write_atomic
from show_your_work.scaffold import is_template_origin, load_templates

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "templates" / "workflows"
DOGFOOD = REPO / "docs" / "workflows"
#: The bundled house-raster font file names (plan Phase 2 section 2.6, Step 10), regular then bold.
FONT_FILES_ANCHOR = ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf")  # anchor: plan section 2.6

USER_DIAGRAM = (
    "%% My Own Flow\n"
    "%% Summary: A diagram the project wrote itself.\n"
    "%% Sources:\n"
    "%%   - docs/flow.md\n"
    "%% Not shown: everything else\n"
    "%% Alt: Two boxes joined by one arrow pointing right.\n"
    "\n"
    "flowchart LR\n"
    "    A[Start] --> B[Finish]\n"
)


def _snapshot(directory: Path) -> dict[str, tuple[bytes, int]]:
    """Every file's bytes and mtime (a rewrite with identical bytes still moves the mtime)."""
    return {
        p.relative_to(directory).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _template(name: str) -> bytes:
    return (TEMPLATES / name).read_bytes().replace(b"\r\n", b"\n")


# --- the templates themselves ----------------------------------------------------------------


def test_template_files_are_the_two_shipped_templates() -> None:
    assert list(constants.TEMPLATE_FILES) == [INTRO_NAME, EXAMPLE_NAME]
    assert sorted(p.name for p in TEMPLATES.iterdir()) == sorted(constants.TEMPLATE_FILES)


@pytest.mark.parametrize("name", [INTRO_NAME, EXAMPLE_NAME])
def test_templates_are_ascii_lf_and_end_with_their_marker(name: str) -> None:
    raw = (TEMPLATES / name).read_bytes()
    assert raw.isascii()
    assert b"\r" not in raw, ".gitattributes must keep templates LF"
    assert raw.endswith(b"\n")
    last = raw.decode("ascii").rstrip("\n").rsplit("\n", 1)[-1]
    assert last == constants.TEMPLATE_FILES[name]
    assert is_template_origin(TEMPLATES / name, constants.TEMPLATE_FILES[name])


def test_example_template_alone_passes_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copy(TEMPLATES / EXAMPLE_NAME, tmp_path / EXAMPLE_NAME)
    assert main(["check", str(tmp_path), "--json"]) == EXIT_OK
    assert '"passed": 1' in capsys.readouterr().out


def test_example_template_carries_all_five_header_fields() -> None:
    from show_your_work.header import parse_header

    header = parse_header(_template(EXAMPLE_NAME).decode("ascii"))
    assert header.title and header.summary and header.not_shown and header.alt
    assert len(header.sources) >= 1


def test_intro_template_states_the_current_budget() -> None:
    """The prose budget cannot drift from ``constants`` unnoticed."""
    text = " ".join(_template(INTRO_NAME).decode("ascii").split())
    for phrase in (
        f"{MAX_NODES} nodes",
        f"{MAX_EDGES} edges",
        f"{MAX_SUBGRAPHS} subgraphs",
        f"node labels of {MAX_NODE_LABEL} characters",
        f"edge labels of {MAX_EDGE_LABEL} characters",
    ):
        assert phrase in text


def test_example_template_passes_a_real_render_when_mermaidx_is_installed(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mermaidx", reason="mermaidx (extra 'render') not installed")
    shutil.copy(TEMPLATES / EXAMPLE_NAME, tmp_path / EXAMPLE_NAME)
    assert main(["check", "--render", str(tmp_path)]) == EXIT_OK


# --- init through the production caller ------------------------------------------------------


def test_init_on_missing_dir_scaffolds_and_leaves_check_and_render_check_green(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "proj" / "docs" / "workflows"
    assert main(["init", str(target)]) == EXIT_OK
    out = capsys.readouterr().out
    assert f"{INTRO_NAME}: {INIT_CREATED}" in out
    assert f"{EXAMPLE_NAME}: {INIT_CREATED}" in out
    assert "1/1 pass" in out
    assert (target / INTRO_NAME).read_bytes() == _template(INTRO_NAME)
    assert (target / EXAMPLE_NAME).read_bytes() == _template(EXAMPLE_NAME)
    assert (target / README_NAME).is_file()
    assert (target / INDEX_NAME).is_file()
    assert main(["check", str(target)]) == EXIT_OK
    assert main(["render", str(target), "--check"]) == EXIT_OK


def test_second_init_changes_no_bytes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init", str(tmp_path)]) == EXIT_OK
    before = _snapshot(tmp_path)
    capsys.readouterr()
    assert main(["init", str(tmp_path)]) == EXIT_OK
    out = capsys.readouterr().out
    assert _snapshot(tmp_path) == before
    assert f"{INTRO_NAME}: {INIT_KEPT}" in out
    assert f"{README_NAME}: {STATE_CURRENT}" in out
    assert f"{INDEX_NAME}: {STATE_CURRENT}" in out


def test_second_init_with_force_on_unchanged_templates_changes_no_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["init", str(tmp_path)]) == EXIT_OK
    before = _snapshot(tmp_path)
    capsys.readouterr()
    assert main(["init", str(tmp_path), "--force"]) == EXIT_OK
    assert _snapshot(tmp_path) == before
    assert f"{EXAMPLE_NAME}: {STATE_CURRENT}" in capsys.readouterr().out


@pytest.mark.parametrize("force", [False, True])
def test_init_never_touches_a_user_intro(tmp_path: Path, force: bool) -> None:
    user_intro = b"# Our project\n\nWritten by us; no template marker here.\n"
    (tmp_path / INTRO_NAME).write_bytes(user_intro)
    argv = ["init", str(tmp_path), *(["--force"] if force else [])]
    assert main(argv) == EXIT_OK
    assert (tmp_path / INTRO_NAME).read_bytes() == user_intro
    assert (tmp_path / EXAMPLE_NAME).read_bytes() == _template(EXAMPLE_NAME)
    assert b"Written by us" in (tmp_path / README_NAME).read_bytes()


def test_force_rewrites_template_origin_and_leaves_user_mmd_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["init", str(tmp_path)]) == EXIT_OK
    # A template-origin intro the user tweaked but whose marker line is still last (CRLF on disk).
    stale_intro = b"Old template prose.\r\n" + TEMPLATE_MARKER_MD.encode("ascii") + b"\r\n"
    (tmp_path / INTRO_NAME).write_bytes(stale_intro)
    # The user replaced the example and removed the marker: it is theirs now.
    (tmp_path / EXAMPLE_NAME).write_bytes(USER_DIAGRAM.encode("ascii"))
    other = tmp_path / "my-flow.mmd"
    other.write_bytes(USER_DIAGRAM.replace("My Own Flow", "Another Flow").encode("ascii"))
    other_bytes = other.read_bytes()
    assert main(["init", str(tmp_path)]) == EXIT_OK
    assert (tmp_path / INTRO_NAME).read_bytes() == stale_intro  # no --force: kept
    capsys.readouterr()

    assert main(["init", str(tmp_path), "--force"]) == EXIT_OK
    out = capsys.readouterr().out
    assert f"{INTRO_NAME}: {INIT_REPLACED}" in out
    assert f"{EXAMPLE_NAME}: {INIT_KEPT}" in out
    assert (tmp_path / INTRO_NAME).read_bytes() == _template(INTRO_NAME)
    assert (tmp_path / EXAMPLE_NAME).read_bytes() == USER_DIAGRAM.encode("ascii")
    assert other.read_bytes() == other_bytes
    assert main(["render", str(tmp_path), "--check"]) == EXIT_OK


def test_force_rewrites_a_template_origin_mmd(tmp_path: Path) -> None:
    edited = _template(EXAMPLE_NAME).replace(b"[Merge]", b"[Ship it]")
    (tmp_path / EXAMPLE_NAME).write_bytes(edited)
    assert main(["init", str(tmp_path)]) == EXIT_OK
    assert (tmp_path / EXAMPLE_NAME).read_bytes() == edited  # no --force: kept
    assert main(["init", str(tmp_path), "--force"]) == EXIT_OK
    assert (tmp_path / EXAMPLE_NAME).read_bytes() == _template(EXAMPLE_NAME)


def test_marker_must_be_the_last_line(tmp_path: Path) -> None:
    path = tmp_path / EXAMPLE_NAME
    path.write_bytes(_template(EXAMPLE_NAME) + b"    X[Added below the marker]\n")
    assert not is_template_origin(path, TEMPLATE_MARKER_MMD)
    path.write_bytes(_template(EXAMPLE_NAME).replace(b"\n", b"\r\n"))
    assert is_template_origin(path, TEMPLATE_MARKER_MMD)  # CRLF is normalized
    assert not is_template_origin(path, TEMPLATE_MARKER_MD)  # the wrong file type's marker
    path.write_bytes(_template(EXAMPLE_NAME).removesuffix(b"\n"))
    assert is_template_origin(path, TEMPLATE_MARKER_MMD)  # no final line break
    path.write_bytes(_template(EXAMPLE_NAME) + b"\n")
    assert not is_template_origin(path, TEMPLATE_MARKER_MMD)  # a trailing blank line follows
    path.write_bytes(_template(EXAMPLE_NAME).replace(b"template\n", b"template \n"))
    assert not is_template_origin(path, TEMPLATE_MARKER_MMD)  # trailing space: not exact
    assert not is_template_origin(tmp_path / "absent.mmd", TEMPLATE_MARKER_MMD)
    assert not is_template_origin(tmp_path, TEMPLATE_MARKER_MMD)  # a directory


def test_oversized_file_is_never_template_origin(tmp_path: Path) -> None:
    path = tmp_path / INTRO_NAME
    path.write_bytes(b"x" * MAX_TEMPLATE_BYTES + b"\n" + TEMPLATE_MARKER_MD.encode("ascii"))
    assert not is_template_origin(path, TEMPLATE_MARKER_MD)


def test_force_never_follows_a_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside_bytes = b"Elsewhere.\n" + TEMPLATE_MARKER_MD.encode("ascii") + b"\n"
    outside.write_bytes(outside_bytes)
    target = tmp_path / "wf"
    target.mkdir()
    try:
        (target / INTRO_NAME).symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available to this user")
    assert main(["init", str(target), "--force"]) == EXIT_OK
    assert outside.read_bytes() == outside_bytes
    assert (target / INTRO_NAME).is_symlink()


def test_init_target_is_a_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "workflows"
    target.write_bytes(b"not a directory\n")
    assert main(["init", str(target)]) == EXIT_GATE
    assert "target is not a directory" in capsys.readouterr().err
    assert target.read_bytes() == b"not a directory\n"


def test_init_parent_is_a_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    blocker = tmp_path / "docs"
    blocker.write_bytes(b"x")
    assert main(["init", str(blocker / "workflows")]) == EXIT_GATE
    assert "cannot create" in capsys.readouterr().err


def test_init_default_target_is_created_under_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == EXIT_OK
    target = tmp_path / "docs" / "workflows"
    assert (target / EXAMPLE_NAME).is_file()
    assert main(["render", "--check"]) == EXIT_OK


def test_init_with_a_failing_user_diagram_exits_1_and_renders_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "broken.mmd").write_bytes(b"%% Only a title\n\nflowchart LR\n    A --> B\n")
    assert main(["init", str(tmp_path)]) == EXIT_FINDINGS
    captured = capsys.readouterr()
    assert "1 of 2 file(s)" in captured.err
    assert (tmp_path / EXAMPLE_NAME).is_file()  # gaps are still filled
    assert not (tmp_path / README_NAME).exists()
    assert not (tmp_path / INDEX_NAME).exists()


def test_init_with_missing_templates_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(scaffold, "TEMPLATE_PACKAGE_PARTS", ("no-such", "templates"))
    target = tmp_path / "wf"
    assert main(["init", str(target)]) == EXIT_GATE
    assert "init templates not found" in capsys.readouterr().err
    assert not target.exists()


def test_load_templates_normalizes_crlf(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    tdir = root / "templates" / "workflows"
    tdir.mkdir(parents=True)
    (root / "pyproject.toml").write_bytes(b"")
    for name in constants.TEMPLATE_FILES:
        (tdir / name).write_bytes(_template(name).replace(b"\n", b"\r\n"))
    monkeypatch.setattr(scaffold, "_checkout_template_dir", lambda: tdir)
    monkeypatch.setattr(scaffold, "TEMPLATE_PACKAGE_PARTS", ("no-such", "templates"))
    loaded = load_templates()
    assert loaded == {name: _template(name) for name in constants.TEMPLATE_FILES}


def test_init_with_undecodable_template_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tdir = tmp_path / "templates"
    tdir.mkdir()
    for name in constants.TEMPLATE_FILES:
        (tdir / name).write_bytes(b"\xff\xfe not utf-8\n")
    monkeypatch.setattr(scaffold, "_checkout_template_dir", lambda: tdir)
    monkeypatch.setattr(scaffold, "TEMPLATE_PACKAGE_PARTS", ("no-such", "templates"))
    target = tmp_path / "wf"
    assert main(["init", str(target)]) == EXIT_GATE
    assert "init templates not found" in capsys.readouterr().err
    assert not target.exists()


def test_write_atomic_applies_the_requested_mode(tmp_path: Path) -> None:
    """``--force`` passes the old file's mode; without it the file would be mkstemp's default."""
    path = tmp_path / "f.md"
    path.write_bytes(b"old\n")
    read_only = stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH
    try:
        write_atomic(path, "new\n", mode=read_only)
        assert path.read_bytes() == b"new\n"
        assert stat.S_IMODE(path.stat().st_mode) & stat.S_IWRITE == 0
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


# --- this repo's own dogfooded docs/workflows -------------------------------------------------


def test_dogfooded_docs_workflows_is_current() -> None:
    assert main(["check", str(DOGFOOD)]) == EXIT_OK
    assert main(["render", str(DOGFOOD), "--check"]) == EXIT_OK


@pytest.mark.parametrize("name", [INTRO_NAME, EXAMPLE_NAME])
def test_dogfooded_files_are_the_templates(name: str) -> None:
    assert (DOGFOOD / name).read_bytes().replace(b"\r\n", b"\n") == _template(name)


# --- the installed package (wheel), not the source checkout ----------------------------------


_RUN_FROM_WHEEL = """
import sys
sys.path.insert(0, sys.argv[1])
import show_your_work.scaffold as s
from pathlib import Path
assert Path(s.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve()), s.__file__
assert s._checkout_template_dir() is None, "the source-checkout fallback must not be reachable"
import show_your_work.renderer as r
for font in r.house_fonts():
    assert Path(str(font)).resolve().is_relative_to(Path(sys.argv[1]).resolve()), font
    assert font.is_file(), font
from show_your_work.cli import main
code = main(["init", sys.argv[2]])
if code == 0:
    code = main(["render", sys.argv[2], "--check"])
sys.exit(code)
"""

#: uv's offline failure when the hatchling build backend is not in its cache (uv 0.10). Matched
#: on whitespace-collapsed stderr; only this case skips, every other build failure fails.
_OFFLINE_BACKEND_MARKERS = (
    "Failed to resolve requirements from `build-system.requires`",
    "network was disabled",
)


def _offline_backend_unavailable(stderr: str) -> bool:
    text = " ".join(stderr.split())
    return all(marker in text for marker in _OFFLINE_BACKEND_MARKERS)


def test_offline_backend_classifier_skips_only_the_cold_cache_case() -> None:
    cold_cache = (
        "  x Failed to build `show-your-work`\n"
        "  |-> Failed to resolve requirements from `build-system.requires`\n"
        "  |-> No solution found when resolving: `hatchling`\n"
        "      hint: Packages were unavailable because the network was disabled. When\n"
        "      the network is disabled, registry packages may only be read from the\n"
        "      cache.\n"
    )
    assert _offline_backend_unavailable(cold_cache)
    real_error = (
        "  x Failed to build `show-your-work`\n"
        "  |-> The build backend returned an error\n"
        "  `-> Forced include not found: templates/workflows\n"
    )
    assert not _offline_backend_unavailable(real_error)
    assert not _offline_backend_unavailable("")


def _build_wheel_offline(uv: str, out_dir: Path) -> Path:
    env = {**os.environ, "NO_COLOR": "1"}
    proc = subprocess.run(
        [uv, "build", "--wheel", "--offline", "--quiet", "--out-dir", str(out_dir), str(REPO)],
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=180,
        env=env,
    )
    if proc.returncode != 0 and _offline_backend_unavailable(proc.stderr):
        pytest.skip("hatchling build backend is not in the uv cache and the build runs offline")
    assert proc.returncode == 0, proc.stderr
    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def test_init_works_from_the_built_wheel(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH; cannot build the wheel")
    wheel = _build_wheel_offline(uv, tmp_path / "dist")
    unpacked = tmp_path / "site"
    with zipfile.ZipFile(wheel) as archive:
        for name in constants.TEMPLATE_FILES:
            member = "/".join([constants.PACKAGE, *constants.TEMPLATE_PACKAGE_PARTS, name])
            assert archive.read(member) == _template(name)
        assert tuple(constants.HOUSE_FONT_SHA256) == FONT_FILES_ANCHOR
        pinned = {
            **constants.HOUSE_FONT_SHA256,
            constants.HOUSE_FONT_LICENSE: constants.HOUSE_FONT_LICENSE_SHA256,
        }
        for name, digest in pinned.items():
            member = "/".join([constants.PACKAGE, *constants.HOUSE_FONT_PACKAGE_PARTS, name])
            data = archive.read(member)
            assert hashlib.sha256(data).hexdigest() == digest
        archive.extractall(unpacked)

    target = tmp_path / "consumer" / "docs" / "workflows"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-I", "-c", _RUN_FROM_WHEEL, str(unpacked), str(target)],
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=120,
        env=env,
        cwd=tmp_path,
    )
    assert proc.returncode == EXIT_OK, proc.stdout + proc.stderr
    for name in constants.TEMPLATE_FILES:
        assert (target / name).read_bytes() == _template(name)
