"""Layout sidecar contract (plan Phase 2 section 2.4).

Every Done-when of Step 8 runs through the production caller ``cli.main``; the remaining tests pin
each 2.4 rule and its boundary through :func:`show_your_work.layout.layout_findings` /
:func:`show_your_work.layout.parse_layout`. Fixtures are synthetic. An invalid or bound fixture is
only a sidecar: the test copies ``valid/flow.mmd`` beside it in a tmp folder (every bound fixture
pins that file's hash, so the one rule it breaks is the only finding). The last tests pin the
README "Layout sidecar" section (Step 11): its example passes ``check`` beside the ``init`` template
diagram, equals this repo's dogfooded sidecar, and the section names every ``layout-*`` code.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import string
import sys
from pathlib import Path
from typing import Any

import pytest

from show_your_work import header, render, renderer
from show_your_work.checks import GateError, check_dir, read_layout, read_source
from show_your_work.cli import main
from show_your_work.constants import (
    CODE_EXCALIDRAW_ORPHAN,
    CODE_LAYOUT_BOUND,
    CODE_LAYOUT_INVALID,
    CODE_LAYOUT_ORPHAN,
    CODE_LAYOUT_STALE,
    EXAMPLE_NAME,
    EXIT_FINDINGS,
    EXIT_GATE,
    EXIT_OK,
    FINDING_CODES,
    LANE_LABEL_CHARS_TEXT,
    LANE_LABEL_RE,
    LAYOUT_SCHEMA,
    LAYOUT_SUFFIX,
    MAX_ALT,
    MAX_BRANCH_LABEL,
    MAX_LANE_LABEL,
    MAX_LANE_NODES,
    MAX_LAYOUT_BYTES,
    MAX_LAYOUT_DETAIL,
    MAX_LAYOUT_INT_DIGITS,
    MAX_LAYOUT_LANES,
    MAX_NODE_NAME,
    MAX_NODE_SUB,
    MAX_REJOIN_LABEL,
    MIN_LAYOUT_LANES,
    SOURCE_SUFFIX,
)
from show_your_work.layout import Layout, LayoutError, layout_findings, parse_layout
from tests.fakes import (
    OVER_COUNTS,
    FakeRenderer,
    finding_codes,
    json_document,
    rows_by_file,
)

FIXTURES = Path(__file__).parent / "fixtures"
LAYOUT = FIXTURES / "layout"
VALID = LAYOUT / "valid"
INVALID = LAYOUT / "invalid"
BOUND = LAYOUT / "bound"
ORPHAN = LAYOUT / "orphan"
PHASE1 = LAYOUT / "phase1"
REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "templates" / "workflows"
DOGFOOD = REPO / "docs" / "workflows"
#: The project README, whose sidecar section carries the documented example (plan section 2.6).
README = REPO / "README.md"
README_SECTION_ANCHOR = "### Layout sidecar"  # anchor: the README heading of that section
#: The sidecar of the ``init`` template diagram, which the README example pins.
EXAMPLE_SIDECAR = EXAMPLE_NAME.removesuffix(SOURCE_SUFFIX) + LAYOUT_SUFFIX

FLOW_MMD = (VALID / "flow.mmd").read_bytes()
FLOW_LAYOUT: dict[str, Any] = json.loads((VALID / "flow.layout.json").read_bytes())

#: Every ``invalid/`` fixture (all ``layout-invalid``) and every ``bound/`` one (``layout-bound``).
INVALID_CASES = (
    "duplicate-key",
    "nan",
    "unknown-key",
    "bool-as-int",
    "non-utf8",
    "digits-5000",
)
BOUND_CASES = (
    "name-23",
    "branch-label-15",
    "non-ascii-escape",
    "newline-escape",
    "lanes-7",
    "loop-non-adjacent",
    "side-first",
    "in-on-side",
    "rejoin-non-last",
)


def _with_sidecar(tmp_path: Path, sidecar: Path) -> Path:
    """A tmp folder with ``valid/flow.mmd`` and ``sidecar`` as its ``flow.layout.json``."""
    folder = tmp_path / "workflows"
    folder.mkdir()
    (folder / "flow.mmd").write_bytes(FLOW_MMD)
    shutil.copyfile(sidecar, folder / "flow.layout.json")
    return folder


def _valid_copy(tmp_path: Path) -> Path:
    folder = tmp_path / "workflows"
    shutil.copytree(VALID, folder)
    return folder


def _findings(doc: dict[str, Any], source: bytes = FLOW_MMD) -> list[tuple[str, str]]:
    raw = json.dumps(doc).encode("ascii")
    return [(f.code, f.message) for f in layout_findings("flow", raw, source)]


def _rows(capsys: pytest.CaptureFixture[str]) -> dict[str, dict[str, Any]]:
    return rows_by_file(json_document(capsys.readouterr().out))


def _mutated(**changes: Any) -> dict[str, Any]:
    doc = copy.deepcopy(FLOW_LAYOUT)
    doc.update(changes)
    return doc


# --- Done-when, through cli.main --------------------------------------------------------------


def test_valid_fixture_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(VALID), "--json"]) == EXIT_OK
    rows = _rows(capsys)
    assert set(rows) == {"feedback.mmd", "flow.mmd", "plain.mmd"}
    assert all(row["pass"] for row in rows.values())


def test_rejoin_from_lane_3_back_to_lane_0_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Section 2.4: a feedback loop (playtest -> idea) runs right to left and is still valid."""
    doc = json.loads((VALID / "feedback.layout.json").read_bytes())
    rejoin = doc["rejoin"]
    assert (rejoin["from_lane"], rejoin["to_lane"]) == (3, 0)
    folder = tmp_path / "workflows"
    folder.mkdir()
    for name in ("feedback.mmd", "feedback.layout.json"):
        shutil.copyfile(VALID / name, folder / name)
    assert main(["check", str(folder), "--json"]) == EXIT_OK
    assert [row["findings"] for row in json_document(capsys.readouterr().out)["files"]] == [[]]


