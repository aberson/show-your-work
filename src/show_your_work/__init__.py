"""show-your-work -- scaffold, validate and render a per-project ``docs/workflows/`` folder.

Package-level exports so far: the single header parser (:mod:`show_your_work.header`). Other
modules: the ``check`` logic in :mod:`show_your_work.checks`, the optional renderer wrapper in
:mod:`show_your_work.renderer`, the generated README / ``workflows.json`` builders in
:mod:`show_your_work.render`, the ``init`` scaffolder in :mod:`show_your_work.scaffold` and the
CLI entry point in :mod:`show_your_work.cli`.
"""

from __future__ import annotations

from show_your_work.header import Header, HeaderError, HeaderFinding, check_header, parse_header

__version__ = "0.1.0"

__all__ = [
    "Header",
    "HeaderError",
    "HeaderFinding",
    "__version__",
    "check_header",
    "parse_header",
]
