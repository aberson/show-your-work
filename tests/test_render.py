"""``render`` verb (plan sections 3.1, 3.4, 4): generated README + ``workflows.json`` + ``--check``.

Every behavioural test drives the production caller ``cli.main`` against a tmp copy of a fixture
(never the committed fixture folders). The ``workflows.json`` schema test asserts the section 3.4
key sets against LITERAL lists, not the constants, so a drifted constant fails here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from show_your_work import __version__, render, renderer
from show_your_work.cli import main
from show_your_work.constants import (
    EXIT_FINDINGS,
    EXIT_GATE,
    EXIT_OK,
    GENERATED_MARKER,
    HOUSE_SVG_MARKER,
    INDEX_ITEM_KEYS,
    INDEX_KEYS,
    INDEX_SCHEMA,
    MSG_NO_DEFAULT_TARGET,
    RASTER_THEMES,
    REPORT_SCHEMA,
)
from show_your_work.render import build_index, build_readme, github_anchor, index_up_to_date
from tests.fakes import FakeRenderer, junction, layout_project, snapshot

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"
LAYOUT = FIXTURES / "layout"
LAYOUT_VALID = LAYOUT / "valid"
PHASE1 = LAYOUT / "phase1"
GOLDEN = LAYOUT / "golden"
#: The ``generated_at`` the ``phase1/render-*`` goldens were recorded with (Phase 1 code).
PHASE1_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

EXPECTED_TOP_KEYS = ["schema", "generator", "generated_at", "source_dir", "items"]
EXPECTED_ITEM_KEYS = [
    "id",
    "label",
    "summary",
    "alt",
    "not_shown",
    "sources",
    "source_path",
    "source_sha256",
    "engine",
    "rasters",
]
EXPECTED_RASTER_KEYS = ["path", "sha256", "width", "height", "bytes"]

COLON_TITLE = "Release Checklist: One Gated Ship"


def _diagram(title: str, *, summary: str = "A short summary of the flow.") -> str:
    return (
        f"%% {title}\n"
        f"%% Summary: {summary}\n"
        "%% Sources:\n"
        "%%   - docs/flow.md\n"
        "%%   - https://example.com/spec\n"
        "%% Not shown: retries, error paths\n"
        "%% Alt: Two boxes joined by one arrow pointing right.\n"
        "\n"
        "flowchart LR\n"
        "    A[Start] --> B[Finish]\n"
    )


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def _copy_good(dest: Path) -> Path:
    shutil.copytree(GOOD, dest)
    return dest


def _index(target: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((target / "workflows.json").read_text(encoding="utf-8"))
    return document


def _has_git_ancestor(path: Path) -> bool:
    resolved = path.resolve()
    return any((p / ".git").exists() for p in (resolved, *resolved.parents))


# --- writing and determinism --------------------------------------------------------------


def test_render_good_writes_readme_and_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _copy_good(tmp_path / "good")
    assert main(["render", str(target)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "README.md: written" in out
    assert "workflows.json: written" in out
    readme = (target / "README.md").read_bytes()
    index = (target / "workflows.json").read_bytes()
    assert readme.startswith(f"<!-- {GENERATED_MARKER}".encode())
    assert b"\r" not in readme and b"\r" not in index
    assert index.isascii()
    assert [item["id"] for item in _index(target)["items"]] == ["minimal"]


def test_second_render_is_byte_identical_except_generated_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _copy_good(tmp_path / "good")
    first = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    monkeypatch.setattr(render, "_utc_now", lambda: first)
    assert main(["render", str(target)]) == EXIT_OK
    readme1 = (target / "README.md").read_bytes()
    index1 = _index(target)

    later = first + timedelta(days=3)
    monkeypatch.setattr(render, "_utc_now", lambda: later)
    assert main(["render", str(target)]) == EXIT_OK
    assert "README.md: up to date" in capsys.readouterr().out
    assert (target / "README.md").read_bytes() == readme1
    index2 = _index(target)
    # Unchanged sources: the file is left untouched (no churn), so even the stamp is stable.
    assert index2 == index1

    # A content change rewrites the index; only content keys and generated_at move.
    src = target / "minimal.mmd"
    src.write_bytes(src.read_bytes() + b"    C --> D[Ship]\n")
    assert main(["render", str(target)]) == EXIT_OK
    index3 = _index(target)
    assert index3["generated_at"] == later.isoformat(timespec="seconds")
    assert index3["generated_at"] != index1["generated_at"]
    masked1 = {**index1, "generated_at": None, "items": None}
    masked3 = {**index3, "generated_at": None, "items": None}
    assert masked1 == masked3


def test_render_check_exits_0_after_render_and_1_after_editing_a_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _copy_good(tmp_path / "good")
    assert main(["render", str(target), "--check"]) == EXIT_FINDINGS  # nothing generated yet
    assert not (target / "README.md").exists()
    assert not (target / "workflows.json").exists()

    assert main(["render", str(target)]) == EXIT_OK
    assert main(["render", str(target), "--check"]) == EXIT_OK
    capsys.readouterr()

    src = target / "minimal.mmd"
    src.write_bytes(src.read_bytes() + b"    C --> D[Ship]\n")
    before = snapshot(target)
    assert main(["render", str(target), "--check"]) == EXIT_FINDINGS
    captured = capsys.readouterr()
    assert "README.md: stale" in captured.out
    assert "workflows.json: stale" in captured.out
    assert f"README.md is out of date; run 'show-your-work render {target}'" in captured.err
    assert snapshot(target) == before  # --check writes nothing


def test_render_check_detects_intro_edit(tmp_path: Path) -> None:
    target = _copy_good(tmp_path / "good")
    assert main(["render", str(target)]) == EXIT_OK
    with (target / "intro.md").open("ab") as handle:
        handle.write(b"\nOne more line.\n")
    assert main(["render", str(target), "--check"]) == EXIT_FINDINGS


# --- workflows.json schema (section 3.4) ---------------------------------------------------


def test_workflows_json_schema(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    target = root / "docs" / "workflows"
    target.mkdir(parents=True)
    raw = {
        "zeta": _diagram("Zeta Flow"),
        "a-b": _diagram("A B Flow"),
        "a": _diagram("A Flow"),
        "m1": _diagram("M One"),
    }
    for diagram_id, text in raw.items():
        _write(target / f"{diagram_id}.mmd", text)
    _write(target / "m1.excalidraw.svg", "<svg/>")

    assert main(["render", str(target)]) == EXIT_OK
    document = _index(target)
    assert list(document) == EXPECTED_TOP_KEYS
    assert list(INDEX_KEYS) == EXPECTED_TOP_KEYS
    assert list(INDEX_ITEM_KEYS) == EXPECTED_ITEM_KEYS
    assert document["schema"] == INDEX_SCHEMA == "show-your-work.workflows.v1"
    assert document["generator"] == f"show-your-work {__version__}"
    assert document["source_dir"] == "docs/workflows"

    stamp = document["generated_at"]
    assert isinstance(stamp, str) and stamp.endswith("+00:00")
    assert datetime.fromisoformat(stamp).utcoffset() == timedelta(0)

    ids = [item["id"] for item in document["items"]]
    assert ids == sorted(ids) == ["a", "a-b", "m1", "zeta"]  # by id, not by file name
    for item in document["items"]:
        assert list(item) == EXPECTED_ITEM_KEYS
        text = raw[item["id"]]
        assert item["label"] == text.split("\n", 1)[0].removeprefix("%% ")
        assert item["summary"] == "A short summary of the flow."
        assert item["alt"] == "Two boxes joined by one arrow pointing right."
        assert item["not_shown"] == "retries, error paths"
        assert item["sources"] == ["docs/flow.md", "https://example.com/spec"]
        assert item["source_path"] == f"docs/workflows/{item['id']}.mmd"
        assert item["source_sha256"] == hashlib.sha256(text.encode()).hexdigest()
        assert item["rasters"] is None
        assert item["engine"] == ("excalidraw" if item["id"] == "m1" else "mermaid")


def test_build_index_rejects_a_non_utc_stamp() -> None:
    with pytest.raises(ValueError):
        build_index([], ".", datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2))))
    with pytest.raises(ValueError):
        build_index([], ".", datetime(2026, 1, 1))  # naive
    document = build_index([], ".", datetime(2026, 1, 1, tzinfo=UTC))
    assert document["generated_at"] == "2026-01-01T00:00:00+00:00"


# --- README shape and anchors (section 4) ---------------------------------------------------


def test_github_anchor_drops_colons() -> None:
    assert github_anchor(COLON_TITLE) == "release-checklist-one-gated-ship"
    assert github_anchor("Plan (v2) & Build_Step") == "plan-v2--build_step"


def test_readme_colon_title_anchor_through_cli(tmp_path: Path) -> None:
    _write(tmp_path / "release.mmd", _diagram(COLON_TITLE))
    assert main(["render", str(tmp_path)]) == EXIT_OK
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert f"- [{COLON_TITLE}](#release-checklist-one-gated-ship) - " in readme
    assert f"\n## {COLON_TITLE}\n" in readme


def test_readme_shape_order_and_intro_verbatim(tmp_path: Path) -> None:
    target = _copy_good(tmp_path / "good")
    assert main(["render", str(target)]) == EXIT_OK
    readme = (target / "README.md").read_text(encoding="utf-8")
    intro = (GOOD / "intro.md").read_bytes().decode("utf-8").replace("\r\n", "\n").rstrip("\n")
    assert intro in readme
    order = [
        f"<!-- {GENERATED_MARKER}",
        "\n# Workflow diagrams\n",
        intro,
        "\n## Contents\n",
        "- [Minimal Example](#minimal-example) - Three steps from a request to a reviewed change.",
        "\n## Minimal Example\n",
        "Source: [`minimal.mmd`](minimal.mmd)",
        "Drawn from:\n\n- `docs/example.md`\n",
        "**Alt:** Three boxes left to right joined by arrows, the last arrow labelled approve.",
        "**Deliberately not shown:** error paths, retries",
        "```mermaid\n%% Minimal Example\n",
        "\n## Regenerating\n",
        "\n```\nshow-your-work render ",
    ]
    positions = [readme.index(part) for part in order]
    assert positions == sorted(positions)
    assert readme.index(f"<!-- {GENERATED_MARKER}") == 0
    # The Drawn-from list holds only sources, never Alt or Not shown text.
    drawn = readme.split("Drawn from:", 1)[1].split("**Alt:**", 1)[0]
    assert "Alt" not in drawn and "Not shown" not in drawn


def test_readme_without_intro_still_renders(tmp_path: Path) -> None:
    _write(tmp_path / "flow.mmd", _diagram("Flow"))
    assert main(["render", str(tmp_path)]) == EXIT_OK
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "# Workflow diagrams\n\n## Contents\n" in readme


def test_readme_anchors_suffix_repeated_headings(tmp_path: Path) -> None:
    _write(tmp_path / "a.mmd", _diagram("Same Title"))
    _write(tmp_path / "b.mmd", _diagram("Same Title"))
    _write(tmp_path / "c.mmd", _diagram("Contents"))
    _write(tmp_path / "intro.md", "## Background\n\n```\n## not a heading\n```\n")
    _write(tmp_path / "d.mmd", _diagram("Background"))
    assert main(["render", str(tmp_path)]) == EXIT_OK
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "- [Same Title](#same-title) - " in readme
    assert "- [Same Title](#same-title-1) - " in readme
    assert "- [Contents](#contents-1) - " in readme  # the fixed ## Contents heading is first
    assert "- [Background](#background-1) - " in readme  # intro.md's heading comes first


def test_readme_anchors_count_setext_and_linked_intro_headings(tmp_path: Path) -> None:
    """Setext headings (and ATX headings with links) in intro.md occupy GitHub slugs too."""
    intro = (
        "Overview\n"
        "========\n"
        "\n"
        "Some prose that is\n"
        "Release Notes\n"
        "-------------\n"
        "\n"
        "---\n"
        "\n"
        "Rule Break\n"
        "\n"
        "---\n"
        "\n"
        "- List Item\n"
        "---\n"
        "\n"
        "## [Linked Title](https://example.com/x)\n"
        "\n"
        "```\n"
        "Fenced Title\n"
        "---\n"
        "```\n"
    )
    _write(tmp_path / "intro.md", intro)
    titles = {
        "a": "Overview",
        "b": "Some prose that is Release Notes",
        "c": "Rule Break",
        "d": "List Item",
        "e": "Linked Title",
        "f": "Fenced Title",
    }
    for diagram_id, title in titles.items():
        _write(tmp_path / f"{diagram_id}.mmd", _diagram(title))
    assert main(["render", str(tmp_path)]) == EXIT_OK
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "- [Overview](#overview-1) - " in readme  # setext ===
    # A setext heading's text is its whole paragraph.
    assert "- [Some prose that is Release Notes](#some-prose-that-is-release-notes-1) - " in readme
    assert "- [Rule Break](#rule-break) - " in readme  # --- after a blank line: thematic break
    assert "- [List Item](#list-item) - " in readme  # a list item is never a setext paragraph
    assert "- [Linked Title](#linked-title-1) - " in readme  # slug from the rendered link text
    assert "- [Fenced Title](#fenced-title) - " in readme  # inside a fence: not a heading


def test_readme_anchors_strip_atx_closing_sequences(tmp_path: Path) -> None:
    """``## Closed ##`` slugs as ``closed``; ``#hashtag`` (no space) is not a heading."""
    _write(tmp_path / "intro.md", "## Closed ##\n\n#Hashtag\n\n### Keep# ###\n")
    _write(tmp_path / "a.mmd", _diagram("Closed"))
    _write(tmp_path / "b.mmd", _diagram("Hashtag"))
    _write(tmp_path / "c.mmd", _diagram("Keep#"))
    assert main(["render", str(tmp_path)]) == EXIT_OK
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "- [Closed](#closed-1) - " in readme
    assert "- [Hashtag](#hashtag) - " in readme
    assert "- [Keep#](#keep-1) - " in readme  # 'Keep#' keeps its '#': no space before it
    assert list(render._intro_headings("# a #b\n# #\n")) == ["a #b", ""]


