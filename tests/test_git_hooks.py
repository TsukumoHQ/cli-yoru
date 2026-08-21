from __future__ import annotations

import stat
import subprocess


def _init_repo(path):
    subprocess.check_call(["git", "init", "-q"], cwd=path)


def test_repo_root_returns_none_outside_git_repo(tmp_path):
    from yoru_cli import git_hooks

    assert git_hooks.repo_root(str(tmp_path)) is None


def test_repo_root_finds_toplevel_from_subdir(tmp_path):
    from yoru_cli import git_hooks

    _init_repo(tmp_path)
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    root = git_hooks.repo_root(str(sub))
    assert root is not None
    assert root.resolve() == tmp_path.resolve()


def test_install_git_hooks_writes_both_hooks_executable(tmp_path):
    from yoru_cli import git_hooks

    _init_repo(tmp_path)
    installed = git_hooks.install_git_hooks(tmp_path)
    assert sorted(installed) == ["post-commit", "pre-push"]

    for name in ("post-commit", "pre-push"):
        hook_path = tmp_path / ".git" / "hooks" / name
        assert hook_path.is_file()
        assert stat.S_IMODE(hook_path.stat().st_mode) & 0o111  # executable
        body = hook_path.read_text()
        assert "yoru git-hook-run" in body
        assert name in body
        # Round-2 review finding: invoke the real `yoru` console-script, not
        # `python3 -m yoru_cli...` (breaks under pipx — wrong interpreter).
        assert "python3 -m yoru_cli" not in body
        # A hook failure must not vanish silently into `|| true` — it's
        # redirected to a recoverable log first.
        assert "git-hook-errors.log" in body
        assert "|| true" in body


def test_install_git_hooks_idempotent_second_run_is_noop(tmp_path):
    from yoru_cli import git_hooks

    _init_repo(tmp_path)
    first = git_hooks.install_git_hooks(tmp_path)
    assert first
    second = git_hooks.install_git_hooks(tmp_path)
    assert second == []

    hook_path = tmp_path / ".git" / "hooks" / "post-commit"
    body = hook_path.read_text()
    assert body.count("yoru git capture") == 2  # one marker pair, not duplicated


def test_install_git_hooks_chains_pre_existing_hook_content(tmp_path):
    from yoru_cli import git_hooks

    _init_repo(tmp_path)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    existing = hooks_dir / "post-commit"
    existing.write_text("#!/usr/bin/env bash\necho husky-was-here\n")
    existing.chmod(0o755)

    installed = git_hooks.install_git_hooks(tmp_path)
    assert "post-commit" in installed

    body = existing.read_text()
    assert "echo husky-was-here" in body  # never clobbered
    assert "yoru git-hook-run" in body  # chained onto the end
    assert body.index("echo husky-was-here") < body.index("yoru git-hook-run")
