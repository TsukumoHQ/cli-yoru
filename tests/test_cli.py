from __future__ import annotations

import subprocess
import sys

import pytest


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "yoru_cli", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_help_lists_both_subcommands() -> None:
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "init" in combined
    assert "tail" in combined


def test_version_prints_expected_string() -> None:
    from yoru_cli import __version__

    result = _run("--version")
    assert result.returncode == 0
    # Assert against the actual package version so a bump never rots this test.
    assert f"yoru {__version__}" in (result.stdout + result.stderr)


@pytest.mark.parametrize("sub", ["init", "tail"])
def test_subcommand_help_exits_zero(sub: str) -> None:
    result = _run(sub, "-h")
    assert result.returncode == 0, result.stderr