def test_build_readme_handles_backticks_in_sources_and_body() -> None:
    from show_your_work.header import parse_header

    text = _diagram("Tick Flow").replace("docs/flow.md", "docs/`odd`.md")
    text += "    %% a ```fence``` in a comment\n"
    diagram = render.Diagram("tick", parse_header(text), text, "0" * 64, "mermaid")
    readme = build_readme([diagram], None, "docs/workflows")
    assert "- ``docs/`odd`.md``\n" in readme
    assert "````mermaid\n" in readme  # fence longer than the body's backtick run
    assert "show-your-work render docs/workflows --check" in readme


# --- source_dir / source_path (section 3.4) ------------------------------------------------


def test_source_path_is_cwd_independent_and_project_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    target = layout_project(root, GOOD)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    monkeypatch.chdir(root)
    assert main(["render", "docs/workflows"]) == EXIT_OK
    first = _index(target)
    (target / "workflows.json").unlink()

    monkeypatch.chdir(elsewhere)
    assert main(["render", str(target)]) == EXIT_OK
    second = _index(target)
    (target / "workflows.json").unlink()

    monkeypatch.chdir(target)
    assert main(["render", "."]) == EXIT_OK
    third = _index(target)
    (target / "workflows.json").unlink()

    monkeypatch.chdir(root)
    assert main(["render"]) == EXIT_OK  # default ./docs/workflows from the project root
    fourth = _index(target)

    for document in (first, second, third, fourth):
        assert document["source_dir"] == "docs/workflows"
        assert [i["source_path"] for i in document["items"]] == ["docs/workflows/minimal.mmd"]


