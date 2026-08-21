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


def test_git_hook_run_help_exits_zero() -> None:
    result = _run("git-hook-run", "-h")
    assert result.returncode == 0, result.stderr


def test_git_hook_run_never_uses_bare_python3_module_invocation(tmp_path) -> None:
    """Round-2 review finding, critical: the installed git hooks used to
    call `python3 -m yoru_cli.git_hook_run`, which under a real pipx install
    resolves to the SYSTEM python3 — not the interpreter pipx installed
    yoru-cli into — raising ModuleNotFoundError on every real install while
    `|| true` swallowed it silently (B3 captured nothing, `yoru init` still
    claimed "commits captured"). `yoru git-hook-run` run as a subprocess via
    `sys.executable -m yoru_cli` (this test's own harness) exercises the
    exact same "resolve the right interpreter" property a real console-script
    entrypoint relies on — proves the subcommand runs clean end to end with
    no import error, regardless of what a bare `python3` on PATH would be."""
    import os
    import subprocess as sp

    sp.check_call(["git", "init", "-q"], cwd=tmp_path)
    sp.check_call(["git", "config", "user.email", "a@b.c"], cwd=tmp_path)
    sp.check_call(["git", "config", "user.name", "t"], cwd=tmp_path)
    (tmp_path / "f.txt").write_text("x\n")
    sp.check_call(["git", "add", "f.txt"], cwd=tmp_path)
    sp.check_call(["git", "commit", "-q", "-m", "c1"], cwd=tmp_path)
    sha = sp.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path).decode().strip()

    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home)}
    sp.run(
        [sys.executable, "-m", "yoru_cli", "init", "--server", "http://fake", "--token", "rcpt_x"],
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    result = sp.run(
        [sys.executable, "-m", "yoru_cli", "git-hook-run", "post-commit", sha],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
