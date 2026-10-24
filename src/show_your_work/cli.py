"""argparse entry point for ``show-your-work`` (``docs/plans/master_plan.md`` section 4).

The argument surface of the three verbs is final:

* ``show-your-work init [TARGET] [--force]``
* ``show-your-work check [TARGET] [--render] [--json]``
* ``show-your-work render [TARGET] [--check] [--png] [--json]``

Every usage error -- no verb, unknown verb, unknown flag, missing value -- prints the usage and
exits ``EXIT_USAGE`` (64): argparse's own default is 2, which would collide with ``EXIT_GATE``
("gate could not run"), so :class:`UsageExitParser` overrides ``ArgumentParser.error``.

``check`` is wired (exit 0 all pass, 1 any file fails, 2 the gate could not run: no ``*.mmd``,
``--render`` without mermaidx or with a broken one, a failed render anchor, an unusable TARGET,
an unreadable / symlinked / oversized source). Human output is a fixed-width table plus the
section 4 summary line, with non-ASCII and control characters escaped; ``--json`` emits
``show-your-work.report.v1``. Gate messages are printed to stderr (with ``--json`` a report with
``exit_code`` 2 still goes to stdout). ``init`` and ``render`` remain stubs until later steps.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn

from show_your_work import __version__, renderer
from show_your_work.checks import FileResult, GateError, Report, check_dir
from show_your_work.constants import (
    BUDGET,
    COUNT_NOT_RENDERED,
    COUNTS_NOTE,
    DEFAULT_TARGET_PARTS,
    EXIT_GATE,
    EXIT_OK,
    EXIT_USAGE,
    MAX_ASCII,
    MIN_PRINTABLE,
    MSG_NO_DEFAULT_TARGET,
    MSG_RENDERER_BROKEN,
    MSG_RENDERER_MISSING,
    REPORT_SCHEMA,
    SUMMARY_LINE,
)

__all__ = ["UsageExitParser", "build_parser", "format_table", "main", "report_json"]

PROG = "show-your-work"

Handler = Callable[[argparse.Namespace], int]


class UsageExitParser(argparse.ArgumentParser):
    """An ``ArgumentParser`` whose usage errors exit ``EXIT_USAGE`` instead of 2.

    Subparsers created through ``add_subparsers`` inherit this class (argparse defaults
    ``parser_class`` to ``type(self)``), so ``check --bogus`` exits 64 too.
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"{PROG} {args.verb}: not implemented yet", file=sys.stderr)
    return EXIT_GATE


def _target_path(raw: str | None) -> Path:
    """Absolute TARGET: the given directory, or ``./docs/workflows`` resolved from cwd."""
    if raw is not None:
        return Path(raw).resolve()
    return Path.cwd().joinpath(*DEFAULT_TARGET_PARTS).resolve()


def _file_json(result: FileResult) -> dict[str, Any]:
    return {
        "file": result.file,
        "id": result.id,
        "pass": result.passed,
        "nodes": result.nodes,
        "edges": result.edges,
        "findings": [{"code": f.code, "message": f.message} for f in result.findings],
    }


def report_json(
    verb: str,
    target: Path,
    files: Sequence[FileResult],
    *,
    rendered: bool,
    exit_code: int,
) -> dict[str, Any]:
    """The ``show-your-work.report.v1`` document (section 4), in its fixed key order."""
    passed = sum(1 for f in files if f.passed)
    return {
        "schema": REPORT_SCHEMA,
        "target": target.as_posix(),
        "verb": verb,
        "files": [_file_json(f) for f in files],
        "summary": {
            "total": len(files),
            "passed": passed,
            "failed": len(files) - passed,
            "rendered": rendered,
            "budget": dict(BUDGET),
        },
        "exit_code": exit_code,
    }


def _count(value: int | None) -> str:
    return COUNT_NOT_RENDERED if value is None else str(value)


def _ascii(text: str) -> str:
    """Escape non-ASCII and control characters (ESC, CR, DEL, ...) as ``\\xNN``.

    Human output then never crashes a legacy-codepage console and a hostile file name or
    renderer message cannot inject terminal escape sequences. ``--json`` needs no help:
    ``json.dumps`` escapes both already.
    """
    escaped = text.encode("ascii", "backslashreplace").decode("ascii")
    return "".join(
        f"\\x{ord(ch):02x}" if ord(ch) < MIN_PRINTABLE or ord(ch) == MAX_ASCII else ch
        for ch in escaped
    )