def test_git_file_marker_counts_as_project_root(tmp_path: Path) -> None:
    root = tmp_path / "worktree"
    root.mkdir()
    _write(root / ".git", "gitdir: elsewhere\n")  # a worktree's .git is a file
    target = _copy_good(root / "docs" / "workflows")
    assert main(["render", str(target)]) == EXIT_OK
    assert _index(target)["items"][0]["source_path"] == "docs/workflows/minimal.mmd"


def test_source_dir_is_dot_without_a_git_ancestor(tmp_path: Path) -> None:
    if _has_git_ancestor(tmp_path):
        pytest.skip("the tmp directory has a .git ancestor on this host")
    target = _copy_good(tmp_path / "good")
    assert main(["render", str(target)]) == EXIT_OK
    document = _index(target)
    assert document["source_dir"] == "."
    assert document["items"][0]["source_path"] == "./minimal.mmd"


# --- CRLF normalization (section 3.3) ------------------------------------------------------


def test_crlf_and_lf_copies_produce_the_same_hash_and_readme(tmp_path: Path) -> None:
    lf_dir = layout_project(tmp_path / "lf", GOOD)
    crlf_dir = layout_project(tmp_path / "crlf", GOOD)
    for name in ("minimal.mmd", "intro.md"):
        raw = (lf_dir / name).read_bytes()
        assert b"\r" not in raw
        (crlf_dir / name).write_bytes(raw.replace(b"\n", b"\r\n"))

    assert main(["render", str(lf_dir)]) == EXIT_OK
    assert main(["render", str(crlf_dir)]) == EXIT_OK
    lf_item = _index(lf_dir)["items"][0]
    crlf_item = _index(crlf_dir)["items"][0]
    expected = hashlib.sha256((lf_dir / "minimal.mmd").read_bytes()).hexdigest()
    assert lf_item["source_sha256"] == crlf_item["source_sha256"] == expected
    assert (lf_dir / "README.md").read_bytes() == (crlf_dir / "README.md").read_bytes()


