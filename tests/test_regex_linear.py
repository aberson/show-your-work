"""Structural guard: every regex in ``constants`` stays fast on hostile input.

Super-linear backtracking on attacker-sized file content has bitten this package twice (the
Step 2 label regexes, the Step 3 intro-heading and inline-link regexes). Instead of pinning
individual patterns, this test discovers EVERY compiled ``re.Pattern`` in
:mod:`show_your_work.constants` by introspection (including patterns held in tuples), so a regex
added later is covered automatically, and runs each through ``finditer`` (which exercises
``search`` at every offset, the superset of how production calls ``match`` / ``search`` /
``finditer`` / ``sub``) against a battery of generic pathological inputs of ~64 KB under a tight
per-input time bound. A quadratic pattern needs tens of seconds per input at this size; a linear
one needs milliseconds.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator

import pytest

from show_your_work import constants

#: Size of each pathological input, in characters.
SIZE = 64 * 1024
#: Per-pattern, per-input wall-clock bound. Linear scans of 64 KB finish in a few milliseconds.
BOUND_SECONDS = 0.5

#: Characters whose long runs drive the classic backtracking shapes (quantified classes,
#: delimiters, quotes, markers) used across the package's patterns.
RUN_CHARS = " \t[](){}|#\"'-=`~%:/.>*_aA0\\"
#: Openers placed before a run so anchored / delimited patterns get past their first tokens.
PREFIXES = ("", "# ", "# a", "%% ", "[a](", '-- "', 'class="', "a:", "|")
#: Trailing characters that make the overall match fail late (forcing full backtracking).
SUFFIXES = ("x", "\n", "!")


def discovered_patterns() -> dict[str, re.Pattern[str]]:
    """Every compiled pattern in ``constants``, keyed by name (``NAME[i]`` inside a tuple)."""
    found: dict[str, re.Pattern[str]] = {}
    for name, value in vars(constants).items():
        if isinstance(value, re.Pattern):
            found[name] = value
        elif isinstance(value, tuple):
            for index, item in enumerate(value):
                if isinstance(item, re.Pattern):
                    found[f"{name}[{index}]"] = item
    return found


def pathological_inputs() -> Iterator[tuple[str, str]]:
    """``(label, text)`` pairs: long single-character runs, alternations and repeated openers."""
    for prefix in PREFIXES:
        for ch in RUN_CHARS:
            for suffix in SUFFIXES:
                yield f"{prefix!r}+{ch!r}*N+{suffix!r}", prefix + ch * SIZE + suffix
    for prefix in PREFIXES:
        if prefix:
            yield f"{prefix!r}*N", (prefix * (SIZE // len(prefix) + 1))[:SIZE]
    for ch in RUN_CHARS:
        yield f"({ch!r}+' ')*N", ((ch + " ") * (SIZE // 2)) + "x"
        yield f"({ch!r}+newline)*N", ((ch + "\n") * (SIZE // 2)) + "x"


#: The Step 3 iteration-2 ATX heading regex: quadratic on ``# a`` + spaces + ``x`` (the review
#: measured 2.8 s at 20 000 spaces). Kept only as the detector's known-garbage anchor.
OLD_QUADRATIC_ATX = r"^ {0,3}#{1,6}(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$"


def elapsed(pattern: re.Pattern[str], text: str) -> float:
    start = time.perf_counter()
    for _ in pattern.finditer(text):
        pass
    return time.perf_counter() - start


def slow_inputs(pattern: re.Pattern[str]) -> Iterator[str]:
    """Yield the label of each battery input ``pattern`` takes longer than the bound on."""
    for label, text in pathological_inputs():
        if elapsed(pattern, text) > BOUND_SECONDS:
            yield label


def test_discovery_finds_every_known_pattern() -> None:
    """The guard is not vacuous: introspection sees the patterns production actually uses."""
    names = set(discovered_patterns())
    expected = {
        "LABEL_RE",
        "ABS_PATH_RE",
        "USERS_PATH_RE",
        "ID_RE",
        "SVG_NODE_RE",
        "SVG_EDGE_RE",
        "SVG_EDGE_FALLBACK_RE",
        "NODE_LABEL_RES[0]",
        "NODE_LABEL_RES[1]",
        "EDGE_LABEL_RES[0]",
        "EDGE_LABEL_RES[1]",
        "EDGE_LABEL_RES[2]",
        "SUBGRAPH_RE",
        "SUBGRAPH_END_RE",
        "SHA256_HEX_RE",
        "ATX_HEADING_RE",
        "FENCE_LINE_RE",
        "SETEXT_UNDERLINE_RE",
        "BLOCK_START_RE",
        "INDENTED_CODE_RE",
        "INLINE_LINK_RE",
    }
    assert expected <= names


def test_timing_detects_the_old_quadratic_atx_regex() -> None:
    """Calibration (known-garbage anchor): the timing method sees a real quadratic pattern.

    Run at a reduced size (~1 s instead of ~30 s) against a tenth of the bound; the same input
    shape at full size is in the battery (``'# a'`` prefix + a run of spaces). The fixed pattern
    on the same input is far below the bound.
    """
    text = "# a" + " " * 12_000 + "x"
    assert elapsed(re.compile(OLD_QUADRATIC_ATX), text) > BOUND_SECONDS / 10
    assert elapsed(constants.ATX_HEADING_RE, text) < BOUND_SECONDS / 10


@pytest.mark.parametrize("name", sorted(discovered_patterns()))
def test_every_constants_regex_is_linear_on_hostile_input(name: str) -> None:
    pattern = discovered_patterns()[name]
    slow = next(slow_inputs(pattern), None)
    assert slow is None, f"{name} ({pattern.pattern!r}) took > {BOUND_SECONDS}s on {slow}"
