"""show-your-work -- scaffold, validate and render a per-project ``docs/workflows/`` folder.

Public surface so far: the single header parser in :mod:`show_your_work.header` and the CLI
entry point in :mod:`show_your_work.cli`. ``check`` / ``render`` / ``init`` land in later build
steps (``docs/plans/master_plan.md`` section 11).
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