def format_table(report: Report) -> str:
    """The human table: one row per file, findings indented below a failing row."""
    names = [_ascii(f.file) for f in report.files]
    width = max([len("FILE"), *(len(n) for n in names)])
    lines = [f"{'FILE':<{width}}  {'NODES':>5}  {'EDGES':>5}  RESULT"]
    for name, result in zip(names, report.files, strict=True):
        verdict = "pass" if result.passed else "FAIL"
        lines.append(
            f"{name:<{width}}  {_count(result.nodes):>5}  {_count(result.edges):>5}  {verdict}"
        )
        lines.extend(f"    {f.code}: {_ascii(f.message)}" for f in result.findings)
    return "\n".join(lines)


def _emit_json(document: dict[str, Any]) -> None:
    print(json.dumps(document, indent=2))


def _run_check(args: argparse.Namespace) -> int:
    as_json: bool = args.json
    target = _target_path(args.target)
    try:
        if args.target is None and not target.is_dir():
            raise GateError(MSG_NO_DEFAULT_TARGET.format(path=target.as_posix()))
        active: renderer.Renderer | None = None
        if args.render:
            try:
                active = renderer.load()
            except renderer.RendererLoadError as exc:
                raise GateError(MSG_RENDERER_BROKEN.format(reason=exc.reason)) from exc
            if active is None:
                raise GateError(MSG_RENDERER_MISSING)
        report = check_dir(target, renderer=active)
    except GateError as exc:
        print(f"{PROG} check: {_ascii(exc.message)}", file=sys.stderr)
        if as_json:
            _emit_json(
                report_json("check", target, [], rendered=bool(args.render), exit_code=EXIT_GATE)
            )
        return EXIT_GATE

    code = report.exit_code
    if as_json:
        _emit_json(
            report_json(
                "check", report.target, report.files, rendered=report.rendered, exit_code=code
            )
        )
        if not report.rendered:
            print(COUNTS_NOTE, file=sys.stderr)
        return code
    print(format_table(report))
    if not report.rendered:
        print(COUNTS_NOTE)
    print(SUMMARY_LINE.format(passed=report.passed, total=report.total))
    return code


def _add_target(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "target",
        metavar="TARGET",
        nargs="?",
        default=None,
        help="the docs/workflows directory (default: ./docs/workflows)",
    )


def build_parser() -> UsageExitParser:
    """Construct the full argument parser (top level + the three verbs)."""
    parser = UsageExitParser(
        prog=PROG,
        description=(
            "Scaffold, validate and render a docs/workflows/ folder of mermaid workflow diagrams."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    verbs = parser.add_subparsers(dest="verb", metavar="<verb>", required=True)

    init_parser = verbs.add_parser("init", help="scaffold a docs/workflows/ folder")
    _add_target(init_parser)
    init_parser.add_argument(
        "--force", action="store_true", help="overwrite template-origin files only"
    )
    init_parser.set_defaults(handler=_not_implemented)

    check_parser = verbs.add_parser("check", help="validate headers, budget and structure")
    _add_target(check_parser)
    check_parser.add_argument(
        "--render", action="store_true", help="also render with mermaidx for exact counts"
    )
    check_parser.add_argument("--json", action="store_true", help="emit a JSON report")
    check_parser.set_defaults(handler=_run_check)

    render_parser = verbs.add_parser("render", help="regenerate README.md and workflows.json")
    _add_target(render_parser)
    render_parser.add_argument(
        "--check", action="store_true", help="write nothing; exit 1 when output is stale"
    )
    render_parser.add_argument(
        "--png", action="store_true", help="also write light/dark PNG rasters via mermaidx"
    )
    render_parser.add_argument("--json", action="store_true", help="emit a JSON report")
    render_parser.set_defaults(handler=_not_implemented)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code (the console script exits with it)."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # usage errors (64), --help / --version (0)
        code = exc.code
        if code is None:
            return EXIT_OK
        return code if isinstance(code, int) else EXIT_USAGE
    handler: Handler = args.handler
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
