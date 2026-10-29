"""The ``init`` logic (plan sections 3.6 and 4, design decision DD6): scaffold a workflows folder.

:func:`scaffold` creates TARGET when missing and copies the two templates -- ``intro.md`` and
``example-workflow.mmd`` -- **only where absent**. An existing file is never overwritten, with one
exception: under ``force`` a file whose last line is its template marker (section 3.3:
``%% show-your-work:template`` for a diagram, ``<!-- show-your-work:template -->`` for
``intro.md``) is template-origin and is replaced with the current template. A file without the
marker, a symlink, or anything that is not a regular file is user content and is left alone.

The templates ship in the wheel under ``show_your_work/templates/workflows/`` (``pyproject.toml``
force-includes the repo's ``templates/workflows/``) and are found through
:mod:`importlib.resources`; an editable install of a source checkout falls back to the repo-root
``templates/workflows/`` beside ``pyproject.toml``. Templates are written as LF bytes whatever the
checkout's ``core.autocrlf``, so a repeat ``init`` changes no bytes.

Running ``check`` + ``render`` on the result is the CLI's job (:mod:`show_your_work.cli`), through
the same :func:`show_your_work.render.render_dir` the ``render`` verb uses.
"""

from __future__ import annotations

import contextlib
import os
import stat
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from show_your_work.checks import GateError
from show_your_work.constants import (
    INIT_CREATED,
    INIT_KEPT,
    INIT_REPLACED,
    MAX_TEMPLATE_BYTES,
    MSG_CANNOT_CREATE,
    MSG_CANNOT_READ,
    MSG_CANNOT_WRITE,
    MSG_TARGET_NOT_DIR,
    MSG_TEMPLATES_MISSING,
    PACKAGE,
    SOURCE_CHECKOUT_MARKER,
    STATE_CURRENT,
    TEMPLATE_FILES,
    TEMPLATE_PACKAGE_PARTS,
)
from show_your_work.header import normalize_newlines
from show_your_work.render import write_atomic

__all__ = ["ScaffoldAction", "is_template_origin", "load_templates", "scaffold"]


@dataclass(frozen=True, slots=True)
class ScaffoldAction:
    """What ``init`` did with one template: created, kept, replaced or up to date."""

    name: str
    state: str


def _reason(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def _checkout_template_dir() -> Path | None:
    """``<repo>/templates/workflows`` when this module runs from a source checkout's ``src/``."""
    root = Path(__file__).resolve().parents[2]
    candidate = root.joinpath(*TEMPLATE_PACKAGE_PARTS)
    if (root / SOURCE_CHECKOUT_MARKER).is_file() and candidate.is_dir():
        return candidate
    return None


def load_templates() -> dict[str, bytes]:
    """Every template's bytes (LF-normalized), keyed by file name in ``TEMPLATE_FILES`` order.

    Packaged data wins; the source-checkout directory is consulted only when the package carries
    no templates (an editable install). A missing template is a broken install: exit 2.
    """
    packaged = resources.files(PACKAGE).joinpath(*TEMPLATE_PACKAGE_PARTS)
    checkout = None if packaged.is_dir() else _checkout_template_dir()
    templates: dict[str, bytes] = {}
    for name in TEMPLATE_FILES:
        try:
            if checkout is not None:
                raw = (checkout / name).read_bytes()
            else:
                raw = packaged.joinpath(name).read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise GateError(MSG_TEMPLATES_MISSING.format(name=name)) from exc
        templates[name] = normalize_newlines(text).encode("utf-8")
    return templates


def is_template_origin(path: Path, marker: str) -> bool:
    """True when ``path`` is a regular file whose last line is exactly ``marker`` (section 3.3).

    The comparison is exact after CRLF -> LF normalization: one final line break is allowed, but
    trailing blank lines or trailing spaces after the marker make the file user content. Symlinks,
    directories and files larger than ``MAX_TEMPLATE_BYTES`` are never template-origin.
    """
    try:
        if path.is_symlink() or not path.is_file():
            return False
        with path.open("rb") as handle:
            raw = handle.read(MAX_TEMPLATE_BYTES + 1)
    except OSError as exc:
        raise GateError(MSG_CANNOT_READ.format(path=path.as_posix(), reason=_reason(exc))) from exc
    if len(raw) > MAX_TEMPLATE_BYTES:
        return False
    text = normalize_newlines(raw.decode("utf-8", errors="replace")).removesuffix("\n")
    return text.rsplit("\n", 1)[-1] == marker


def _ensure_dir(target: Path) -> None:
    if target.exists() and not target.is_dir():
        raise GateError(MSG_TARGET_NOT_DIR.format(path=target.as_posix()))
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GateError(
            MSG_CANNOT_CREATE.format(path=target.as_posix(), reason=_reason(exc))
        ) from exc


def _create(path: Path, data: bytes) -> bool:
    """Write ``data`` to a NEW file; ``False`` (nothing written) if ``path`` appeared meanwhile."""
    try:
        handle = path.open("xb")
    except FileExistsError:
        return False
    except OSError as exc:
        raise GateError(MSG_CANNOT_WRITE.format(path=path.as_posix(), reason=_reason(exc))) from exc
    try:
        with handle:
            handle.write(data)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise GateError(MSG_CANNOT_WRITE.format(path=path.as_posix(), reason=_reason(exc))) from exc
    return True


def _current_bytes(path: Path) -> bytes | None:
    try:
        with path.open("rb") as handle:
            return handle.read(MAX_TEMPLATE_BYTES + 1)
    except OSError:
        return None


def _permission_bits(path: Path) -> int | None:
    """The existing file's permission bits, so a ``--force`` replace keeps them."""
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def scaffold(target: Path, *, force: bool = False) -> tuple[ScaffoldAction, ...]:
    """Create ``target`` if missing and copy each template where absent (DD6).

    With ``force``, an existing template-origin file (its last line is the template marker) is
    replaced when its bytes differ from the template. Raises :class:`GateError` (exit 2) when
    ``target`` is a file, cannot be created, or a template cannot be read or written.
    """
    templates = load_templates()
    _ensure_dir(target)
    actions: list[ScaffoldAction] = []
    for name, marker in TEMPLATE_FILES.items():
        path = target / name
        data = templates[name]
        if not os.path.lexists(path):
            state = INIT_CREATED if _create(path, data) else INIT_KEPT
        elif force and is_template_origin(path, marker):
            if _current_bytes(path) == data:
                state = STATE_CURRENT
            else:
                write_atomic(path, data.decode("utf-8"), mode=_permission_bits(path))
                state = INIT_REPLACED
        else:
            state = INIT_KEPT
        actions.append(ScaffoldAction(name, state))
    return tuple(actions)