def test_crlf_checkout_of_generated_files_is_up_to_date(tmp_path: Path) -> None:
    """core.autocrlf may check README/index out as CRLF; that is not staleness or churn."""
    target = _copy_good(tmp_path / "good")
    assert main(["render", str(target)]) == EXIT_OK
    for name in ("README.md", "workflows.json"):
        path = target / name
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    before = snapshot(target)
    assert main(["render", str(target), "--check"]) == EXIT_OK
    assert main(["render", str(target)]) == EXIT_OK
    assert snapshot(target) == before


# --- rasters preserved-or-null (section 3.4) -----------------------------------------------


def _raster_entry(diagram_id: str, theme: str, data: bytes) -> dict[str, Any]:
    return {
        "path": f"rasters/{diagram_id}-{theme}.png",
        "sha256": hashlib.sha256(data).hexdigest(),
        "width": 640,
        "height": 480,
        "bytes": len(data),
    }


def _place_rasters(target: Path, diagram_id: str = "minimal") -> dict[str, Any]:
    (target / "rasters").mkdir(exist_ok=True)
    rasters: dict[str, Any] = {}
    for theme in ("light", "dark"):
        data = f"png-{theme}".encode()
        (target / "rasters" / f"{diagram_id}-{theme}.png").write_bytes(data)
        rasters[theme] = _raster_entry(diagram_id, theme, data)
    return rasters


def _set_rasters(target: Path, rasters: Any) -> None:
    document = _index(target)
    document["items"][0]["rasters"] = rasters
    (target / "workflows.json").write_bytes(
        (json.dumps(document, indent=2, ensure_ascii=True) + "\n").encode()
    )


def test_plain_render_preserves_rasters_when_both_files_exist(tmp_path: Path) -> None:
    target = _copy_good(tmp_path / "good")
    assert main(["render", str(target)]) == EXIT_OK
    rasters = _place_rasters(target)
    (target / "rasters" / "stray.png").write_bytes(b"stray")
    _set_rasters(target, rasters)

    assert main(["render", str(target)]) == EXIT_OK
    item = _index(target)["items"][0]
    assert item["rasters"] == rasters
    assert list(item["rasters"]) == ["light", "dark"]
    for entry in item["rasters"].values():
        assert list(entry) == EXPECTED_RASTER_KEYS
    assert sorted(p.name for p in (target / "rasters").iterdir()) == [
        "minimal-dark.png",
        "minimal-light.png",
        "stray.png",
    ]
    assert main(["render", str(target), "--check"]) == EXIT_OK


def test_plain_render_nulls_rasters_when_a_file_is_missing_and_deletes_nothing(
    tmp_path: Path,
) -> None:
    target = _copy_good(tmp_path / "good")
    assert main(["render", str(target)]) == EXIT_OK
    _set_rasters(target, _place_rasters(target))
    (target / "rasters" / "minimal-dark.png").unlink()

    # --check masks rasters (host-font dependent hashes), so the dangling entry is not stale.
    assert main(["render", str(target), "--check"]) == EXIT_OK
    assert main(["render", str(target)]) == EXIT_OK
    assert _index(target)["items"][0]["rasters"] is None
    assert [p.name for p in (target / "rasters").iterdir()] == ["minimal-light.png"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["light"].update(path="rasters/other-light.png"),
        lambda r: r["light"].update(path="../minimal-light.png"),
        lambda r: r["dark"].update(sha256="not-a-hash"),
        lambda r: r["dark"].update(width=True),
        lambda r: r["dark"].update(height=-1),
        lambda r: r.pop("dark"),
        lambda r: r["light"].pop("bytes"),
    ],
    ids=["wrong-id", "escape", "bad-hash", "bool-width", "negative", "no-dark", "missing-key"],
)
def test_malformed_previous_rasters_become_null(tmp_path: Path, mutate: Any) -> None:
    target = _copy_good(tmp_path / "good")
    assert main(["render", str(target)]) == EXIT_OK
    rasters = _place_rasters(target)
    mutate(rasters)
    _set_rasters(target, rasters)
    assert main(["render", str(target)]) == EXIT_OK
    assert _index(target)["items"][0]["rasters"] is None


