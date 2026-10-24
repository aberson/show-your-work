"""The synthetic ``realistic`` fixture set (plan Step 5, P6).

The real coding-root diagrams are private, so this public repo carries five SYNTHETIC diagrams on
generic topics that reproduce every header edge case the real set exhibits: a title containing a
colon, a title shaped like a label (``%% Title: ...``), unbulleted ``Sources:`` continuation lines
with `` - description`` suffixes, a CRLF file, a ``Not shown:`` of exactly 400 characters and a
five-bullet ``Sources:`` list. Every behavioural test drives the production caller ``cli.main``;
``render`` always runs against a tmp copy, never the committed folder.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from show_your_work.cli import main
from show_your_work.constants import EXIT_OK, MAX_NOT_SHOWN

REALISTIC = Path(__file__).parent / "fixtures" / "realistic"

KITCHEN = "kitchen-ticket-flow"
LIBRARY = "library-loan-lifecycle"
GARDEN_CRLF = "garden-watering-crlf"
BIKE = "bike-repair-triage"
RECIPE = "recipe-scaling-pipeline"
IDS = sorted([KITCHEN, LIBRARY, GARDEN_CRLF, BIKE, RECIPE])


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "workflows"
    shutil.copytree(REALISTIC, target)
    return target


def _rendered(tmp_path: Path) -> tuple[str, dict[str, dict[str, Any]]]:
    """Render a tmp copy; return the README text and the index items keyed by id."""
    target = _copy(tmp_path)
    assert main(["render", str(target)]) == EXIT_OK
    readme = (target / "README.md").read_bytes().decode("utf-8")
    index = json.loads((target / "workflows.json").read_bytes().decode("utf-8"))
    return readme, {item["id"]: item for item in index["items"]}


def _header_contents(name: str) -> list[str]:
    """The text after ``%%`` on each non-empty header line of a fixture (CRLF normalised)."""
    text = (REALISTIC / f"{name}.mmd").read_bytes().decode("ascii").replace("\r\n", "\n")
    contents: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("%%"):
            break
        contents.append(stripped[2:].strip())
    return contents


def _sections(readme: str) -> dict[str, str]:
    """README per-diagram sections keyed by their ``##`` heading (the Regenerating one dropped)."""
    sections: dict[str, str] = {}
    for chunk in readme.split("\n---\n\n## ")[1:]:
        heading, _, body = chunk.partition("\n")
        sections[heading] = body
    sections.pop("Regenerating")
    return sections


def _contents_entries(readme: str) -> list[str]:
    _, _, after = readme.partition("\n## Contents\n\n")
    block = after.split("\n---\n", 1)[0]
    return [line for line in block.split("\n") if line.startswith("- [")]


def _drawn_from(section: str) -> list[str]:
    _, _, after = section.partition("Drawn from:\n\n")
    block = after.split("\n\n", 1)[0]
    return block.split("\n")


# --- check ---------------------------------------------------------------------------------


def test_check_realistic_passes_five_of_five(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(REALISTIC), "--json"]) == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["total"] == 5
    assert report["summary"]["passed"] == 5
    assert sorted(f["id"] for f in report["files"]) == IDS
    assert all(f["findings"] == [] for f in report["files"])


def test_check_realistic_human_summary(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(REALISTIC)]) == EXIT_OK
    assert "5/5 pass" in capsys.readouterr().out


def test_realistic_folder_holds_exactly_the_five_diagrams_and_intro() -> None:
    assert sorted(p.name for p in REALISTIC.iterdir()) == sorted(
        [*(f"{i}.mmd" for i in IDS), "intro.md"]
    )


# --- the edge cases are really present -----------------------------------------------------


def test_crlf_fixture_kept_crlf_by_gitattributes() -> None:
    raw = (REALISTIC / f"{GARDEN_CRLF}.mmd").read_bytes()
    assert b"\r\n" in raw, ".gitattributes '-text' must keep the *-crlf.mmd fixture CRLF"
    assert raw.count(b"\n") == raw.count(b"\r\n"), "every line ends CRLF (no bare LF)"


