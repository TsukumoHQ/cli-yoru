from __future__ import annotations

import io
import json
import subprocess


def _git(args, cwd):
    subprocess.check_call(["git", *args], cwd=cwd)


def _init_repo_with_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config

    config.save({"server": "http://fake", "token": "rcpt_test_abcd"})

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "a@b.c"], repo)
    _git(["config", "user.name", "tester"], repo)
    return repo


def _spool_files(spool_dir):
    if not spool_dir.is_dir():
        return []
    return sorted(spool_dir.glob("*.json"))


def test_no_active_identity_is_a_silent_noop(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, git_hook_run

    assert config.exists() is False
    rc = git_hook_run.main(["post-commit", "deadbeef"])
    assert rc == 0
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_post_commit_spools_commit_with_diff_and_stat(tmp_path, monkeypatch):
    from yoru_cli import config, git_hook_run

    repo = _init_repo_with_identity(tmp_path, monkeypatch)
    (repo / "a.txt").write_text("hello\n")
    _git(["add", "a.txt"], repo)
    _git(["commit", "-q", "-m", "add a.txt"], repo)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()

    monkeypatch.chdir(repo)
    rc = git_hook_run.main(["post-commit", sha])
    assert rc == 0

    files = _spool_files(config.git_spool_dir())
    assert len(files) == 1
    event = json.loads(files[0].read_text())
    assert event["source"] == "independent:git"
    assert event["kind"] == "commit"
    assert event["content"] == "add a.txt"
    assert "a.txt" in event["diff_unified"]
    assert event["diff_stat"]
    assert event["session_id"].startswith("git-")


def test_post_commit_diff_stat_capped_under_backend_limit(tmp_path, monkeypatch):
    """Regression (round-1 review finding): diff_stat was capped at the
    same 200_000-char limit as diff_unified, but the backend bounds
    diff_stat to 8_000 (models.py) — a wide commit (many files touched)
    produced a --stat summary that blew past 8_000 long before the diff
    text ever would, and the CLI's own capped payload 422'd at ingest."""
    from yoru_cli import config, git_hook_run

    repo = _init_repo_with_identity(tmp_path, monkeypatch)
    (repo / "a.txt").write_text("hello\n")
    _git(["add", "a.txt"], repo)
    _git(["commit", "-q", "-m", "add a.txt"], repo)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()

    monkeypatch.chdir(repo)
    huge_stat = "x" * 20_000
    monkeypatch.setattr(
        git_hook_run,
        "_run_git",
        lambda args, cwd: huge_stat if args[:2] == ["show", "--stat"] else None,
    )
    rc = git_hook_run.main(["post-commit", sha])
    assert rc == 0

    files = _spool_files(config.git_spool_dir())
    assert len(files) == 1
    event = json.loads(files[0].read_text())
    assert len(event["diff_stat"]) <= 8_000


def test_post_commit_agent_relay_child_is_noop(tmp_path, monkeypatch):
    from yoru_cli import config, git_hook_run

    repo = _init_repo_with_identity(tmp_path, monkeypatch)
    (repo / "a.txt").write_text("hello\n")
    _git(["add", "a.txt"], repo)
    _git(["commit", "-q", "-m", "add a.txt"], repo)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()

    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENT_RELAY_CHILD", "1")
    rc = git_hook_run.main(["post-commit", sha])
    assert rc == 0
    assert _spool_files(config.git_spool_dir()) == []


def test_pre_push_force_push_detected_and_spooled(tmp_path, monkeypatch):
    from yoru_cli import config, git_hook_run

    repo = _init_repo_with_identity(tmp_path, monkeypatch)
    (repo / "a.txt").write_text("v1\n")
    _git(["add", "a.txt"], repo)
    _git(["commit", "-q", "-m", "v1"], repo)
    old_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()

    # Amend to produce a non-fast-forward local sha vs. "old" remote sha.
    (repo / "a.txt").write_text("v2\n")
    _git(["add", "a.txt"], repo)
    _git(["commit", "-q", "--amend", "-m", "v2"], repo)
    new_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()

    monkeypatch.chdir(repo)
    stdin_line = f"refs/heads/main {new_sha} refs/heads/main {old_sha}\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_line))
    rc = git_hook_run.main(["pre-push"])
    assert rc == 0

    files = _spool_files(config.git_spool_dir())
    assert len(files) == 1
    event = json.loads(files[0].read_text())
    assert event["force_push"] is True
    assert old_sha[:8] in event["content"] or new_sha[:8] in event["content"]
    # Regression (found wiring B3 slice2.5's session agent-confidence
    # rollup): an unset kind falls through the backend's
    # _infer_kind(tool=None) to "tool_use", which then incorrectly bumps
    # the session's tools_count for an event that never called a tool.
    assert event["kind"] == "message"


def test_pre_push_fast_forward_not_flagged(tmp_path, monkeypatch):
    from yoru_cli import config, git_hook_run

    repo = _init_repo_with_identity(tmp_path, monkeypatch)
    (repo / "a.txt").write_text("v1\n")
    _git(["add", "a.txt"], repo)
    _git(["commit", "-q", "-m", "v1"], repo)
    old_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()

    (repo / "b.txt").write_text("v2\n")
    _git(["add", "b.txt"], repo)
    _git(["commit", "-q", "-m", "v2"], repo)
    new_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()

    monkeypatch.chdir(repo)
    stdin_line = f"refs/heads/main {new_sha} refs/heads/main {old_sha}\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_line))
    rc = git_hook_run.main(["pre-push"])
    assert rc == 0
    assert _spool_files(config.git_spool_dir()) == []


def test_cli_git_hook_run_post_commit_dispatches_correctly(tmp_path, monkeypatch):
    """The `yoru git-hook-run` console-script subcommand (round-2 review
    finding: must be a real entrypoint, not `python3 -m yoru_cli...`, which
    resolves to the wrong interpreter under pipx) — proves the argparse
    wiring passes the hook_kind + extra args through to git_hook_run.main
    unchanged."""
    from yoru_cli import cli, config

    repo = _init_repo_with_identity(tmp_path, monkeypatch)
    (repo / "a.txt").write_text("hello\n")
    _git(["add", "a.txt"], repo)
    _git(["commit", "-q", "-m", "add a.txt"], repo)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()

    monkeypatch.chdir(repo)
    rc = cli.main(["git-hook-run", "post-commit", sha])
    assert rc == 0

    files = _spool_files(config.git_spool_dir())
    assert len(files) == 1
    event = json.loads(files[0].read_text())
    assert event["kind"] == "commit"


def test_pre_push_new_branch_not_flagged(tmp_path, monkeypatch):
    from yoru_cli import config, git_hook_run

    repo = _init_repo_with_identity(tmp_path, monkeypatch)
    (repo / "a.txt").write_text("v1\n")
    _git(["add", "a.txt"], repo)
    _git(["commit", "-q", "-m", "v1"], repo)
    new_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()

    monkeypatch.chdir(repo)
    zero = "0" * 40
    stdin_line = f"refs/heads/new-branch {new_sha} refs/heads/new-branch {zero}\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_line))
    rc = git_hook_run.main(["pre-push"])
    assert rc == 0
    assert _spool_files(config.git_spool_dir()) == []
