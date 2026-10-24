"""CLI usage-error contract (plan section 4): every usage error exits 64 with the usage text."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from show_your_work import __version__
from show_your_work.cli import main
from show_your_work.constants import EXIT_OK, EXIT_USAGE


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["bogus"],
        ["check", "--bogus"],
        ["check", "a", "b"],
    ],
)
def test_usage_errors_exit_64_with_usage(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == EXIT_USAGE
    err = capsys.readouterr().err
    assert err.startswith("usage: show-your-work")
    assert "error:" in err


def test_help_and_version_exit_0(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == EXIT_OK
    assert "usage: show-your-work" in capsys.readouterr().out
    assert main(["--version"]) == EXIT_OK
    assert capsys.readouterr().out.strip() == f"show-your-work {__version__}"


def _console_script() -> Path:
    scripts = Path(sys.executable).parent
    found = shutil.which("show-your-work", path=str(scripts))
    if found is None:
        pytest.fail(f"console script 'show-your-work' not installed next to {sys.executable}")
    return Path(found)


@pytest.mark.parametrize("argv", [[], ["check", "--bogus"]])
def test_console_script_usage_errors_exit_64(argv: list[str]) -> None:
    proc = subprocess.run(
        [str(_console_script()), *argv],
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert proc.returncode == EXIT_USAGE
    assert proc.stderr.startswith("usage: show-your-work")