def test_preserved_rasters_drop_unknown_keys(tmp_path: Path) -> None:
    target = _copy_good(tmp_path / "good")
    assert main(["render", str(target)]) == EXIT_OK
    rasters = _place_rasters(target)
    expected = json.loads(json.dumps(rasters))
    rasters["light"]["extra"] = 1
    rasters["extra"] = {}
    _set_rasters(target, rasters)
    assert main(["render", str(target)]) == EXIT_OK
    assert _index(target)["items"][0]["rasters"] == expected


# --- refusal and gate errors ---------------------------------------------------------------


def test_render_refuses_a_failing_set_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _copy_good(tmp_path / "good")
    _write(target / "broken.mmd", "%% Broken\n%% Summary: no other fields\n\nflowchart LR\n")
    _write(target / "README.md", "hand-written placeholder\n")
    before = snapshot(target)
    assert main(["render", str(target)]) == EXIT_FINDINGS
    captured = capsys.readouterr()
    assert "broken.mmd" in captured.out and "FAIL" in captured.out
    assert "refusing to render: 1 of 2 file(s) fail check" in captured.err
    assert snapshot(target) == before
    assert not (target / "workflows.json").exists()
    assert main(["render", str(target), "--check"]) == EXIT_FINDINGS
    assert snapshot(target) == before


def test_render_json_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = _copy_good(tmp_path / "good")
    assert main(["render", str(target), "--json"]) == EXIT_OK
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert list(document) == ["schema", "target", "verb", "files", "summary", "exit_code"]
    assert (document["schema"], document["verb"], document["exit_code"]) == (
        REPORT_SCHEMA,
        "render",
        EXIT_OK,
    )
    assert [row["file"] for row in document["files"]] == ["minimal.mmd"]
    assert "README.md: written" in captured.err  # stdout stays pure JSON

    assert main(["render", str(target), "--check", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["exit_code"] == EXIT_OK


def test_render_default_target_missing_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["render"]) == EXIT_GATE
    expected = MSG_NO_DEFAULT_TARGET.format(path=(tmp_path / "docs" / "workflows").resolve())
    assert expected.split(" at ")[0] in capsys.readouterr().err


def test_render_empty_dir_exits_2(tmp_path: Path) -> None:
    assert main(["render", str(tmp_path)]) == EXIT_GATE
    assert list(tmp_path.iterdir()) == []


def test_render_write_failure_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _copy_good(tmp_path / "good")

    def _denied(src: str, dst: str) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "replace", _denied)
    assert main(["render", str(target)]) == EXIT_GATE
    assert "cannot write" in capsys.readouterr().err
    assert sorted(p.name for p in target.iterdir()) == ["intro.md", "minimal.mmd"]  # no temp left


def test_render_non_utf8_intro_exits_2(tmp_path: Path) -> None:
    target = _copy_good(tmp_path / "good")
    (target / "intro.md").write_bytes(b"\xff\xfe broken")
    assert main(["render", str(target)]) == EXIT_GATE
    assert not (target / "README.md").exists()
    assert not (target / "workflows.json").exists()


# --- index_up_to_date (the --check comparison) ---------------------------------------------


def _fresh(tmp_path: Path) -> tuple[Path, str]:
    target = _copy_good(tmp_path / "good")
    assert main(["render", str(target)]) == EXIT_OK
    return target, (target / "workflows.json").read_text(encoding="utf-8")


def test_deeply_nested_corrupt_index_is_stale_not_a_traceback(tmp_path: Path) -> None:
    target, text = _fresh(tmp_path)
    nested = "[" * 200_000 + "]" * 200_000 + "\n"
    with pytest.raises(RecursionError):
        json.loads(nested)  # the failure mode being guarded actually occurs on this input
    assert not index_up_to_date(nested, json.loads(text), mask_rasters=True)
    (target / "workflows.json").write_bytes(nested.encode())
    assert main(["render", str(target), "--check"]) == EXIT_FINDINGS
    assert main(["render", str(target)]) == EXIT_OK
    assert _index(target)["items"][0]["id"] == "minimal"
    assert main(["render", str(target), "--check"]) == EXIT_OK


def test_check_flags_a_reformatted_or_restamped_index(tmp_path: Path) -> None:
    target, text = _fresh(tmp_path)
    document = json.loads(text)
    assert index_up_to_date(text, document, mask_rasters=True)

    compact = json.dumps(document) + "\n"
    assert not index_up_to_date(compact, document, mask_rasters=True)

    for stamp in ("yesterday", "2026-01-01T00:00:00+02:00", "2026-01-01T00:00:00", 5):
        bad = json.dumps({**document, "generated_at": stamp}, indent=2) + "\n"
        assert not index_up_to_date(bad, document, mask_rasters=True)

    extra = {**document, "items": [{**document["items"][0], "extra": 1}]}
    assert not index_up_to_date(json.dumps(extra, indent=2) + "\n", document, mask_rasters=True)
    assert not index_up_to_date("[]\n", document, mask_rasters=True)
    assert not index_up_to_date(None, document, mask_rasters=True)

    (target / "workflows.json").write_bytes(compact.encode())
    assert main(["render", str(target), "--check"]) == EXIT_FINDINGS
    assert main(["render", str(target)]) == EXIT_OK
    assert main(["render", str(target), "--check"]) == EXIT_OK


