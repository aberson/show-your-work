"""The layout sidecar ``<id>.layout.json`` (``docs/plans/master_plan.md`` Phase 2 section 2.4).

A sidecar opts one diagram into the house column-and-spine drawing. This module owns its parse and
its validation; ``checks.py`` discovers and reads the files and attaches the results to the report
rows (section 2.4: ``layout-orphan`` gets its own row, every other code lands on the ``.mmd`` row).

* :func:`parse_layout` turns the bytes into a typed :class:`Layout` or raises :class:`LayoutError`:
  non-UTF-8 bytes, a JSON error of any kind (duplicate keys, ``NaN`` / ``Infinity``, an integer
  token over ``MAX_LAYOUT_INT_DIGITS`` digits, nesting that recurses too deep), unknown or missing
  keys, a wrong type (a boolean is never an integer), a wrong ``schema`` or a malformed
  ``source_sha256``.
* :func:`layout_findings` adds the ``layout-bound`` rules (printable ASCII, lengths, counts, side /
  loop / rejoin structure, a sidecar beside an excalidraw drawing) and the ``layout-stale`` hash pin
  against the sibling ``.mmd`` (:func:`show_your_work.header.sha256_lf`).

Every bound, key name, code and regex comes from :mod:`show_your_work.constants`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from show_your_work.constants import (
    CODE_LAYOUT_BOUND,
    CODE_LAYOUT_INVALID,
    CODE_LAYOUT_STALE,
    EXCALIDRAW_SUFFIX,
    LANE_IN_KEY,
    LANE_KEYS,
    LANE_LABEL_CHARS_TEXT,
    LANE_LABEL_KEY,
    LANE_LABEL_RE,
    LANE_NODES_KEY,
    LANE_OUT_KEY,
    LAYOUT_ALT_KEY,
    LAYOUT_KEYS,
    LAYOUT_LANES_KEY,
    LAYOUT_LOOPS_KEY,
    LAYOUT_REJOIN_KEY,
    LAYOUT_SCHEMA,
    LAYOUT_SCHEMA_KEY,
    LAYOUT_SHA_KEY,
    LAYOUT_SUFFIX,
    LOOP_FROM_KEY,
    LOOP_KEYS,
    LOOP_LANE_KEY,
    LOOP_TO_KEY,
    MAX_ALT,
    MAX_BRANCH_LABEL,
    MAX_LANE_LABEL,
    MAX_LANE_NODES,
    MAX_LAYOUT_DETAIL,
    MAX_LAYOUT_INT_DIGITS,
    MAX_LAYOUT_LANES,
    MAX_LOOPS_PER_LANE,
    MAX_NODE_NAME,
    MAX_NODE_SUB,
    MAX_PRINTABLE,
    MAX_REJOIN_LABEL,
    MIN_LANE_NODES,
    MIN_LAYOUT_LANES,
    MIN_PRINTABLE,
    NODE_BRANCH_LABEL_KEY,
    NODE_KEYS,
    NODE_NAME_KEY,
    NODE_SIDE_KEY,
    NODE_SUB_KEY,
    REJOIN_FROM_LANE_KEY,
    REJOIN_FROM_NODE_KEY,
    REJOIN_KEYS,
    REJOIN_LABEL_KEY,
    REJOIN_TO_LANE_KEY,
    REJOIN_TO_NODE_KEY,
    SHA256_HEX_CHARS,
    SHA256_HEX_RE,
    SOURCE_SUFFIX,
)
from show_your_work.header import clip_detail, sha256_lf

__all__ = [
    "Lane",
    "Layout",
    "LayoutError",
    "LayoutFinding",
    "Loop",
    "Node",
    "Rejoin",
    "layout_findings",
    "parse_layout",
]


@dataclass(frozen=True, slots=True)
class Node:
    """One pill in a lane."""

    name: str
    sub: str
    side: bool
    branch_label: str


@dataclass(frozen=True, slots=True)
class Lane:
    """One column: a label, its spine entry / exit node indexes and its nodes.

    ``layout_findings`` bounds the node count between ``MIN_LANE_NODES`` and ``MAX_LANE_NODES``.
    """

    label: str
    in_node: int
    out_node: int
    nodes: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class Loop:
    """An up-arrow inside one lane from node ``from_node`` back to node ``to_node``."""

    lane: int
    from_node: int
    to_node: int


@dataclass(frozen=True, slots=True)
class Rejoin:
    """The dashed return from the last node of one lane to the last node of another."""

    from_lane: int
    from_node: int
    to_lane: int
    to_node: int
    label: str


@dataclass(frozen=True, slots=True)
class Layout:
    """A structurally valid sidecar (bounds are checked separately by :func:`layout_findings`)."""

    schema: str
    source_sha256: str
    alt: str
    lanes: tuple[Lane, ...]
    loops: tuple[Loop, ...]
    rejoin: Rejoin | None


@dataclass(frozen=True, slots=True)
class LayoutFinding:
    """One sidecar violation: a stable ``layout-*`` code and a message naming the sidecar."""

    code: str
    message: str


class LayoutError(ValueError):
    """Raised by :func:`parse_layout` when the sidecar is not a valid ``LAYOUT_SCHEMA`` document."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# --- parsing ---------------------------------------------------------------------------------