def test_other_realistic_files_are_lf_ascii() -> None:
    for path in REALISTIC.iterdir():
        raw = path.read_bytes()
        assert raw.isascii(), path.name
        if path.name != f"{GARDEN_CRLF}.mmd":
            assert b"\r" not in raw, f"{path.name}: .gitattributes must keep it LF"


def test_edge_cases_reach_the_index(tmp_path: Path) -> None:
    _, items = _rendered(tmp_path)
    assert sorted(items) == IDS

    # A title containing a colon (its prefix is shaped like an unrecognised label).
    assert items[KITCHEN]["label"] == "Kitchen Orders: From Ticket to Table"
    # A title shaped like a label stays the title, verbatim.
    assert items[LIBRARY]["label"] == "Title: Library Loan Lifecycle"
    # Unbulleted continuation lines with " - description" suffixes are sources, kept whole.
    assert items[LIBRARY]["sources"] == [
        "docs/library/circulation.md",
        "docs/library/holds.md - how the hold queue is ordered",
        "docs/library/overdue.md - when reminder notices go out",
    ]
    # Not shown at exactly the bound.
    assert len(items[BIKE]["not_shown"]) == 400 == MAX_NOT_SHOWN
    # A five-bullet Sources list.
    assert len(items[RECIPE]["sources"]) == 5
    # The CRLF file parses like any other (no stray CR in any field).
    assert "\r" not in json.dumps(items[GARDEN_CRLF])
    # Every fixture carries a non-empty Alt distinct from its title.
    for item in items.values():
        assert item["alt"]
        assert item["alt"].casefold() != item["label"].casefold()


def test_source_lines_have_the_intended_shape() -> None:
    library = _header_contents(LIBRARY)
    assert library[0].startswith("Title: ")
    continuation = [c for c in library if " - " in c and not c.startswith("-")]
    assert len(continuation) == 2, "two UNBULLETED continuation lines with descriptions"
    assert any(c.startswith("Sources: ") and len(c) > len("Sources: ") for c in library)

    recipe = _header_contents(RECIPE)
    start = recipe.index("Sources:")
    assert [c.startswith("- ") for c in recipe[start + 1 : start + 7]] == [True] * 5 + [False]

    assert ":" in _header_contents(KITCHEN)[0]
    for name in IDS:
        assert sum(c.startswith("Alt: ") for c in _header_contents(name)) == 1, name


# --- render ---------------------------------------------------------------------------------


def test_render_contents_lists_exactly_five(tmp_path: Path) -> None:
    readme, items = _rendered(tmp_path)
    entries = _contents_entries(readme)
    assert len(entries) == 5
    titles = {item["label"] for item in items.values()}
    assert {entry[3:].split("](", 1)[0] for entry in entries} == titles


def test_render_sections_carry_alt_and_clean_drawn_from(tmp_path: Path) -> None:
    readme, items = _rendered(tmp_path)
    sections = _sections(readme)
    assert set(sections) == {item["label"] for item in items.values()}
    for item in items.values():
        section = sections[item["label"]]
        assert f"**Alt:** {item['alt']}\n" in section
        drawn = _drawn_from(section)
        assert len(drawn) == len(item["sources"])
        for line in drawn:
            assert line.startswith("- `"), line
            assert "Alt:" not in line
            assert "Not shown:" not in line


def test_render_embeds_intro_document_table(tmp_path: Path) -> None:
    readme, _ = _rendered(tmp_path)
    intro = (REALISTIC / "intro.md").read_bytes().decode("ascii")
    assert "| Document | What it is |" in intro
    for name in IDS:
        assert f"[{name}.mmd]({name}.mmd)" in intro
    assert intro.rstrip("\n") in readme


def test_render_check_is_clean_after_render(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _copy(tmp_path)
    assert main(["render", str(target)]) == EXIT_OK
    capsys.readouterr()
    assert main(["render", str(target), "--check"]) == EXIT_OK