@pytest.mark.parametrize("case", INVALID_CASES)
def test_invalid_fixture_exits_1_with_layout_invalid_only(
    case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _with_sidecar(tmp_path, INVALID / f"{case}.layout.json")
    assert main(["check", str(folder), "--json"]) == EXIT_FINDINGS
    document = json_document(capsys.readouterr().out)
    rows = rows_by_file(document)
    assert set(rows) == {"flow.mmd"}
    assert finding_codes(rows["flow.mmd"]) == [CODE_LAYOUT_INVALID]
    assert rows["flow.mmd"]["findings"][0]["message"].startswith("'flow.layout.json': ")
    assert document["summary"]["total"] == 1


@pytest.mark.parametrize("case", BOUND_CASES)
def test_bound_fixture_exits_1_with_layout_bound_only(
    case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _with_sidecar(tmp_path, BOUND / f"{case}.layout.json")
    assert main(["check", str(folder), "--json"]) == EXIT_FINDINGS
    rows = _rows(capsys)
    assert set(rows) == {"flow.mmd"}
    assert finding_codes(rows["flow.mmd"]) == [CODE_LAYOUT_BOUND]


def test_every_invalid_and_bound_fixture_is_covered() -> None:
    assert sorted(p.name for p in INVALID.iterdir()) == sorted(
        f"{c}.layout.json" for c in INVALID_CASES
    )
    assert sorted(p.name for p in BOUND.iterdir()) == sorted(
        f"{c}.layout.json" for c in BOUND_CASES
    )


def _check_with_in_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], token: str
) -> tuple[int, list[dict[str, Any]]]:
    """``check --json`` on ``flow`` whose first lane's ``in`` is the raw JSON ``token``."""
    doc = copy.deepcopy(FLOW_LAYOUT)
    doc["lanes"][0]["in"] = 0
    text = json.dumps(doc).replace('"in": 0', f'"in": {token}', 1)
    sidecar = tmp_path / "sidecar.layout.json"
    sidecar.write_bytes(text.encode("ascii"))
    code = main(["check", str(_with_sidecar(tmp_path, sidecar)), "--json"])
    rows = _rows(capsys)
    assert set(rows) == {"flow.mmd"}
    findings: list[dict[str, Any]] = rows["flow.mmd"]["findings"]
    return code, findings


