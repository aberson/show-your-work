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
``exit_code`` 2 still goes to stdout).

``render`` is wired (exit 0 written / up to date, 1 the check set fails -- nothing is written --
or ``--check`` found a stale output, 2 the gate could not run or an output could not be written).
It prints the same check report, then one ``<file>: written | up to date | stale`` line per
generated output (to stderr under ``--json``, so stdout stays pure JSON with ``verb`` render).
``--png`` loads mermaidx (absent or broken: exit 2 with the documented message), checks WITH it
(anchors first), then also writes ``rasters/<id>-{light,dark}.png``, deletes orphan PNGs there and
fills ``rasters`` in ``workflows.json``; each raster gets its own ``<path>: <state>`` line.

``init`` is wired (exit 0 scaffolded, 1 the scaffolded set fails check -- nothing is rendered,
2 TARGET is a file / cannot be created / a template cannot be read or written). It creates TARGET
(default ``./docs/workflows``, created when missing), copies the templates only where absent
(``--force`` replaces template-origin files only, design decision DD6), prints one
``<file>: created | kept | replaced | up to date`` line per template, then runs ``check`` and
``render`` on the result and prints the same report and output lines as ``render``.
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
    MSG_INIT_CHECK_FAILED,
    MSG_NO_DEFAULT_TARGET,
    MSG_PNG_RENDERER_BROKEN,
    MSG_PNG_RENDERER_MISSING,
    MSG_RENDER_REFUSED,
    MSG_RENDERER_BROKEN,
    MSG_RENDERER_MISSING,
    MSG_STALE,
    REPORT_SCHEMA,
    SUMMARY_LINE,
)
from show_your_work.render import RenderResult, render_dir
from show_your_work.scaffold import scaffold

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
    _emit_report("check", report, code, as_json=as_json)
    return code


def _emit_report(verb: str, report: Report, code: int, *, as_json: bool) -> None:
    """Print a finished report: JSON on stdout (NOTE on stderr), or table + NOTE + summary."""
    if as_json:
        _emit_json(
            report_json(verb, report.target, report.files, rendered=report.rendered, exit_code=code)
        )
        if not report.rendered:
            print(COUNTS_NOTE, file=sys.stderr)
        return
    print(format_table(report))
    if not report.rendered:
        print(COUNTS_NOTE)
    print(SUMMARY_LINE.format(passed=report.passed, total=report.total))


def _run_render(args: argparse.Namespace) -> int:
    as_json: bool = args.json
    target = _target_path(args.target)
    try:
        if args.target is None and not target.is_dir():
            raise GateError(MSG_NO_DEFAULT_TARGET.format(path=target.as_posix()))
        rasterizer: renderer.RasterRenderer | None = None
        if args.png:
            try:
                rasterizer = renderer.load()
            except renderer.RendererLoadError as exc:
                raise GateError(MSG_PNG_RENDERER_BROKEN.format(reason=exc.reason)) from exc
            if rasterizer is None:
                raise GateError(MSG_PNG_RENDERER_MISSING)
        result = render_dir(target, check_only=args.check, rasterizer=rasterizer)
    except GateError as exc:
        print(f"{PROG} render: {_ascii(exc.message)}", file=sys.stderr)
        if as_json:
            _emit_json(
                report_json("render", target, [], rendered=bool(args.png), exit_code=EXIT_GATE)
            )
        return EXIT_GATE

    report = result.report
    code = result.exit_code
    _emit_report("render", report, code, as_json=as_json)
    if result.refused:
        print(
            f"{PROG} render: "
            + MSG_RENDER_REFUSED.format(failed=report.failed, total=report.total),
            file=sys.stderr,
        )
        return code
    _emit_outputs(result, args.target, as_json=as_json, png=bool(args.png))
    return code


def _emit_outputs(
    result: RenderResult, raw_target: str | None, *, as_json: bool, png: bool = False
) -> None:
    """One ``<file>: <state>`` line per generated output, then a hint per stale output."""
    status = sys.stderr if as_json else sys.stdout
    words = [PROG, "render", *([raw_target] if raw_target is not None else [])]
    command = " ".join([*words, *(["--png"] if png else [])])
    for output in result.outputs:
        print(f"{output.name}: {output.state}", file=status)
    for name in result.stale:
        print(_ascii(MSG_STALE.format(name=name, command=command)), file=sys.stderr)


def _run_init(args: argparse.Namespace) -> int:
    target = _target_path(args.target)
    try:
        actions = scaffold(target, force=args.force)
        for action in actions:
            print(f"{action.name}: {action.state}")
        result = render_dir(target)
    except GateError as exc:
        print(f"{PROG} init: {_ascii(exc.message)}", file=sys.stderr)
        return EXIT_GATE

    report = result.report
    code = result.exit_code
    _emit_report("init", report, code, as_json=False)
    if result.refused:
        message = MSG_INIT_CHECK_FAILED.format(
            failed=report.failed, total=report.total, path=target.as_posix()
        )
        print(f"{PROG} init: {_ascii(message)}", file=sys.stderr)
        return code
    _emit_outputs(result, args.target, as_json=False)
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
    init_parser.set_defaults(handler=_run_init)

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
        "--png",
        action="store_true",
        help="also write light/dark PNG rasters: via mermaidx, or from the house SVGs for a "
        "diagram with a layout sidecar",
    )
    render_parser.add_argument("--json", action="store_true", help="emit a JSON report")
    render_parser.set_defaults(handler=_run_render)

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
