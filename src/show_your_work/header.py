"""The single ``.mmd`` header parser (``docs/plans/master_plan.md`` section 3.3, DD1).

A diagram opens with a block of mermaid ``%%`` comment lines:

* the first non-empty header line is the **title**, verbatim (a colon is fine, and a title shaped
  like an *unrecognised* label such as ``Title: ...`` stays the title). A first line that is one of
  the four *recognised* labels is not a title: the title is reported missing and the line is parsed
  as that label;
* then ``Label: value`` lines for ``Summary``, ``Sources``, ``Not shown`` and ``Alt`` (label names
  compare case-insensitively; a label needs a space or end-of-line after its colon, so an
  unbulleted ``https://...`` source is never a label);
* a bullet (``- ...``) continues ``Sources:``, and so does any other non-label line while the
  "in sources" state is set; **every** label line clears that state (the prototype left it sticky,
  so an ``Alt:`` after ``Sources:`` was swallowed as a source);
* any other non-label line is ``header-stray-line``, any other label is ``header-unknown-label``;
* the header ends at the first non-``%%``, non-blank line. CRLF is normalised to LF first.

``parse_header`` raises :class:`HeaderError` carrying every finding; ``check_header`` returns the
error instead of raising. :func:`sha256_lf` is the SHA-256 of the source with CRLF turned into LF,
shared by ``render`` and the layout sidecar check, and :func:`clip_detail` the one-line message
clipper shared by ``checks`` and ``layout``. Every field name, bound, regex and finding code comes
from :mod:`show_your_work.constants`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from show_your_work.constants import (
    ABS_PATH_RE,
    BOM,
    BULLET_PREFIX,
    CODE_ABS_PATH,
    CODE_ALT_NOT_ASCII,
    CODE_ALT_REPEATS_TITLE,
    CODE_HEADER_BOUND,
    CODE_HEADER_STRAY_LINE,
    CODE_HEADER_UNKNOWN_LABEL,
    CODE_NON_ASCII,
    COMMENT_PREFIX,
    FIELD_ALT,
    FIELD_NOT_SHOWN,
    FIELD_SOURCES,
    FIELD_SUMMARY,
    FIELD_TITLE,
    HEADER_BOUNDS,
    HEADER_LABELS,
    LABEL_RE,
    MAX_ASCII,
    MIN_PRINTABLE,
    MIN_SOURCES,
    MISSING_FIELD_CODES,
)
from show_your_work.constants import HEADER_FIELDS as HEADER_FIELDS

__all__ = [
    "HEADER_FIELDS",
    "Header",
    "HeaderError",
    "HeaderFinding",
    "check_header",
    "clip_detail",
    "normalize_newlines",
    "parse_header",
    "sha256_lf",
]


@dataclass(frozen=True, slots=True)
class Header:
    """The five required header fields of one diagram (field order = ``HEADER_FIELDS``)."""

    title: str
    summary: str
    sources: tuple[str, ...]
    not_shown: str
    alt: str


@dataclass(frozen=True, slots=True)
class HeaderFinding:
    """One header-contract violation.

    ``field`` names the header field concerned (one of ``HEADER_FIELDS``) or is ``None`` for a
    structural finding (stray line, unknown label). ``line`` is the 1-based source line, when the
    finding is tied to one.
    """

    code: str
    field: str | None
    message: str
    line: int | None = None


class HeaderError(ValueError):
    """Raised by :func:`parse_header` when the header violates the contract.

    ``findings`` holds every violation in a stable order (line order for structural findings, then
    missing fields in ``HEADER_FIELDS`` order, then per-field value checks). ``code``, ``field``
    and ``message`` mirror the first finding.
    """

    def __init__(self, findings: Sequence[HeaderFinding]) -> None:
        if not findings:
            raise ValueError("HeaderError needs at least one finding")
        self.findings: tuple[HeaderFinding, ...] = tuple(findings)
        super().__init__("; ".join(f"{f.code}: {f.message}" for f in self.findings))

    @property
    def code(self) -> str:
        return self.findings[0].code

    @property
    def field(self) -> str | None:
        return self.findings[0].field

    @property
    def message(self) -> str:
        return self.findings[0].message

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(f.code for f in self.findings)


def normalize_newlines(text: str) -> str:
    """Return ``text`` with one leading BOM removed and every CRLF turned into LF.

    Section 3.3: every reader normalizes CRLF before parsing, hashing or embedding. A leading
    UTF-8 BOM would otherwise hide the first ``%%`` line and report every field missing.
    """
    return text.removeprefix(BOM).replace("\r\n", "\n")


def sha256_lf(raw: bytes) -> str:
    """SHA-256 over the bytes after CRLF -> LF normalization (section 3.4).

    The one hash a diagram is known by: ``workflows.json`` ``source_sha256`` and the layout
    sidecar's ``source_sha256`` pin (Phase 2 section 2.4) both use it.
    """
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def clip_detail(text: str, limit: int, empty: str) -> str:
    """``text`` collapsed to one whitespace-joined line of at most ``limit`` characters (plus
    ``...``); ``empty`` when nothing is left after the collapse."""
    detail = " ".join(text.split()) or empty
    if len(detail) > limit:
        detail = detail[:limit] + "..."
    return detail


def _header_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line_number, content)`` for each non-empty ``%%`` line of the header block."""
    for number, raw in enumerate(normalize_newlines(text).split("\n"), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        if not stripped.startswith(COMMENT_PREFIX):
            return
        content = stripped[len(COMMENT_PREFIX) :].strip()
        if content:
            yield number, content


def _label_of(content: str) -> tuple[str, str] | None:
    """Return ``(label, value)`` when ``content`` is a label line, else ``None``."""
    if content.startswith(BULLET_PREFIX):
        return None
    match = LABEL_RE.match(content)
    if match is None:
        return None
    return match.group(1).strip(), content[match.end(1) + 1 :].strip()


def _is_ascii(value: str) -> bool:
    return all(ord(ch) <= MAX_ASCII for ch in value)


def _has_control(value: str) -> bool:
    return any(ord(ch) < MIN_PRINTABLE or ord(ch) == MAX_ASCII for ch in value)


def _scan(text: str) -> tuple[dict[str, str], list[str], list[HeaderFinding]]:
    """Walk the header block; return scalar fields, source entries and structural findings."""
    values: dict[str, str] = {}
    sources: list[str] = []
    findings: list[HeaderFinding] = []
    seen_labels: set[str] = set()
    in_sources = False
    first = True

    for number, content in _header_lines(text):
        label = _label_of(content)
        if first:
            first = False
            if label is None or label[0].casefold() not in HEADER_LABELS:
                values[FIELD_TITLE] = content
                continue
            # A recognised label on the first line: the title is missing; parse it as a label.

        if label is not None:
            name, value = label
            in_sources = False  # every label line clears the "in sources" state
            field = HEADER_LABELS.get(name.casefold())
            if field is None:
                findings.append(
                    HeaderFinding(
                        CODE_HEADER_UNKNOWN_LABEL,
                        None,
                        f"unknown header label {name!r} on line {number}",
                        number,
                    )
                )
                continue
            if field in seen_labels:
                findings.append(
                    HeaderFinding(
                        CODE_HEADER_STRAY_LINE,
                        field,
                        f"duplicate header label {name!r} on line {number}",
                        number,
                    )
                )
                continue
            seen_labels.add(field)
            if field == FIELD_SOURCES:
                in_sources = True
                if value:
                    sources.append(value)
            else:
                values[field] = value
            continue

        if in_sources:
            entry = content[len(BULLET_PREFIX) :] if content.startswith(BULLET_PREFIX) else content
            entry = entry.strip()
            if entry:
                sources.append(entry)
            continue

        findings.append(
            HeaderFinding(
                CODE_HEADER_STRAY_LINE,
                None,
                f"stray header line {number} outside 'Sources:': {content!r}",
                number,
            )
        )

    return values, sources, findings


def _value_findings(values: dict[str, str], sources: list[str]) -> list[HeaderFinding]:
    """Missing-field, bound, ASCII, Alt and absolute-path checks on the scanned values."""
    findings: list[HeaderFinding] = []
    for field in HEADER_FIELDS:
        present = len(sources) >= MIN_SOURCES if field == FIELD_SOURCES else bool(values.get(field))
        if not present:
            findings.append(
                HeaderFinding(MISSING_FIELD_CODES[field], field, f"header field {field!r} missing")
            )

    for field, bound in HEADER_BOUNDS.items():
        value = values.get(field, "")
        if len(value) > bound:
            findings.append(
                HeaderFinding(
                    CODE_HEADER_BOUND,
                    field,
                    f"header field {field!r} is {len(value)} chars (max {bound})",
                )
            )

    for field in (FIELD_TITLE, FIELD_SUMMARY, FIELD_NOT_SHOWN):
        if not _is_ascii(values.get(field, "")):
            findings.append(
                HeaderFinding(CODE_NON_ASCII, field, f"header field {field!r} is not ASCII")
            )
    if any(not _is_ascii(entry) for entry in sources):
        findings.append(
            HeaderFinding(
                CODE_NON_ASCII, FIELD_SOURCES, f"header field {FIELD_SOURCES!r} is not ASCII"
            )
        )

    alt = values.get(FIELD_ALT, "")
    if not _is_ascii(alt) or _has_control(alt):
        findings.append(
            HeaderFinding(
                CODE_ALT_NOT_ASCII,
                FIELD_ALT,
                f"header field {FIELD_ALT!r} must be printable ASCII with no control characters",
            )
        )
    title = values.get(FIELD_TITLE, "")
    if alt and title and alt.casefold() == title.casefold():
        findings.append(
            HeaderFinding(
                CODE_ALT_REPEATS_TITLE,
                FIELD_ALT,
                f"header field {FIELD_ALT!r} repeats the title; describe the picture instead",
            )
        )

    for entry in sources:
        if ABS_PATH_RE.match(entry):
            findings.append(
                HeaderFinding(
                    CODE_ABS_PATH,
                    FIELD_SOURCES,
                    f"header field {FIELD_SOURCES!r} entry is a machine-absolute path: {entry!r}",
                )
            )
    return findings


def check_header(text: str) -> Header | HeaderError:
    """Parse ``text``; return a :class:`Header`, or the :class:`HeaderError` instead of raising."""
    values, sources, findings = _scan(text)
    findings.extend(_value_findings(values, sources))
    if findings:
        return HeaderError(findings)
    return Header(
        title=values[FIELD_TITLE],
        summary=values[FIELD_SUMMARY],
        sources=tuple(sources),
        not_shown=values[FIELD_NOT_SHOWN],
        alt=values[FIELD_ALT],
    )


def parse_header(text: str) -> Header:
    """Parse the header block of a ``.mmd`` source; raise :class:`HeaderError` on any violation."""
    result = check_header(text)
    if isinstance(result, HeaderError):
        raise result
    return result