@pytest.mark.parametrize("digits", [MAX_LAYOUT_INT_DIGITS + 1, 100, 4290])
def test_oversized_integer_is_invalid_with_a_bounded_message(
    digits: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Under the default 4300-digit int-string limit too: the token is refused unconverted."""
    code, findings = _check_with_in_token(tmp_path, capsys, "9" * digits)
    assert code == EXIT_FINDINGS
    assert [f["code"] for f in findings] == [CODE_LAYOUT_INVALID]
    message = findings[0]["message"]
    assert f"{digits} digits" in message
    assert len(message) < MAX_LAYOUT_DETAIL


@pytest.mark.parametrize("token", ["9" * MAX_LAYOUT_INT_DIGITS, "-" + "9" * MAX_LAYOUT_INT_DIGITS])
def test_integer_at_the_digit_bound_parses_and_is_only_out_of_range(
    token: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, findings = _check_with_in_token(tmp_path, capsys, token)
    assert code == EXIT_FINDINGS
    assert [f["code"] for f in findings] == [CODE_LAYOUT_BOUND]
    assert f"in = {token} is not a node of the lane" in findings[0]["message"]


def test_digits_5000_is_invalid_whatever_the_int_string_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``PYTHONINTMAXSTRDIGITS=0`` (no limit) does not change the 5000-digit result."""
    previous = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(0)
    try:
        folder = _with_sidecar(tmp_path, INVALID / "digits-5000.layout.json")
        assert main(["check", str(folder), "--json"]) == EXIT_FINDINGS
    finally:
        sys.set_int_max_str_digits(previous)
    findings = _rows(capsys)["flow.mmd"]["findings"]
    assert [f["code"] for f in findings] == [CODE_LAYOUT_INVALID]
    assert f"allowed at most {MAX_LAYOUT_INT_DIGITS}" in findings[0]["message"]


def test_sidecar_beside_an_excalidraw_drawing_is_bound(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _valid_copy(tmp_path)
    (folder / "flow.excalidraw.svg").write_bytes(b"<svg/>")
    assert main(["check", str(folder), "--json"]) == EXIT_FINDINGS
    rows = _rows(capsys)
    assert finding_codes(rows["flow.mmd"]) == [CODE_LAYOUT_BOUND]
    assert "flow.excalidraw.svg" in rows["flow.mmd"]["findings"][0]["message"]
    assert rows["feedback.mmd"]["pass"] and rows["plain.mmd"]["pass"]


def test_orphan_sidecar_gets_its_own_row(capsys: pytest.CaptureFixture[str]) -> None:
    assert sorted(p.name for p in ORPHAN.iterdir()) == ["a.mmd", "b.layout.json"]
    assert main(["check", str(ORPHAN), "--json"]) == EXIT_FINDINGS
    document = json_document(capsys.readouterr().out)
    rows = rows_by_file(document)
    assert list(rows) == ["a.mmd", "b.layout.json"]
    assert rows["a.mmd"]["pass"] is True
    orphan = rows["b.layout.json"]
    assert (orphan["id"], orphan["nodes"], orphan["edges"]) == ("b", None, None)
    assert finding_codes(orphan) == [CODE_LAYOUT_ORPHAN]
    summary = document["summary"]
    assert (summary["total"], summary["passed"], summary["failed"]) == (2, 1, 1)


def test_orphan_row_in_the_human_table(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(ORPHAN)]) == EXIT_FINDINGS
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].split() == ["a.mmd", "-", "-", "pass"]
    assert lines[2].split() == ["b.layout.json", "-", "-", "FAIL"]
    assert lines[3] == (
        f"    {CODE_LAYOUT_ORPHAN}: 'b.layout.json' has no sibling 'b.mmd'; author in mermaid first"
    )
    assert lines[-1].startswith("1/2 pass")


def test_one_byte_change_is_stale_naming_the_expected_hash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _valid_copy(tmp_path)
    changed = FLOW_MMD.replace(b"Take order", b"Take order!", 1)
    assert len(changed) == len(FLOW_MMD) + 1
    (folder / "flow.mmd").write_bytes(changed)
    assert main(["check", str(folder), "--json"]) == EXIT_FINDINGS
    rows = _rows(capsys)
    assert finding_codes(rows["flow.mmd"]) == [CODE_LAYOUT_STALE]
    expected = header.sha256_lf(changed)
    assert expected != FLOW_LAYOUT["source_sha256"]
    assert expected in rows["flow.mmd"]["findings"][0]["message"]
    assert "flow.layout.json" not in rows  # stale attaches to the .mmd row


def test_crlf_copy_of_the_mmd_stays_passing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _valid_copy(tmp_path)
    crlf = FLOW_MMD.replace(b"\n", b"\r\n")
    assert crlf != FLOW_MMD
    (folder / "flow.mmd").write_bytes(crlf)
    assert main(["check", str(folder), "--json"]) == EXIT_OK
    assert all(row["pass"] for row in json_document(capsys.readouterr().out)["files"])


def test_oversized_sidecar_exits_2_naming_the_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _valid_copy(tmp_path)
    raw = (VALID / "flow.layout.json").read_bytes()
    big = raw + b" " * (MAX_LAYOUT_BYTES + 1 - len(raw))
    assert len(big) == MAX_LAYOUT_BYTES + 1
    (folder / "flow.layout.json").write_bytes(big)
    assert main(["check", str(folder)]) == EXIT_GATE
    err = capsys.readouterr().err
    assert "(64 KiB)" in err and "flow.layout.json" in err  # anchor: the derivation must spell this


def test_oversized_orphan_sidecar_exits_2(tmp_path: Path) -> None:
    folder = _valid_copy(tmp_path)
    (folder / "ghost.layout.json").write_bytes(b" " * (MAX_LAYOUT_BYTES + 1))
    assert main(["check", str(folder)]) == EXIT_GATE


