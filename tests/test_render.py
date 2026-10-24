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

from show_your_work import __version__, render
from show_your_work.cli import main
from show_your_work.constants import (
    EXIT_FINDINGS,
    EXIT_GATE,
    EXIT_OK,
    GENERATED_MARKER,
    INDEX_ITEM_KEYS,
    INDEX_KEYS,
    INDEX_SCHEMA,
    MSG_NO_DEFAULT_TARGET,
    REPORT_SCHEMA,
)
from show_your_work.render import build_index, build_readme, github_anchor, index_up_to_date

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"

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


def _project(tmp_path: Path, name: str = "proj") -> Path:
    """A tmp project tree with a ``.git`` marker at its root and the good set in docs/workflows."""
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    _copy_good(root / "docs" / "workflows")
    return root


def _index(target: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((target / "workflows.json").read_text(encoding="utf-8"))
    return document


def _snapshot(target: Path) -> dict[str, bytes]:
    return {
        p.relative_to(target).as_posix(): p.read_bytes()
        for p in sorted(target.rglob("*"))
        if p.is_file()
    }


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
    before = _snapshot(target)
    assert main(["render", str(target), "--check"]) == EXIT_FINDINGS
    captured = capsys.readouterr()
    assert "README.md: stale" in captured.out
    assert "workflows.json: stale" in captured.out
    assert f"README.md is out of date; run 'show-your-work render {target}'" in captured.err
    assert _snapshot(target) == before  # --check writes nothing


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
    root = _project(tmp_path)
    target = root / "docs" / "workflows"
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
    lf_root = _project(tmp_path, "lf")
    crlf_root = _project(tmp_path, "crlf")
    lf_dir = lf_root / "docs" / "workflows"
    crlf_dir = crlf_root / "docs" / "workflows"
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
    before = _snapshot(target)
    assert main(["render", str(target), "--check"]) == EXIT_OK
    assert main(["render", str(target)]) == EXIT_OK
    assert _snapshot(target) == before


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
    before = _snapshot(target)
    assert main(["render", str(target)]) == EXIT_FINDINGS
    captured = capsys.readouterr()
    assert "broken.mmd" in captured.out and "FAIL" in captured.out
    assert "refusing to render: 1 of 2 file(s) fail check" in captured.err
    assert _snapshot(target) == before
    assert not (target / "workflows.json").exists()
    assert main(["render", str(target), "--check"]) == EXIT_FINDINGS
    assert _snapshot(target) == before


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


def test_render_png_exits_2_until_step_6(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _copy_good(tmp_path / "good")
    before = _snapshot(target)
    assert main(["render", str(target), "--png"]) == EXIT_GATE
    assert "--png" in capsys.readouterr().err
    assert _snapshot(target) == before


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