def _clip(text: str) -> str:
    """``text`` as one line of at most ``MAX_LAYOUT_DETAIL`` characters (``header.clip_detail``)."""
    return clip_detail(text, MAX_LAYOUT_DETAIL, "(empty)")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook``: build the object, refusing a key that appears twice."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _no_constant(name: str) -> Any:
    """``parse_constant`` hook: ``NaN``, ``Infinity`` and ``-Infinity`` are never accepted."""
    raise ValueError(f"{name} is not allowed")


def _bounded_int(token: str) -> int:
    """``parse_int`` hook: refuse a token of over ``MAX_LAYOUT_INT_DIGITS`` digits unconverted.

    The bound applies before ``int()`` runs, so the interpreter's int-string digit limit never
    decides whether a sidecar is valid.
    """
    digits = len(token.lstrip("-"))
    if digits > MAX_LAYOUT_INT_DIGITS:
        raise LayoutError(
            f"an integer has {digits} digits (allowed at most {MAX_LAYOUT_INT_DIGITS})"
        )
    return int(token)


def _load_json(raw: bytes) -> Any:
    """Decode strict UTF-8 and strict JSON; every failure is one :class:`LayoutError`."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LayoutError(f"not valid UTF-8 (byte {exc.start})") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_no_duplicates,
            parse_constant=_no_constant,
            parse_int=_bounded_int,
        )
    except LayoutError:
        raise
    except RecursionError as exc:
        raise LayoutError("not valid JSON: nested too deeply") from exc
    except ValueError as exc:  # JSONDecodeError and the duplicate-key / constant hooks
        raise LayoutError(f"not valid JSON: {_clip(str(exc))}") from exc


def _type_name(value: object) -> str:
    return type(value).__name__


def _object(value: object, where: str, keys: Sequence[str]) -> Mapping[str, Any]:
    """``value`` as a JSON object holding exactly ``keys`` (unknown keys reported first)."""
    if not isinstance(value, dict):
        raise LayoutError(f"{where} must be an object, not {_type_name(value)}")
    unknown = [k for k in value if k not in keys]
    if unknown:
        listed = _clip(", ".join(repr(k) for k in unknown))
        raise LayoutError(f"{where} has unknown key(s): {listed}")
    missing = [k for k in keys if k not in value]
    if missing:
        raise LayoutError(f"{where} is missing key(s): {', '.join(repr(k) for k in missing)}")
    return value


def _str(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise LayoutError(f"{where} must be a string, not {_type_name(value)}")
    return value


def _int(value: object, where: str) -> int:
    # ``type(...) is int``: a JSON boolean is a Python bool, which ``isinstance`` calls an int.
    if type(value) is not int:
        raise LayoutError(f"{where} must be an integer, not {_type_name(value)}")
    return value


def _bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise LayoutError(f"{where} must be a boolean, not {_type_name(value)}")
    return value


def _array(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise LayoutError(f"{where} must be an array, not {_type_name(value)}")
    return value


def _node(value: object, where: str) -> Node:
    """Parse one lane node object into a :class:`Node`."""
    obj = _object(value, where, NODE_KEYS)
    return Node(
        name=_str(obj[NODE_NAME_KEY], f"{where}.{NODE_NAME_KEY}"),
        sub=_str(obj[NODE_SUB_KEY], f"{where}.{NODE_SUB_KEY}"),
        side=_bool(obj[NODE_SIDE_KEY], f"{where}.{NODE_SIDE_KEY}"),
        branch_label=_str(obj[NODE_BRANCH_LABEL_KEY], f"{where}.{NODE_BRANCH_LABEL_KEY}"),
    )


def _lane(value: object, where: str) -> Lane:
    """Parse one lane object, with its nodes, into a :class:`Lane`."""
    obj = _object(value, where, LANE_KEYS)
    nodes_where = f"{where}.{LANE_NODES_KEY}"
    nodes = _array(obj[LANE_NODES_KEY], nodes_where)
    return Lane(
        label=_str(obj[LANE_LABEL_KEY], f"{where}.{LANE_LABEL_KEY}"),
        in_node=_int(obj[LANE_IN_KEY], f"{where}.{LANE_IN_KEY}"),
        out_node=_int(obj[LANE_OUT_KEY], f"{where}.{LANE_OUT_KEY}"),
        nodes=tuple(_node(n, f"{nodes_where}[{k}]") for k, n in enumerate(nodes)),
    )


def _loop(value: object, where: str) -> Loop:
    """Parse one loop object into a :class:`Loop`."""
    obj = _object(value, where, LOOP_KEYS)
    return Loop(
        lane=_int(obj[LOOP_LANE_KEY], f"{where}.{LOOP_LANE_KEY}"),
        from_node=_int(obj[LOOP_FROM_KEY], f"{where}.{LOOP_FROM_KEY}"),
        to_node=_int(obj[LOOP_TO_KEY], f"{where}.{LOOP_TO_KEY}"),
    )


def _rejoin(value: object, where: str) -> Rejoin | None:
    """Parse the rejoin object into a :class:`Rejoin`; ``null`` means no rejoin."""
    if value is None:
        return None
    obj = _object(value, where, REJOIN_KEYS)
    return Rejoin(
        from_lane=_int(obj[REJOIN_FROM_LANE_KEY], f"{where}.{REJOIN_FROM_LANE_KEY}"),
        from_node=_int(obj[REJOIN_FROM_NODE_KEY], f"{where}.{REJOIN_FROM_NODE_KEY}"),
        to_lane=_int(obj[REJOIN_TO_LANE_KEY], f"{where}.{REJOIN_TO_LANE_KEY}"),
        to_node=_int(obj[REJOIN_TO_NODE_KEY], f"{where}.{REJOIN_TO_NODE_KEY}"),
        label=_str(obj[REJOIN_LABEL_KEY], f"{where}.{REJOIN_LABEL_KEY}"),
    )


def parse_layout(raw: bytes) -> Layout:
    """Parse sidecar bytes into a :class:`Layout`; raise :class:`LayoutError` (section 2.4).

    Only the ``layout-invalid`` rules apply here; the bounds are :func:`layout_findings`'s job.
    """
    obj = _object(_load_json(raw), "the sidecar", LAYOUT_KEYS)
    schema = _str(obj[LAYOUT_SCHEMA_KEY], LAYOUT_SCHEMA_KEY)
    if schema != LAYOUT_SCHEMA:
        raise LayoutError(f"{LAYOUT_SCHEMA_KEY} must be {LAYOUT_SCHEMA!r}")
    digest = _str(obj[LAYOUT_SHA_KEY], LAYOUT_SHA_KEY)
    if SHA256_HEX_RE.fullmatch(digest) is None:
        raise LayoutError(f"{LAYOUT_SHA_KEY} must be {SHA256_HEX_CHARS} lowercase hex characters")
    lanes = _array(obj[LAYOUT_LANES_KEY], LAYOUT_LANES_KEY)
    loops = _array(obj[LAYOUT_LOOPS_KEY], LAYOUT_LOOPS_KEY)
    return Layout(
        schema=schema,
        source_sha256=digest,
        alt=_str(obj[LAYOUT_ALT_KEY], LAYOUT_ALT_KEY),
        lanes=tuple(_lane(lane, f"{LAYOUT_LANES_KEY}[{i}]") for i, lane in enumerate(lanes)),
        loops=tuple(_loop(loop, f"{LAYOUT_LOOPS_KEY}[{j}]") for j, loop in enumerate(loops)),
        rejoin=_rejoin(obj[LAYOUT_REJOIN_KEY], LAYOUT_REJOIN_KEY),
    )


# --- bounds ----------------------------------------------------------------------------------


def _printable(value: str) -> bool:
    return all(MIN_PRINTABLE <= ord(ch) <= MAX_PRINTABLE for ch in value)


def _string_problems(value: str, where: str, low: int, high: int) -> list[str]:
    """Printable ASCII first (a failing string gets that one problem), then the length bound."""
    if not _printable(value):
        return [f"{where} is not printable ASCII (0x{MIN_PRINTABLE:02X}-0x{MAX_PRINTABLE:02X})"]
    if not low <= len(value) <= high:
        return [f"{where} is {len(value)} chars (allowed {low}..{high})"]
    return []


def _int_text(value: int) -> str:
    """An untrusted sidecar integer as message text, clipped like every other echoed detail."""
    return _clip(str(value))


def _in_range(index: int, count: int) -> bool:
    return 0 <= index < count


def _lane_problems(lane: Lane, where: str) -> list[str]:
    """Label, node-count, node-text, side-node and ``in`` / ``out`` problems of one lane."""
    problems = _string_problems(lane.label, f"{where}.{LANE_LABEL_KEY}", 1, MAX_LANE_LABEL)
    if not problems and LANE_LABEL_RE.fullmatch(lane.label) is None:
        problems.append(f"{where}.{LANE_LABEL_KEY} may hold only {LANE_LABEL_CHARS_TEXT}")
    count = len(lane.nodes)
    if not MIN_LANE_NODES <= count <= MAX_LANE_NODES:
        problems.append(
            f"{where}.{LANE_NODES_KEY} has {count} nodes "
            f"(allowed {MIN_LANE_NODES}..{MAX_LANE_NODES})"
        )
    for k, node in enumerate(lane.nodes):
        node_where = f"{where}.{LANE_NODES_KEY}[{k}]"
        problems += _string_problems(node.name, f"{node_where}.{NODE_NAME_KEY}", 1, MAX_NODE_NAME)
        problems += _string_problems(node.sub, f"{node_where}.{NODE_SUB_KEY}", 0, MAX_NODE_SUB)
        label_where = f"{node_where}.{NODE_BRANCH_LABEL_KEY}"
        if node.side:
            if k == 0:
                problems.append(f"{node_where} is a side node but node 0 is never side")
            elif k != count - 1:
                problems.append(f"{node_where} is a side node but not the lane's last node")
            problems += _string_problems(node.branch_label, label_where, 1, MAX_BRANCH_LABEL)
        elif node.branch_label:
            problems.append(f"{label_where} must be empty on a non-side node")
    for key, index in ((LANE_IN_KEY, lane.in_node), (LANE_OUT_KEY, lane.out_node)):
        if not _in_range(index, count):
            problems.append(f"{where}.{key} = {_int_text(index)} is not a node of the lane")
        elif lane.nodes[index].side:
            problems.append(f"{where}.{key} = {_int_text(index)} names a side node")
    return problems


def _loop_problems(layout: Layout) -> list[str]:
    """Each loop: an existing lane, one per lane, one node up, never touching a side node."""
    problems: list[str] = []
    per_lane: dict[int, int] = {}
    for j, loop in enumerate(layout.loops):
        where = f"{LAYOUT_LOOPS_KEY}[{j}]"
        if not _in_range(loop.lane, len(layout.lanes)):
            problems.append(f"{where}.{LOOP_LANE_KEY} = {_int_text(loop.lane)} is not a lane")
            continue
        per_lane[loop.lane] = per_lane.get(loop.lane, 0) + 1
        if per_lane[loop.lane] == MAX_LOOPS_PER_LANE + 1:
            problems.append(f"lane {loop.lane} has more than {MAX_LOOPS_PER_LANE} loop")
        nodes = layout.lanes[loop.lane].nodes
        ends = ((LOOP_FROM_KEY, loop.from_node), (LOOP_TO_KEY, loop.to_node))
        bad_ends = [
            f"{where}.{key} = {_int_text(index)}"
            for key, index in ends
            if not _in_range(index, len(nodes))
        ]
        if bad_ends:
            problems.extend(f"{end} is not a node of lane {loop.lane}" for end in bad_ends)
            continue
        if loop.from_node != loop.to_node + 1:
            problems.append(
                f"{where} must run from a node to the one directly above it "
                f"({LOOP_FROM_KEY} == {LOOP_TO_KEY} + 1)"
            )
        if nodes[loop.from_node].side or nodes[loop.to_node].side:
            problems.append(f"{where} touches a side node")
    return problems


def _rejoin_problems(layout: Layout) -> list[str]:
    """The rejoin (if any): two different lanes, last node to last node, a bounded label."""
    rejoin = layout.rejoin
    if rejoin is None:
        return []
    where = LAYOUT_REJOIN_KEY
    problems: list[str] = []
    if rejoin.from_lane == rejoin.to_lane:
        problems.append(f"{where} must join two different lanes")
    ends = (
        (REJOIN_FROM_LANE_KEY, rejoin.from_lane, REJOIN_FROM_NODE_KEY, rejoin.from_node),
        (REJOIN_TO_LANE_KEY, rejoin.to_lane, REJOIN_TO_NODE_KEY, rejoin.to_node),
    )
    for lane_key, lane, node_key, node in ends:
        if not _in_range(lane, len(layout.lanes)):
            problems.append(f"{where}.{lane_key} = {_int_text(lane)} is not a lane")
            continue
        last = len(layout.lanes[lane].nodes) - 1
        if node != last:
            problems.append(
                f"{where}.{node_key} = {_int_text(node)} is not the last node of lane {lane}"
            )
    problems += _string_problems(rejoin.label, f"{where}.{REJOIN_LABEL_KEY}", 1, MAX_REJOIN_LABEL)
    return problems


def _bound_problems(layout: Layout) -> list[str]:
    """Every ``layout-bound`` problem of a parsed sidecar, in document order."""
    problems = _string_problems(layout.alt, LAYOUT_ALT_KEY, 1, MAX_ALT)
    count = len(layout.lanes)
    if not MIN_LAYOUT_LANES <= count <= MAX_LAYOUT_LANES:
        problems.append(
            f"{LAYOUT_LANES_KEY} has {count} lanes (allowed {MIN_LAYOUT_LANES}..{MAX_LAYOUT_LANES})"
        )
    for i, lane in enumerate(layout.lanes):
        problems += _lane_problems(lane, f"{LAYOUT_LANES_KEY}[{i}]")
    return problems + _loop_problems(layout) + _rejoin_problems(layout)


def layout_findings(
    diagram_id: str, raw: bytes, source_raw: bytes, *, beside_drawing: bool = False
) -> list[LayoutFinding]:
    """Validate ``<diagram_id>.layout.json`` (bytes ``raw``) against its sibling ``.mmd`` bytes.

    One ``layout-invalid`` finding when the sidecar does not parse (nothing else is checked then);
    otherwise every ``layout-bound`` problem in document order, ``layout-bound`` when an
    ``<id>.excalidraw.svg`` sits beside it, and ``layout-stale`` when ``source_sha256`` is not the
    CRLF->LF SHA-256 of ``source_raw`` (the message names the expected hash). Every message starts
    with the sidecar's file name, quoted with ``repr`` like its neighbours.
    """
    name = diagram_id + LAYOUT_SUFFIX
    try:
        layout = parse_layout(raw)
    except LayoutError as exc:
        return [LayoutFinding(CODE_LAYOUT_INVALID, f"{name!r}: {exc.message}")]
    findings = [LayoutFinding(CODE_LAYOUT_BOUND, f"{name!r}: {p}") for p in _bound_problems(layout)]
    if beside_drawing:
        drawing = diagram_id + EXCALIDRAW_SUFFIX
        findings.append(
            LayoutFinding(
                CODE_LAYOUT_BOUND,
                f"{name!r}: sits beside {drawing!r}, which is already the drawing for this id",
            )
        )
    expected = sha256_lf(source_raw)
    if layout.source_sha256 != expected:
        source = diagram_id + SOURCE_SUFFIX
        findings.append(
            LayoutFinding(
                CODE_LAYOUT_STALE,
                f"{name!r}: {LAYOUT_SHA_KEY} does not match {source!r} (expected {expected})",
            )
        )
    return findings