def test_sidecar_at_the_size_limit_is_read(tmp_path: Path) -> None:
    folder = _valid_copy(tmp_path)
    raw = (VALID / "flow.layout.json").read_bytes()
    (folder / "flow.layout.json").write_bytes(raw + b" " * (MAX_LAYOUT_BYTES - len(raw)))
    assert (folder / "flow.layout.json").stat().st_size == MAX_LAYOUT_BYTES
    assert main(["check", str(folder)]) == EXIT_OK


def test_symlinked_sidecar_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deterministic on every host: ``is_symlink`` is faked for the sidecar only."""
    folder = _valid_copy(tmp_path)
    real = Path.is_symlink
    monkeypatch.setattr(
        Path, "is_symlink", lambda self: self.name == "flow.layout.json" or real(self)
    )
    assert main(["check", str(folder)]) == EXIT_GATE
    assert "refusing to read symlinked layout sidecar" in capsys.readouterr().err


def test_real_symlinked_sidecar_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = _valid_copy(tmp_path)
    outside = tmp_path / "outside.json"
    shutil.move(folder / "flow.layout.json", outside)
    try:
        os.symlink(outside, folder / "flow.layout.json")
    except (OSError, NotImplementedError) as exc:  # Windows without symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    assert main(["check", str(folder)]) == EXIT_GATE
    assert "refusing to read symlinked layout sidecar" in capsys.readouterr().err


def test_sha256_lf_has_one_source_of_truth() -> None:
    assert header.sha256_lf is render.sha256_lf
    assert not hasattr(render, "_sha256_lf")
    assert header.sha256_lf(b"a\r\nb\n") == header.sha256_lf(b"a\nb\n")


# --- a folder without sidecars reports byte-identically to Phase 1 --------------------------


def _phase1_folder(folder: Path) -> None:
    """The sidecar-less folder the ``phase1/`` goldens were recorded on (with the Phase 1 code):
    passing, failing, CRLF and non-ASCII sources, a matched and an orphan excalidraw drawing, a
    nested ``rasters/*.layout.json`` (never discovered) and an unrelated file."""
    for sub in ("good", "realistic", "over-budget"):
        for path in sorted((FIXTURES / sub).glob("*.mmd")):
            shutil.copyfile(path, folder / path.name)
    for name in ("missing-alt.mmd", "non-ascii.mmd"):
        shutil.copyfile(FIXTURES / "bad-headers" / name, folder / name)
    (folder / "minimal.excalidraw.svg").write_bytes(b"<svg/>")
    (folder / "ghost.excalidraw.svg").write_bytes(b"<svg/>")
    (folder / "rasters").mkdir()
    (folder / "rasters" / "nested.layout.json").write_bytes(b"{}")
    (folder / "notes.txt").write_bytes(b"not a diagram")


@pytest.mark.parametrize(
    ("golden", "flags"),
    [
        ("check", []),
        ("check-json", ["--json"]),
        ("check-render", ["--render"]),
        ("check-render-json", ["--render", "--json"]),
    ],
)
def test_sidecar_less_folder_reports_byte_identically_to_phase1(
    golden: str,
    flags: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = tmp_path / "workflows"
    folder.mkdir()
    _phase1_folder(folder)
    monkeypatch.setattr(renderer, "load", lambda: FakeRenderer(OVER_COUNTS))
    code = main(["check", str(folder), *flags])
    captured = capsys.readouterr()
    text = f"exit {code}\n--- stdout\n{captured.out}--- stderr\n{captured.err}"
    text = text.replace(folder.resolve().as_posix(), "<TARGET>")
    assert text.encode("utf-8") == (PHASE1 / f"{golden}.txt").read_bytes()


# --- parse rules (layout-invalid) -------------------------------------------------------------


def test_valid_sidecar_parses_into_typed_layout() -> None:
    parsed = parse_layout((VALID / "flow.layout.json").read_bytes())
    assert isinstance(parsed, Layout)
    assert parsed.schema == LAYOUT_SCHEMA
    assert [lane.label for lane in parsed.lanes] == ["PLAN", "BUILD + SHIP"]
    assert parsed.lanes[0].nodes[2].side is True
    assert (parsed.loops[0].from_node, parsed.loops[0].to_node) == (1, 0)
    assert parsed.rejoin is not None and parsed.rejoin.label == "stock arrives"


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b'"text"',
        b"",
        b"\xef\xbb\xbf{}",  # a UTF-8 BOM is not JSON
        b'{"a": Infinity}',
        b'{"a": -Infinity}',
        b"[" * 40_000 + b"]" * 40_000,  # RecursionError inside the C scanner
        b"{" + b'"a":{' * 30_000 + b"}" * 30_001,
    ],
    ids=[
        "array",
        "string",
        "empty",
        "bom",
        "infinity",
        "minus-infinity",
        "deep-array",
        "deep-object",
    ],
)
def test_unparseable_documents_are_invalid(raw: bytes) -> None:
    with pytest.raises(LayoutError):
        parse_layout(raw)
    findings = layout_findings("flow", raw, FLOW_MMD)
    assert [f.code for f in findings] == [CODE_LAYOUT_INVALID]


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "show-your-work.layout.v2"},
        {"schema": 1},
        {"source_sha256": FLOW_LAYOUT["source_sha256"].upper()},
        {"source_sha256": FLOW_LAYOUT["source_sha256"][:-1]},
        {"source_sha256": FLOW_LAYOUT["source_sha256"] + "\n"},
        {"alt": None},
        {"lanes": {}},
        {"loops": None},
        {"rejoin": []},
        {"rejoin": {"from_lane": 0}},
    ],
)
def test_wrong_schema_hash_shape_or_type_is_invalid(change: dict[str, Any]) -> None:
    assert [c for c, _ in _findings(_mutated(**change))] == [CODE_LAYOUT_INVALID]


def test_missing_key_is_invalid() -> None:
    for key in FLOW_LAYOUT:
        doc = copy.deepcopy(FLOW_LAYOUT)
        del doc[key]
        findings = _findings(doc)
        assert [c for c, _ in findings] == [CODE_LAYOUT_INVALID], key
        assert repr(key) in findings[0][1]
    node = copy.deepcopy(FLOW_LAYOUT)
    del node["lanes"][0]["nodes"][0]["sub"]
    assert [c for c, _ in _findings(node)] == [CODE_LAYOUT_INVALID]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("lanes", 0, "out"), True),
        (("lanes", 0, "in"), 0.0),
        (("lanes", 0, "nodes", 0, "side"), 0),
        (("lanes", 0, "nodes", 0, "name"), 7),
        (("loops", 0, "lane"), False),
        (("rejoin", "to_node"), "2"),
        (("lanes", 1, "nodes"), "Pick items"),
    ],
)
def test_wrong_nested_type_is_invalid(path: tuple[str | int, ...], value: object) -> None:
    doc = copy.deepcopy(FLOW_LAYOUT)
    target: Any = doc
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    assert [c for c, _ in _findings(doc)] == [CODE_LAYOUT_INVALID]


def test_unknown_nested_key_is_invalid() -> None:
    doc = copy.deepcopy(FLOW_LAYOUT)
    doc["lanes"][0]["nodes"][0]["colour"] = "red"
    findings = _findings(doc)
    assert [c for c, _ in findings] == [CODE_LAYOUT_INVALID]
    assert "'colour'" in findings[0][1]


def test_invalid_detail_is_bounded() -> None:
    doc = _mutated(**{"k" * 5000: 1})
    message = _findings(doc)[0][1]
    assert len(message) < MAX_LAYOUT_DETAIL + 100
    assert message.endswith("...")


def test_invalid_stops_before_bound_and_stale() -> None:
    doc = _mutated(alt="", source_sha256="0" * 64, extra=1)
    assert [c for c, _ in _findings(doc)] == [CODE_LAYOUT_INVALID]


# --- bound rules (layout-bound), inclusive at every limit ------------------------------------


def _node(name: str = "Step", sub: str = "", side: bool = False, label: str = "") -> dict[str, Any]:
    return {"name": name, "sub": sub, "side": side, "branch_label": label}


def _lane(label: str = "LANE", nodes: int = 1, **kw: Any) -> dict[str, Any]:
    lane: dict[str, Any] = {"label": label, "in": 0, "out": 0}
    lane["nodes"] = [_node() for _ in range(nodes)]
    lane.update(kw)
    return lane


def _plain(lanes: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema": LAYOUT_SCHEMA,
        "source_sha256": FLOW_LAYOUT["source_sha256"],
        "alt": "Plain drawing.",
        "lanes": lanes,
        "loops": [],
        "rejoin": None,
    }
    doc.update(kw)
    return doc


def _bound_count(doc: dict[str, Any]) -> int:
    codes = [c for c, _ in _findings(doc)]
    assert set(codes) <= {CODE_LAYOUT_BOUND}, codes
    return len(codes)


def test_plain_two_lane_layout_passes() -> None:
    assert _findings(_plain([_lane(), _lane()])) == []


def test_alt_length_is_inclusive_at_max_and_empty_fails() -> None:
    assert _bound_count(_plain([_lane(), _lane()], alt="a" * MAX_ALT)) == 0
    assert _bound_count(_plain([_lane(), _lane()], alt="a" * (MAX_ALT + 1))) == 1
    assert _bound_count(_plain([_lane(), _lane()], alt="")) == 1


def test_lane_count_is_inclusive_at_both_limits() -> None:
    assert _bound_count(_plain([_lane()] * MAX_LAYOUT_LANES)) == 0
    assert _bound_count(_plain([_lane()] * (MAX_LAYOUT_LANES + 1))) == 1
    assert _bound_count(_plain([_lane()] * (MIN_LAYOUT_LANES - 1))) == 1


def test_node_count_is_inclusive_at_max_and_empty_fails() -> None:
    assert _bound_count(_plain([_lane(nodes=MAX_LANE_NODES), _lane()])) == 0
    assert _bound_count(_plain([_lane(nodes=MAX_LANE_NODES + 1), _lane()])) == 1
    empty = _lane()
    empty["nodes"] = []
    assert _bound_count(_plain([empty, _lane()])) >= 1  # no nodes, so in/out name nothing too


@pytest.mark.parametrize(
    ("label", "ok"),
    [
        ("A" * MAX_LANE_LABEL, True),
        ("A" * (MAX_LANE_LABEL + 1), False),
        ("", False),
        ("BUILD + TEST/QA-2", False),  # 17 chars
        ("BUILD+TEST/QA-2", True),
        ("Build", False),
        ("BUILD_TEST", False),
        ("BUILDÉ", False),
    ],
)
def test_lane_label_bounds_and_alphabet(label: str, ok: bool) -> None:
    assert _bound_count(_plain([_lane(label), _lane()])) == (0 if ok else 1)


#: What each phrase of ``LANE_LABEL_CHARS_TEXT`` names; a quoted ``'c'`` names ``c`` itself.
LANE_LABEL_PHRASES = {
    "uppercase letters": string.ascii_uppercase,
    "digits": string.digits,
    "space": " ",
}


def test_lane_label_wording_describes_lane_label_re_exactly() -> None:
    named = ""
    for phrase in LANE_LABEL_CHARS_TEXT.split(", "):
        quoted = len(phrase) == 3 and phrase[0] == phrase[2] == "'"
        named += phrase[1] if quoted else LANE_LABEL_PHRASES[phrase]
    allowed = {chr(c) for c in range(0x250) if LANE_LABEL_RE.fullmatch(chr(c))}
    assert allowed == set(named)
    for excluded in ("a", "_", ".", "\\", "É", "\t"):
        assert LANE_LABEL_RE.fullmatch(excluded) is None


def test_lane_label_message_uses_the_wording() -> None:
    messages = [message for _, message in _findings(_plain([_lane("Build"), _lane()]))]
    assert any(m.endswith(f"may hold only {LANE_LABEL_CHARS_TEXT}") for m in messages), messages


def test_name_sub_and_branch_label_bounds() -> None:
    lane = _lane(nodes=2)
    lane["nodes"] = [
        _node("n" * MAX_NODE_NAME, "s" * MAX_NODE_SUB),
        _node("Side", "", True, "b" * MAX_BRANCH_LABEL),
    ]
    assert _bound_count(_plain([lane, _lane()])) == 0
    lane["nodes"] = [
        _node("n" * (MAX_NODE_NAME + 1), "s" * (MAX_NODE_SUB + 1)),
        _node("Side", "", True, ""),
    ]
    assert _bound_count(_plain([lane, _lane()])) == 3
    lane["nodes"] = [_node("", ""), _node("Next", "", False, "label")]
    assert _bound_count(_plain([lane, _lane()])) == 2  # empty name; label on a non-side node


@pytest.mark.parametrize("bad", ["café", "tab\there", "line\nbreak", "del\x7f", "\x00"])
def test_every_string_must_be_printable_ascii(bad: str) -> None:
    assert _bound_count(_mutated(alt=bad)) == 1
    doc = copy.deepcopy(FLOW_LAYOUT)
    doc["lanes"][0]["nodes"][0]["sub"] = bad
    assert _bound_count(doc) == 1
    doc = copy.deepcopy(FLOW_LAYOUT)
    doc["rejoin"]["label"] = bad
    assert _bound_count(doc) == 1
    doc = copy.deepcopy(FLOW_LAYOUT)
    doc["lanes"][0]["label"] = bad
    assert _bound_count(doc) == 1


def test_printable_ascii_edges_pass() -> None:
    assert _bound_count(_mutated(alt=" ~ <&\"'> ")) == 0


def test_side_node_must_be_last_and_never_first() -> None:
    lane = _lane(nodes=3)
    lane["nodes"][1] = _node("Mid", "", True, "branch")  # side but not last
    assert _bound_count(_plain([lane, _lane()])) == 1
    lane = _lane(nodes=2)
    lane["in"] = lane["out"] = 1
    lane["nodes"][0] = _node("First", "", True, "branch")  # node 0 is never side
    assert _bound_count(_plain([lane, _lane()])) == 1


@pytest.mark.parametrize(("key", "index"), [("in", 1), ("out", 1), ("in", 2), ("out", -1)])
def test_in_and_out_must_name_a_non_side_node(key: str, index: int) -> None:
    lane = _lane(nodes=2)
    lane["nodes"][1] = _node("Side", "", True, "branch")
    lane[key] = index
    assert _bound_count(_plain([lane, _lane()])) == 1


def test_loop_must_be_adjacent_upward_one_per_lane_off_side_nodes() -> None:
    two = _lane(nodes=3)
    ok = {"lane": 0, "from": 2, "to": 1}
    assert _bound_count(_plain([two, _lane()], loops=[ok])) == 0
    assert _bound_count(_plain([two, _lane()], loops=[ok, ok])) == 1  # one per lane
    assert _bound_count(_plain([two, _lane()], loops=[{"lane": 0, "from": 1, "to": 1}])) == 1
    assert _bound_count(_plain([two, _lane()], loops=[{"lane": 0, "from": 0, "to": 1}])) == 1
    assert _bound_count(_plain([two, _lane()], loops=[{"lane": 0, "from": 3, "to": 2}])) == 1
    assert _bound_count(_plain([two, _lane()], loops=[{"lane": 2, "from": 1, "to": 0}])) == 1
    side = _lane(nodes=2)
    side["nodes"][1] = _node("Side", "", True, "branch")
    assert _bound_count(_plain([side, _lane()], loops=[{"lane": 0, "from": 1, "to": 0}])) == 1


def _rejoin(**kw: Any) -> dict[str, Any]:
    rejoin = {"from_lane": 0, "from_node": 1, "to_lane": 1, "to_node": 0, "label": "back"}
    rejoin.update(kw)
    return rejoin


def test_rejoin_joins_last_nodes_of_two_lanes_in_either_direction() -> None:
    lanes = [_lane(nodes=2), _lane()]
    assert _bound_count(_plain(lanes, rejoin=_rejoin())) == 0
    assert _bound_count(_plain(lanes, rejoin=_rejoin(to_lane=0, to_node=1))) == 1  # same lane
    assert _bound_count(_plain(lanes, rejoin=_rejoin(from_node=0))) == 1  # not last
    assert _bound_count(_plain(lanes, rejoin=_rejoin(to_lane=5))) == 1
    assert _bound_count(_plain(lanes, rejoin=_rejoin(label="r" * MAX_REJOIN_LABEL))) == 0
    assert _bound_count(_plain(lanes, rejoin=_rejoin(label="r" * (MAX_REJOIN_LABEL + 1)))) == 1
    assert _bound_count(_plain(lanes, rejoin=_rejoin(label=""))) == 1
    back = _rejoin(from_lane=1, from_node=0, to_lane=0, to_node=1)  # section 2.4: either direction
    assert _bound_count(_plain(lanes, rejoin=back)) == 0


def test_rejoin_may_leave_a_side_node() -> None:
    lane = _lane(nodes=2)
    lane["nodes"][1] = _node("Side", "", True, "branch")
    assert _bound_count(_plain([lane, _lane()], rejoin=_rejoin())) == 0


def test_every_bound_problem_is_reported() -> None:
    doc = _plain([_lane("bad")] * 7, alt="")
    assert _bound_count(doc) == 1 + 1 + 7


def test_stale_and_bound_are_reported_together() -> None:
    doc = _mutated(alt="")
    findings = _findings(doc, source=FLOW_MMD + b"\n")
    assert [c for c, _ in findings] == [CODE_LAYOUT_BOUND, CODE_LAYOUT_STALE]
    assert header.sha256_lf(FLOW_MMD + b"\n") in findings[1][1]


def test_beside_drawing_is_bound_even_when_otherwise_valid() -> None:
    raw = (VALID / "flow.layout.json").read_bytes()
    findings = layout_findings("flow", raw, FLOW_MMD, beside_drawing=True)
    assert [f.code for f in findings] == [CODE_LAYOUT_BOUND]
    assert "'flow.excalidraw.svg'" in findings[0].message


# --- codes, discovery and the fixtures themselves ---------------------------------------------


def test_layout_codes_are_registered() -> None:
    codes = (CODE_LAYOUT_INVALID, CODE_LAYOUT_BOUND, CODE_LAYOUT_STALE, CODE_LAYOUT_ORPHAN)
    assert set(codes) <= set(FINDING_CODES)
    assert len(set(FINDING_CODES)) == len(FINDING_CODES)


def test_orphan_drawing_and_orphan_sidecar_are_separate_rows(tmp_path: Path) -> None:
    folder = _valid_copy(tmp_path)
    (folder / "lost.excalidraw.svg").write_bytes(b"<svg/>")
    shutil.copyfile(VALID / "flow.layout.json", folder / "lost.layout.json")
    files = {r.file: [f.code for f in r.findings] for r in check_dir(folder).files}
    assert files["lost.excalidraw.svg"] == [CODE_EXCALIDRAW_ORPHAN]
    assert files["lost.layout.json"] == [CODE_LAYOUT_ORPHAN]
    assert files["flow.mmd"] == []


def test_nested_and_directory_sidecars_are_not_discovered(tmp_path: Path) -> None:
    folder = tmp_path / "workflows"
    folder.mkdir()
    (folder / "flow.mmd").write_bytes(FLOW_MMD)
    (folder / "rasters").mkdir()
    (folder / "rasters" / "flow.layout.json").write_bytes(b"not json")
    (folder / "ghost.layout.json").mkdir()
    report = check_dir(folder)
    assert [r.file for r in report.files] == ["flow.mmd"]
    assert report.exit_code == EXIT_OK


def test_read_layout_names_the_layout_limit(tmp_path: Path) -> None:
    """The ``.mmd`` default keeps its Phase 1 limit; a sidecar read names the 64 KiB limit."""
    big = tmp_path / "big.layout.json"
    big.write_bytes(b" " * (MAX_LAYOUT_BYTES + 1))
    assert len(read_source(big)) == MAX_LAYOUT_BYTES + 1
    expected = rf"larger than {MAX_LAYOUT_BYTES} bytes \({MAX_LAYOUT_BYTES // 1024} KiB\)"
    with pytest.raises(GateError, match=expected):
        read_layout(big)


def test_layout_fixtures_are_lf_and_bound_fixtures_pin_the_flow_hash() -> None:
    for path in sorted(LAYOUT.rglob("*")):
        if path.is_file():
            assert b"\r" not in path.read_bytes(), path.name
    flow_hash = header.sha256_lf(FLOW_MMD)
    assert FLOW_LAYOUT["source_sha256"] == flow_hash
    for path in sorted(BOUND.glob("*.layout.json")):
        assert json.loads(path.read_bytes())["source_sha256"] == flow_hash, path.name


# --- the README "Layout sidecar" example (plan section 2.6, Step 11) -------------------------


def _readme_section() -> str:
    """The README "Layout sidecar" section, LF-normalized, up to the next ``##`` heading."""
    text = README.read_bytes().decode("utf-8").replace("\r\n", "\n")
    return text.split(f"\n{README_SECTION_ANCHOR}\n", 1)[1].split("\n## ", 1)[0]


def _readme_example() -> bytes:
    """The section's ``json`` fence: the documented example sidecar, as LF bytes."""
    body = _readme_section().split("\n```json\n", 1)[1].split("\n```\n", 1)[0]
    return f"{body}\n".encode("ascii")


def test_readme_example_sidecar_passes_check_beside_the_template_diagram(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The documented example passes ``check`` beside the ``init`` template diagram it pins.

    One appended line in that diagram then reports ``layout-stale``, so the pass is not vacuous:
    the sidecar really was read and validated.
    """
    folder = tmp_path / "workflows"
    folder.mkdir()
    shutil.copyfile(TEMPLATES / EXAMPLE_NAME, folder / EXAMPLE_NAME)
    (folder / EXAMPLE_SIDECAR).write_bytes(_readme_example())
    assert main(["check", str(folder), "--json"]) == EXIT_OK
    assert finding_codes(_rows(capsys)[EXAMPLE_NAME]) == []

    with (folder / EXAMPLE_NAME).open("ab") as source:
        source.write(b"%% edited\n")
    assert main(["check", str(folder), "--json"]) == EXIT_FINDINGS
    assert finding_codes(_rows(capsys)[EXAMPLE_NAME]) == [CODE_LAYOUT_STALE]


def test_readme_example_sidecar_is_the_dogfooded_sidecar() -> None:
    assert _readme_example() == (DOGFOOD / EXAMPLE_SIDECAR).read_bytes().replace(b"\r\n", b"\n")


def test_readme_layout_section_names_every_layout_code() -> None:
    section = _readme_section()
    for code in (CODE_LAYOUT_INVALID, CODE_LAYOUT_BOUND, CODE_LAYOUT_STALE, CODE_LAYOUT_ORPHAN):
        assert f"`{code}`" in section, code