# --- house SVGs and the README embed (Phase 2 section 2.5) ---------------------------------


def _link_dir(kind: str, link: Path, target: Path) -> None:
    if kind == "junction":
        junction(link, target)
        return
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")


def _sidecar_project(tmp_path: Path, *, keep: tuple[str, ...] = ("feedback", "flow")) -> Path:
    """``layout/valid`` in a tmp project, keeping only the sidecars of the ``keep`` ids."""
    target = layout_project(tmp_path / "proj", LAYOUT_VALID)
    for path in target.glob("*.layout.json"):
        if path.name.removesuffix(".layout.json") not in keep:
            path.unlink()
    return target


def _marked_svg() -> bytes:
    return (GOLDEN / "flow-light.svg").read_bytes()


def _status_lines(text: str) -> dict[str, str]:
    """The ``<file>: <state>`` lines of a render run, keyed by file."""
    pairs = (line.rpartition(": ") for line in text.splitlines() if ": " in line)
    return {name: state for name, _, state in pairs if state in ("written", "up to date", "stale")}


@pytest.mark.parametrize("fixture", ["valid", "realistic"])
def test_sidecar_less_folder_renders_byte_identically_to_phase1(
    fixture: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if fixture == "valid":
        target = _sidecar_project(tmp_path, keep=())
    else:
        target = layout_project(tmp_path / "proj", FIXTURES / "realistic")
    monkeypatch.setattr(render, "_utc_now", lambda: PHASE1_NOW)
    assert main(["render", str(target)]) == EXIT_OK
    readme = PHASE1 / f"render-{fixture}-README.md"
    index = PHASE1 / f"render-{fixture}-workflows.json"
    assert (target / "README.md").read_bytes() == readme.read_bytes()
    assert (target / "workflows.json").read_bytes() == index.read_bytes()
    assert not (target / "rasters").exists()


@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_sidecar_less_folder_renders_as_in_phase1_through_a_linked_rasters(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "flow-light.svg").write_bytes(_marked_svg())  # a marked orphan behind the link
    (elsewhere / "stray.png").write_bytes(b"stray")
    target = _sidecar_project(tmp_path, keep=())
    _link_dir(kind, target / "rasters", elsewhere)
    before = snapshot(elsewhere)
    monkeypatch.setattr(render, "_utc_now", lambda: PHASE1_NOW)
    assert main(["render", str(target)]) == EXIT_OK
    assert main(["render", str(target), "--check"]) == EXIT_OK
    assert (target / "README.md").read_bytes() == (PHASE1 / "render-valid-README.md").read_bytes()
    assert snapshot(elsewhere) == before  # cleanup is skipped silently through a link


@pytest.mark.parametrize("kind", ["junction", "symlink", "file"])
def test_sidecar_folder_refuses_a_rasters_that_is_not_a_plain_directory(
    kind: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_bytes(b"keep")
    target = _sidecar_project(tmp_path)
    if kind == "file":
        (target / "rasters").write_bytes(b"not a dir")
    else:
        _link_dir(kind, target / "rasters", elsewhere)
    before = snapshot(target)
    assert main(["render", str(target)]) == EXIT_GATE
    assert main(["render", str(target), "--check"]) == EXIT_GATE
    assert "not a plain directory" in capsys.readouterr().err
    assert snapshot(elsewhere) == {"keep.txt": b"keep"}
    assert snapshot(target) == before
    assert not (target / "README.md").exists()


@pytest.mark.parametrize("as_json", [False, True])
def test_a_node_name_edit_keeps_check_passing_and_makes_exactly_the_two_svgs_stale(
    as_json: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _sidecar_project(tmp_path)
    assert main(["render", str(target)]) == EXIT_OK
    sidecar = target / "flow.layout.json"
    sidecar.write_bytes(sidecar.read_bytes().replace(b'"Pack box"', b'"Pack bix"', 1))
    assert main(["check", str(target)]) == EXIT_OK
    capsys.readouterr()
    before = snapshot(target)

    assert (
        main(["render", str(target), "--check", *(["--json"] if as_json else [])]) == EXIT_FINDINGS
    )
    captured = capsys.readouterr()
    if as_json:
        assert json.loads(captured.out)["exit_code"] == EXIT_FINDINGS
    lines = captured.err if as_json else captured.out
    stale = {name for name, state in _status_lines(lines).items() if state == "stale"}
    assert stale == {"rasters/flow-light.svg", "rasters/flow-dark.svg"}
    assert _status_lines(lines)["README.md"] == "up to date"
    assert _status_lines(lines)["workflows.json"] == "up to date"
    command = f"show-your-work render {target}"
    for name in sorted(stale):
        assert f"{name} is out of date; run '{command}'" in captured.err
    assert snapshot(target) == before  # --check writes nothing


def test_crlf_checked_out_svgs_stay_current_and_untouched_until_their_content_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # core.autocrlf=true checks the committed LF SVGs out as CRLF; they compare as LF text like
    # README.md / workflows.json, so they are neither stale nor rewritten.
    target = _sidecar_project(tmp_path)
    assert main(["render", str(target)]) == EXIT_OK
    svgs = sorted((target / "rasters").glob("*.svg"))
    assert svgs
    for path in svgs:
        raw = path.read_bytes()
        assert b"\r" not in raw
        path.write_bytes(raw.replace(b"\n", b"\r\n"))
    crlf = snapshot(target)
    capsys.readouterr()

    assert main(["render", str(target), "--check"]) == EXIT_OK
    states = _status_lines(capsys.readouterr().out)
    current = {name for name, state in states.items() if state == "up to date"}
    assert {f"rasters/{p.name}" for p in svgs} <= current
    assert "stale" not in states.values()

    assert main(["render", str(target)]) == EXIT_OK
    assert "written" not in _status_lines(capsys.readouterr().out).values()
    assert snapshot(target) == crlf  # idempotent: the CRLF bytes are left as they are

    sidecar = target / "flow.layout.json"
    sidecar.write_bytes(sidecar.read_bytes().replace(b'"Pack box"', b'"Pack bix"', 1))
    assert main(["render", str(target), "--check"]) == EXIT_FINDINGS
    stale = {n for n, s in _status_lines(capsys.readouterr().out).items() if s == "stale"}
    assert stale == {"rasters/flow-light.svg", "rasters/flow-dark.svg"}


def test_a_missing_svg_and_a_marked_orphan_svg_are_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _sidecar_project(tmp_path)
    assert main(["render", str(target)]) == EXIT_OK
    (target / "rasters" / "flow-dark.svg").unlink()
    (target / "rasters" / "old-light.svg").write_bytes(_marked_svg())
    capsys.readouterr()
    assert main(["render", str(target), "--check"]) == EXIT_FINDINGS
    stale = {n for n, s in _status_lines(capsys.readouterr().out).items() if s == "stale"}
    assert stale == {"rasters/flow-dark.svg", "rasters/old-light.svg"}

    assert main(["render", str(target)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "rasters/flow-dark.svg: written" in out
    assert "rasters/old-light.svg: deleted" in out
    assert not (target / "rasters" / "old-light.svg").exists()
    assert main(["render", str(target), "--check"]) == EXIT_OK


def test_readme_embeds_the_house_svgs_with_the_exact_template(tmp_path: Path) -> None:
    target = _sidecar_project(tmp_path)
    assert main(["render", str(target)]) == EXIT_OK
    readme = (target / "README.md").read_text(encoding="utf-8")
    flow = json.loads((target / "flow.layout.json").read_bytes())
    body = (LAYOUT_VALID / "flow.mmd").read_text(encoding="utf-8").rstrip() + "\n"
    # Literal section 2.5 text: a drifted constant fails here.
    embed = (
        "**Alt (mermaid source):** Two columns, plan then build, joined by a spine; a dashed "
        "return closes the loop.\n\n"
        "**Deliberately not shown:** refunds, partial shipments\n\n"
        "<picture>\n"
        '  <source media="(prefers-color-scheme: dark)" srcset="rasters/flow-dark.svg">\n'
        f'  <img alt="{flow["alt"]}" src="rasters/flow-light.svg">\n'
        "</picture>\n\n"
        "<details><summary>Mermaid source</summary>\n\n"
        f"```mermaid\n{body}```\n\n"
        "</details>\n\n---\n"
    )
    assert embed in readme
    assert readme.startswith(
        "<!-- GENERATED FILE - DO NOT EDIT.\n"
        "     Produced by `show-your-work render` from intro.md, the .mmd files and their "
        ".layout.json sidecars in this directory.\n"
    )
    assert (
        "The house-style SVGs in `rasters/` are generated from the `*.layout.json` sidecars by "
        "the same command.\n"
    ) in readme
    # The sidecar-less diagram in the same folder keeps its Phase 1 section.
    plain = (LAYOUT_VALID / "plain.mmd").read_text(encoding="utf-8").rstrip() + "\n"
    assert "**Alt:** Two boxes side by side joined by a single arrow.\n" in readme
    assert f"everything else\n\n```mermaid\n{plain}```\n" in readme
    assert readme.count("<picture>") == len(list(target.glob("*.layout.json")))


def test_an_unmarked_file_at_a_generated_svg_path_exits_2_and_stays_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _sidecar_project(tmp_path)
    (target / "rasters").mkdir()
    mine = target / "rasters" / "flow-light.svg"
    mine.write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'><!-- mine --></svg>\n")
    before = snapshot(target)
    assert main(["render", str(target)]) == EXIT_GATE
    assert main(["render", str(target), "--check"]) == EXIT_GATE
    assert "refusing to overwrite" in capsys.readouterr().err
    assert snapshot(target) == before


def _quoting_svgs() -> dict[str, bytes]:
    """User SVGs that hold the marker text, but not as the first child the generator writes."""
    root = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">'
    golden = _marked_svg().decode("utf-8")
    first, _, rest = golden.partition("\n")
    variants = {
        "desc": f"{root}\n<desc>quoting {HOUSE_SVG_MARKER}</desc>\n</svg>\n",
        "later-comment": f"{root}\n<!-- mine -->\n{HOUSE_SVG_MARKER}\n</svg>\n",
        "title-first": f"{first}\n<title>mine</title>\n{rest}",
        "marker-before-root": f"{HOUSE_SVG_MARKER}\n{golden}",
        "same-line": f"{first}{rest}",
    }
    return {name: text.encode("utf-8") for name, text in variants.items()}


@pytest.mark.parametrize("variant", sorted(_quoting_svgs()))
def test_a_user_svg_quoting_the_marker_at_a_generated_path_exits_2_and_stays_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], variant: str
) -> None:
    quoting = _quoting_svgs()[variant]
    assert HOUSE_SVG_MARKER.encode("ascii") in quoting
    target = _sidecar_project(tmp_path)
    (target / "rasters").mkdir()
    (target / "rasters" / "flow-light.svg").write_bytes(quoting)
    before = snapshot(target)
    assert main(["render", str(target)]) == EXIT_GATE
    assert main(["render", str(target), "--check"]) == EXIT_GATE
    assert "refusing to overwrite" in capsys.readouterr().err
    assert snapshot(target) == before


@pytest.mark.parametrize("variant", sorted(_quoting_svgs()))
def test_a_user_svg_quoting_the_marker_is_not_deleted_as_an_orphan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], variant: str
) -> None:
    quoting = _quoting_svgs()[variant]
    target = _sidecar_project(tmp_path)
    assert main(["render", str(target)]) == EXIT_OK
    mine = target / "rasters" / "old-light.svg"
    mine.write_bytes(quoting)
    capsys.readouterr()
    assert main(["render", str(target), "--check"]) == EXIT_OK
    assert "old-light.svg" not in capsys.readouterr().out
    assert main(["render", str(target)]) == EXIT_OK
    assert "deleted" not in capsys.readouterr().out
    assert mine.read_bytes() == quoting


def test_removing_the_only_sidecar_deletes_its_svgs_from_a_plain_rasters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _sidecar_project(tmp_path, keep=("flow",))
    monkeypatch.setattr(render, "_utc_now", lambda: PHASE1_NOW)
    assert main(["render", str(target)]) == EXIT_OK
    assert sorted(p.name for p in (target / "rasters").iterdir()) == [
        "flow-dark.svg",
        "flow-light.svg",
    ]
    (target / "flow.layout.json").unlink()
    capsys.readouterr()
    assert main(["render", str(target)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "rasters/flow-light.svg: deleted" in out and "rasters/flow-dark.svg: deleted" in out
    assert list((target / "rasters").iterdir()) == []
    # Back to a sidecar-less folder: the Phase 1 README again.
    assert (target / "README.md").read_bytes() == (PHASE1 / "render-valid-README.md").read_bytes()


def test_a_stale_generated_svg_is_removed_last_and_nothing_else_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marked = _marked_svg()
    target = _sidecar_project(tmp_path)
    rasters = target / "rasters"
    (rasters / "sub").mkdir(parents=True)
    survivors = {
        "x.png": b"png bytes",
        "notes.txt": b"keep me",
        "sub/y-light.svg": marked,
        "mine-light.svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>\n",
    }
    for name, data in survivors.items():
        (rasters / name).write_bytes(data)
    (rasters / "old-light.svg").write_bytes(marked)

    events: list[str] = []
    real_write, real_delete = render.write_atomic, render._delete

    def _record_write(path: Path, text: str | bytes, *, mode: int | None = None) -> None:
        events.append(f"write {path.name}")
        real_write(path, text, mode=mode)

    def _record_delete(path: Path) -> None:
        events.append(f"delete {path.name}")
        real_delete(path)

    monkeypatch.setattr(render, "write_atomic", _record_write)
    monkeypatch.setattr(render, "_delete", _record_delete)
    assert main(["render", str(target)]) == EXIT_OK
    assert events[-1] == "delete old-light.svg"
    assert events.index("write README.md") < events.index("delete old-light.svg")
    assert events.index("write workflows.json") < events.index("delete old-light.svg")
    assert events.count("delete old-light.svg") == 1 and sum("delete" in e for e in events) == 1
    assert not (rasters / "old-light.svg").exists()
    for name, data in survivors.items():
        assert (rasters / name).read_bytes() == data, name


def test_a_symlinked_house_svg_name_in_rasters_survives_cleanup(tmp_path: Path) -> None:
    marked = _marked_svg()
    outside = tmp_path / "outside-light.svg"
    outside.write_bytes(marked)
    target = _sidecar_project(tmp_path)
    (target / "rasters").mkdir()
    link = target / "rasters" / "link-light.svg"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")
    assert main(["render", str(target)]) == EXIT_OK
    assert link.is_symlink()
    assert outside.read_bytes() == marked


def test_a_sidecar_failing_check_makes_render_exit_1_writing_nothing(tmp_path: Path) -> None:
    target = _sidecar_project(tmp_path)
    shutil.copyfile(LAYOUT / "bound" / "name-23.layout.json", target / "flow.layout.json")
    before = snapshot(target)
    assert main(["render", str(target)]) == EXIT_FINDINGS
    assert snapshot(target) == before
    assert not (target / "rasters").exists()


def test_a_sidecar_changed_after_check_exits_2_writing_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _sidecar_project(tmp_path)
    before = snapshot(target)
    bound = (LAYOUT / "bound" / "name-23.layout.json").read_bytes()
    monkeypatch.setattr(render, "read_layout", lambda path: bound)  # render's re-read only
    assert main(["render", str(target)]) == EXIT_GATE
    assert "changed during render" in capsys.readouterr().err
    assert snapshot(target) == before


def test_png_with_a_sidecar_writes_both_rasters_and_house_svgs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _sidecar_project(tmp_path)
    monkeypatch.setattr(renderer, "load", FakeRenderer)
    assert main(["render", str(target), "--png"]) == EXIT_OK
    names = sorted(p.name for p in (target / "rasters").iterdir())
    ids = ("feedback", "flow", "plain")
    expected = [f"{i}-{t}.png" for i in ids for t in RASTER_THEMES]
    expected += [f"{i}-{t}.svg" for i in ("feedback", "flow") for t in RASTER_THEMES]
    assert names == sorted(expected)
    assert main(["render", str(target), "--check", "--png"]) == EXIT_OK
    assert main(["render", str(target), "--check"]) == EXIT_OK
