"""argparse entry point for ``show-your-work`` (``docs/plans/master_plan.md`` section 4).

The argument surface of the three verbs is final:

* ``show-your-work init [TARGET] [--force]``
* ``show-your-work check [TARGET] [--render] [--json]``
* ``show-your-work render [TARGET] [--check] [--png] [--json]``

Every usage error -- no verb, unknown verb, unknown flag, missing value -- prints the usage and
exits ``EXIT_USAGE`` (64): argparse's own default is 2, which would collide with ``EXIT_GATE``
("gate could not run"), so :class:`UsageExitParser` overrides ``ArgumentParser.error``. The verb
handlers are stubs in build step 1 (they report "not implemented" and exit ``EXIT_GATE``); later
steps wire them.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import NoReturn

from show_your_work import __version__
from show_your_work.constants import EXIT_GATE, EXIT_OK, EXIT_USAGE

__all__ = ["UsageExitParser", "build_parser", "main"]

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
    check_parser.set_defaults(handler=_not_implemented)

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
