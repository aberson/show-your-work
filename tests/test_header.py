"""Tests for the single header parser (plan section 3.3) and the constants single source of truth.

Machine-absolute paths are never written literally in this repository (it is public, and the
publication audit flags any drive-letter path in tracked content or history), so the
``absolute-path`` fixture carries a ``<drive>`` placeholder that :func:`_load` materialises.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from show_your_work import constants, header
from show_your_work.constants import (
    CODE_ABS_PATH,
    CODE_ALT_NOT_ASCII,
    CODE_ALT_REPEATS_TITLE,
    CODE_HEADER_BOUND,
    CODE_HEADER_MISSING_ALT,
    CODE_HEADER_MISSING_NOT_SHOWN,
    CODE_HEADER_MISSING_SOURCES,
    CODE_HEADER_MISSING_SUMMARY,
    CODE_HEADER_MISSING_TITLE,
    CODE_HEADER_STRAY_LINE,
    CODE_HEADER_UNKNOWN_LABEL,
    CODE_NON_ASCII,
    FIELD_ALT,
    FIELD_NOT_SHOWN,
    FIELD_SOURCES,
    FIELD_SUMMARY,
    FIELD_TITLE,
    MAX_ALT,
    MAX_NOT_SHOWN,
    MAX_SUMMARY,
    MAX_TITLE,
)
from show_your_work.header import (
    Header,
    HeaderError,
    check_header,
    normalize_newlines,
    parse_header,
)

FIXTURES = Path(__file__).parent / "fixtures" / "bad-headers"
SRC = Path(__file__).resolve().parents[1] / "src" / "show_your_work"

DRIVE_PLACEHOLDER = "<drive>"
DRIVE = "C"  # joined with ":/" only at runtime; never a literal absolute path in the repo

TITLE = "Release Checklist"
SUMMARY = (
    "The four gates a release passes in order - tests, changelog, tag, publish - and the one "
    "shortcut that is forbidden."
)
ALT = (
    "Four boxes in a horizontal row joined by right-pointing arrows, with one red crossed-out "
    "arrow skipping from the first box straight to the last."
)
NOT_SHOWN = "hotfix path, rollback, signing keys"

#: Plan appendix 12.A, verbatim header plus a small body.
VALID = (
    f"%% {TITLE}\n"
    f"%% Summary: {SUMMARY}\n"
    "%% Sources:\n"
    "%%   - docs/release.md\n"
    "%%   - scripts/release.py\n"
    f"%% Not shown: {NOT_SHOWN}\n"
    f"%% Alt: {ALT}\n"
    "\n"
    "flowchart TD\n"
    "    A[Tests] --> B[Changelog]\n"
)

VALID_HEADER = Header(
    title=TITLE,
    summary=SUMMARY,
    sources=("docs/release.md", "scripts/release.py"),
    not_shown=NOT_SHOWN,
    alt=ALT,
)


def _load(name: str) -> str:
    text = (FIXTURES / name).read_bytes().decode("utf-8")
    return text.replace(DRIVE_PLACEHOLDER, DRIVE)


def _error(text: str) -> HeaderError:
    with pytest.raises(HeaderError) as info:
        parse_header(text)
    return info.value


def _with_line(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new, 1)


# --- valid header ---------------------------------------------------------------------------


def test_valid_header_parses_into_header() -> None:
    parsed = parse_header(VALID)
    assert parsed == VALID_HEADER


def test_check_header_returns_instead_of_raising() -> None:
    assert check_header(VALID) == VALID_HEADER
    result = check_header(VALID.replace(f"%% Alt: {ALT}\n", ""))
    assert isinstance(result, HeaderError)
    assert result.code == CODE_HEADER_MISSING_ALT


def test_crlf_copy_parses_identically_to_lf() -> None:
    crlf = VALID.replace("\n", "\r\n")
    assert "\r\n" in crlf
    assert parse_header(crlf) == parse_header(VALID)


def test_leading_bom_parses_like_plain_text() -> None:
    assert parse_header("\ufeff" + VALID) == VALID_HEADER
    crlf = VALID.replace("\n", "\r\n")
    assert parse_header("\ufeff" + crlf) == VALID_HEADER
    # exactly one BOM is stripped; a second one is content
    assert normalize_newlines("\ufeff\ufeffx\r\n") == "\ufeffx\n"


def test_header_ends_at_first_body_line() -> None:
    text = VALID + "%% Owner: not part of the header, it follows the body\n"
    assert parse_header(text) == VALID_HEADER


def test_blank_and_empty_comment_lines_inside_header_are_skipped() -> None:
    text = _with_line(VALID, "%% Sources:\n", "%%\n\n%% Sources:\n")
    assert parse_header(text) == VALID_HEADER


def test_labels_compare_case_insensitively() -> None:
    text = _with_line(VALID, "%% Not shown:", "%% NOT SHOWN:")
    text = _with_line(text, "%% Alt:", "%% alt:")
    assert parse_header(text) == VALID_HEADER


def test_title_with_colon_and_unrecognised_label_shape_stays_title() -> None:
    text = _with_line(VALID, f"%% {TITLE}\n", "%% Title: release checklist: one gated ship\n")
    assert parse_header(text).title == "Title: release checklist: one gated ship"


def test_sources_value_on_label_line_and_bullets_combine() -> None:
    text = _with_line(VALID, "%% Sources:\n", "%% Sources: README.md - Release section\n")
    assert parse_header(text).sources == (
        "README.md - Release section",
        "docs/release.md",
        "scripts/release.py",
    )


def test_url_source_is_never_a_label() -> None:
    text = _with_line(VALID, "%%   - docs/release.md\n", "%% https://example.com/release\n")
    assert parse_header(text).sources == ("https://example.com/release", "scripts/release.py")


def test_bullet_with_colon_is_a_source_not_a_label() -> None:
    text = _with_line(VALID, "%%   - docs/release.md\n", "%%   - Notes: see docs/release.md\n")
    assert parse_header(text).sources[0] == "Notes: see docs/release.md"


# --- bad-headers fixtures -------------------------------------------------------------------

BAD_FIXTURES: dict[str, tuple[str, str]] = {
    "missing-title.mmd": (CODE_HEADER_MISSING_TITLE, FIELD_TITLE),
    "missing-summary.mmd": (CODE_HEADER_MISSING_SUMMARY, FIELD_SUMMARY),
    "missing-sources.mmd": (CODE_HEADER_MISSING_SOURCES, FIELD_SOURCES),
    "missing-not-shown.mmd": (CODE_HEADER_MISSING_NOT_SHOWN, FIELD_NOT_SHOWN),
    "missing-alt.mmd": (CODE_HEADER_MISSING_ALT, FIELD_ALT),
    "absolute-path.mmd": (CODE_ABS_PATH, FIELD_SOURCES),
    "non-ascii.mmd": (CODE_NON_ASCII, FIELD_SUMMARY),
}


@pytest.mark.parametrize(("name", "expected"), sorted(BAD_FIXTURES.items()))
def test_bad_fixture_fails_naming_exact_field(name: str, expected: tuple[str, str]) -> None:
    code, field = expected
    err = _error(_load(name))
    assert [(f.code, f.field) for f in err.findings] == [(code, field)]
    assert repr(field) in err.message


def test_every_bad_fixture_is_covered() -> None:
    on_disk = {p.name for p in FIXTURES.glob("*.mmd")}
    assert on_disk == set(BAD_FIXTURES) | {"alt-after-sources.mmd", "unknown-label.mmd"}


def test_bad_fixtures_are_lf_and_ascii_except_non_ascii() -> None:
    for path in FIXTURES.glob("*.mmd"):
        raw = path.read_bytes()
        assert b"\r" not in raw, path.name
        assert raw.isascii() is (path.name != "non-ascii.mmd"), path.name


def test_absolute_path_fixture_is_not_absolute_on_disk() -> None:
    raw = (FIXTURES / "absolute-path.mmd").read_text(encoding="utf-8")
    assert DRIVE_PLACEHOLDER in raw
    assert parse_header(raw).sources[1].startswith(DRIVE_PLACEHOLDER)


def test_unknown_label_fixture_raises_naming_the_label() -> None:
    err = _error(_load("unknown-label.mmd"))
    assert err.codes == (CODE_HEADER_UNKNOWN_LABEL,)
    assert "'Owner'" in err.message
    assert err.findings[0].line == 6


def test_alt_after_sources_is_not_swallowed_as_a_source() -> None:
    """Sticky-state regression: an Alt: right after Sources: must clear the sources state."""
    text = _load("alt-after-sources.mmd")
    alt_line = f"%% Alt: {ALT}\n"
    lines = text.split("\n")
    assert lines.index(alt_line.rstrip("\n")) == lines.index("%%   - scripts/release.py") + 1

    parsed = parse_header(text)
    assert parsed.alt == ALT
    assert parsed.sources == ("docs/release.md", "scripts/release.py")

    # Same header with the Alt line moved out from under Sources: -> identical sources.
    without = text.replace(alt_line, "", 1)
    relocated = _with_line(without, "%% Sources:\n", alt_line + "%% Sources:\n")
    assert parse_header(relocated) == parsed


# --- continuation and stray lines -----------------------------------------------------------


def test_unbulleted_line_in_sources_is_a_source() -> None:
    text = _with_line(
        VALID,
        "%%   - scripts/release.py\n",
        "%%   - scripts/release.py\n%% docs/changelog.md - Release notes section\n",
    )
    assert parse_header(text).sources == (
        "docs/release.md",
        "scripts/release.py",
        "docs/changelog.md - Release notes section",
    )


def test_stray_non_label_line_outside_sources_fails() -> None:
    text = _with_line(VALID, "%% Sources:\n", "%% this line is not a label\n%% Sources:\n")
    err = _error(text)
    assert err.codes == (CODE_HEADER_STRAY_LINE,)
    assert err.findings[0].line == 3
    assert "this line is not a label" in err.message


def test_label_after_sources_clears_state_for_unbulleted_lines() -> None:
    """Every label clears "in sources": an unbulleted line after Alt: is stray, not a source."""
    text = _with_line(VALID, f"%% Alt: {ALT}\n", f"%% Alt: {ALT}\n%% docs/extra.md\n")
    text = _with_line(text, f"%% Not shown: {NOT_SHOWN}\n", "")
    text = _with_line(text, "%% Sources:\n", f"%% Not shown: {NOT_SHOWN}\n%% Sources:\n")
    assert _error(text).codes == (CODE_HEADER_STRAY_LINE,)


def test_bullet_outside_sources_is_stray() -> None:
    text = _with_line(VALID, f"%% Alt: {ALT}\n", f"%% Alt: {ALT}\n%%   - docs/extra.md\n")
    assert _error(text).codes == (CODE_HEADER_STRAY_LINE,)


def test_duplicate_label_fails() -> None:
    text = _with_line(VALID, f"%% Alt: {ALT}\n", f"%% Alt: {ALT}\n%% Summary: again\n")
    err = _error(text)
    assert err.codes == (CODE_HEADER_STRAY_LINE,)
    assert "duplicate" in err.message


def test_empty_value_counts_as_missing() -> None:
    text = _with_line(VALID, f"%% Summary: {SUMMARY}\n", "%% Summary:\n")
    assert _error(text).codes == (CODE_HEADER_MISSING_SUMMARY,)


def test_no_header_reports_every_field_missing() -> None:
    err = _error("flowchart TD\n    A --> B\n")
    assert err.codes == (
        CODE_HEADER_MISSING_TITLE,
        CODE_HEADER_MISSING_SUMMARY,
        CODE_HEADER_MISSING_SOURCES,
        CODE_HEADER_MISSING_NOT_SHOWN,
        CODE_HEADER_MISSING_ALT,
    )


# --- bounds, Alt rules, absolute paths ------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "old", "bound"),
    [
        (FIELD_TITLE, f"%% {TITLE}\n", MAX_TITLE),
        (FIELD_SUMMARY, f"%% Summary: {SUMMARY}\n", MAX_SUMMARY),
        (FIELD_NOT_SHOWN, f"%% Not shown: {NOT_SHOWN}\n", MAX_NOT_SHOWN),
        (FIELD_ALT, f"%% Alt: {ALT}\n", MAX_ALT),
    ],
)
def test_bounds_inclusive_then_fail(field: str, old: str, bound: int) -> None:
    prefix = old[: old.index(":") + 2] if field != FIELD_TITLE else "%% "
    at_bound = _with_line(VALID, old, prefix + "x" * bound + "\n")
    assert len(getattr(parse_header(at_bound), field)) == bound
    over = _with_line(VALID, old, prefix + "x" * (bound + 1) + "\n")
    err = _error(over)
    assert [(f.code, f.field) for f in err.findings] == [(CODE_HEADER_BOUND, field)]


def test_alt_must_be_ascii() -> None:
    text = _with_line(VALID, f"%% Alt: {ALT}\n", "%% Alt: Four boxes \u2192 arrows\n")
    assert _error(text).codes == (CODE_ALT_NOT_ASCII,)


def test_alt_rejects_control_characters() -> None:
    text = _with_line(VALID, f"%% Alt: {ALT}\n", "%% Alt: Four boxes\tjoined by arrows\n")
    assert _error(text).codes == (CODE_ALT_NOT_ASCII,)


def test_alt_must_not_repeat_title() -> None:
    text = _with_line(VALID, f"%% Alt: {ALT}\n", f"%% Alt: {TITLE.upper()}\n")
    assert _error(text).codes == (CODE_ALT_REPEATS_TITLE,)


@pytest.mark.parametrize("sep", ["/", "\\"])
def test_absolute_source_rejected(sep: str) -> None:
    entry = DRIVE + ":" + sep + "work" + sep + "release.md"
    text = _with_line(VALID, "%%   - docs/release.md\n", f"%%   - {entry}\n")
    err = _error(text)
    assert [(f.code, f.field) for f in err.findings] == [(CODE_ABS_PATH, FIELD_SOURCES)]


def test_header_error_requires_findings() -> None:
    with pytest.raises(ValueError):
        HeaderError([])


# --- single source of truth for data-shape constants ----------------------------------------


def _constant_names() -> set[str]:
    tree = ast.parse((SRC / "constants.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        names.update(t.id for t in targets if isinstance(t, ast.Name) and t.id.isupper())
    return names


def _module_level_redefinitions(source: str, names: set[str]) -> list[str]:
    tree = ast.parse(source)
    hits: list[str] = []
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
            targets = [node.target]
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name) and sub.id in names:
                    hits.append(sub.id)
    return hits


def test_constant_names_cover_budget_bounds_and_field_names() -> None:
    names = _constant_names()
    expected = {
        "MAX_NODES",
        "MAX_EDGES",
        "MAX_NODE_LABEL",
        "MAX_EDGE_LABEL",
        "MAX_SUBGRAPHS",
        "MAX_TITLE",
        "MAX_SUMMARY",
        "MAX_NOT_SHOWN",
        "MAX_ALT",
        "HEADER_FIELDS",
        "HEADER_LABELS",
        "HEADER_BOUNDS",
        "FIELD_TITLE",
        "FIELD_ALT",
        "LABEL_RE",
        "ID_RE",
        "GARBAGE",
    }
    assert expected <= names


def test_no_module_redefines_a_constant() -> None:
    names = _constant_names()
    modules = sorted(p for p in SRC.glob("*.py") if p.name != "constants.py")
    assert modules, "expected package modules to scan"
    offenders = {
        p.name: hits
        for p in modules
        if (hits := _module_level_redefinitions(p.read_text(encoding="utf-8"), names))
    }
    assert offenders == {}


def test_redefinition_scan_can_fail() -> None:
    """The guard is not vacuous: a re-declared budget/bound/field constant is caught."""
    names = _constant_names()
    source = 'MAX_NODES = 14\nMAX_ALT: int = 240\nHEADER_FIELDS = ("title",)\nOTHER = 1\n'
    assert _module_level_redefinitions(source, names) == ["MAX_NODES", "MAX_ALT", "HEADER_FIELDS"]


def test_header_fields_identity_is_shared() -> None:
    assert header.HEADER_FIELDS is constants.HEADER_FIELDS
    assert constants.HEADER_FIELDS == (
        FIELD_TITLE,
        FIELD_SUMMARY,
        FIELD_SOURCES,
        FIELD_NOT_SHOWN,
        FIELD_ALT,
    )
    assert tuple(f.name for f in Header.__dataclass_fields__.values()) == header.HEADER_FIELDS
