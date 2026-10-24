"""Read-only live test against the coding root's ``docs/workflows/`` (plan Step 5, P6, M1).

The coding root is a different, PRIVATE git repository: nothing here writes to it, and none of
its content is committed to this public repo. When its ``docs/workflows/*.mmd`` set is present,
the files are copied to a tmp dir and ``check`` / ``render`` run on the copy through ``cli.main``.
The test branches on the live state:

* before M1 (any live diagram lacks an ``Alt:`` line): ``check`` exits 1 with
  ``header-missing-alt`` as the ONLY finding code, and ``render`` exits 1 writing nothing;
* after M1: both exit 0.

Locating the coding root: a build-step worktree is a SIBLING of the main checkout, not a child of
the coding root, so the root is never derived from the working directory or from this file's
path. Git is asked for the common git dir (shared by every worktree), whose parent is the main
checkout and whose grandparent is the coding root. The git subprocess merely needs to start
somewhere inside this repository; this file's directory is used for that, not for the answer.
When git, the layout or the live set is absent (an sdist, a CI clone), the tests skip with a
message saying why.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from show_your_work.cli import main
from show_your_work.constants import (
    CODE_HEADER_MISSING_ALT,
    EXIT_FINDINGS,
    EXIT_OK,
    GIT_MARKER,
    INDEX_NAME,
    INTRO_NAME,
    README_NAME,
)

#: Committed fixtures the privacy guard compares against the live set.
FIXTURES = Path(__file__).parent / "fixtures"
#: Bound on the git subprocess (it is a local, read-only query).
GIT_TIMEOUT_SECONDS = 30
ALT_LABEL = "alt:"


def _coding_root() -> Path | None:
    """The coding root (grandparent of the shared ``.git``), or ``None`` when not resolvable."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            encoding="utf-8",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    common_dir = Path(completed.stdout.strip())
    if common_dir.name != GIT_MARKER:  # a bare repo or unusual layout: no main checkout
        return None
    main_checkout = common_dir.parent
    return main_checkout.parent


def _live_sources() -> list[Path]:
    """The live ``*.mmd`` files, or skip the calling test with the reason."""
    root = _coding_root()
    if root is None:
        pytest.skip("live coding-root test: cannot locate the main checkout via git")
    live = root / "docs" / "workflows"
    sources = sorted(live.glob("*.mmd")) if live.is_dir() else []
    if not sources:
        pytest.skip("live coding-root test: no *.mmd files under <coding-root>/docs/workflows")
    return sources


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _header_contents(text: str) -> list[str]:
    """Text after ``%%`` on each non-empty line of the leading comment block (LF input)."""
    contents: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("%%"):
            break
        contents.append(stripped[2:].strip())
    return contents


def _has_alt(raw: bytes) -> bool:
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
    return any(c.casefold().startswith(ALT_LABEL) for c in _header_contents(text))


def _without_alt_lf(raw: bytes) -> str:
    """LF-normalised text with every ``%% Alt:`` header line removed (privacy comparison form)."""
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
    kept = [
        line
        for line in text.split("\n")
        if not (
            line.strip().startswith("%%")
            and line.strip()[2:].strip().casefold().startswith(ALT_LABEL)
        )
    ]
    return "\n".join(kept)


def test_live_coding_root_check_and_render(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sources = _live_sources()
    live_dir = sources[0].parent
    before = {p.name: p.read_bytes() for p in sources}

    target = tmp_path / "workflows"
    target.mkdir()
    for path in sources:
        (target / path.name).write_bytes(before[path.name])
    intro = live_dir / INTRO_NAME
    if intro.is_file():
        (target / INTRO_NAME).write_bytes(intro.read_bytes())

    pre_m1 = not all(_has_alt(raw) for raw in before.values())
    with capsys.disabled():
        print(
            f"\nlive coding-root test: {len(sources)} diagram(s) discovered; "
            f"state={'pre-M1' if pre_m1 else 'post-M1'}"
        )

    check_code = main(["check", str(target), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["total"] == len(sources)
    codes = {f["code"] for file in report["files"] for f in file["findings"]}

    if pre_m1:
        assert check_code == EXIT_FINDINGS
        assert codes == {CODE_HEADER_MISSING_ALT}
        lacking = {name for name, raw in before.items() if not _has_alt(raw)}
        failing = {file["file"] for file in report["files"] if not file["pass"]}
        assert failing == lacking
        staged = _snapshot(target)
        assert main(["render", str(target)]) == EXIT_FINDINGS
        assert _snapshot(target) == staged, "a refused render must write nothing"
        assert not (target / README_NAME).exists()
        assert not (target / INDEX_NAME).exists()
    else:
        assert check_code == EXIT_OK, codes
        assert codes == set()
        assert main(["render", str(target)]) == EXIT_OK
        assert (target / README_NAME).is_file()
        assert (target / INDEX_NAME).is_file()

    # Read-only: the live files are byte-identical afterwards.
    assert {p.name: p.read_bytes() for p in sources} == before


def test_no_fixture_is_a_copy_of_a_live_diagram() -> None:
    """Privacy guard (P6): no committed fixture equals a live diagram, Alt lines aside."""
    sources = _live_sources()
    live = [_without_alt_lf(p.read_bytes()) for p in sources]
    live_titles = {contents[0] for text in live if (contents := _header_contents(text))}
    fixtures = [p for p in sorted(FIXTURES.rglob("*")) if p.is_file()]
    assert fixtures, "no committed fixtures found"
    for path in fixtures:
        normalised = _without_alt_lf(path.read_bytes())
        assert normalised not in live, f"{path.relative_to(FIXTURES)} copies a live diagram"
        fixture_title = _header_contents(normalised)[:1]
        assert not live_titles.intersection(fixture_title), (
            f"{path.relative_to(FIXTURES)} reuses a live diagram title"
        )
