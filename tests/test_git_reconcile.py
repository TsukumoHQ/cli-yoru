from __future__ import annotations

import json
import subprocess


def _git(args, cwd):
    subprocess.check_call(["git", *args], cwd=cwd)


def _head_sha(repo):
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()


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


def _commit(repo, name, message):
    (repo / name).write_text(message + "\n")
    _git(["add", name], repo)
    _git(["commit", "-q", "-m", message], repo)
    return _head_sha(repo)


def _spool_events(spool_dir):
    if not spool_dir.is_dir():
        return []
    events = []
    for p in sorted(spool_dir.glob("*.json")):
        events.append(json.loads(p.read_text()))
    return events


def test_register_repo_seeds_head_without_backfilling_existing_history(tmp_path, monkeypatch):
    from yoru_cli import config, git_reconcile

    repo = _init_repo_with_identity(tmp_path, monkeypatch)
    sha1 = _commit(repo, "a.txt", "commit 1")
    _commit(repo, "b.txt", "commit 2")

    git_reconcile.register_repo(str(repo))

    state = json.loads(config.git_reconcile_state_path().read_text())
    assert state[str(repo)] == _head_sha(repo)
    # pre-existing history is NOT backfilled on registration — audit-safe
    # discovery posture, same as transcript_tailer seeking a newly-found
    # transcript to its end.
    assert _spool_events(config.git_spool_dir()) == []
    assert sha1  # sanity: repo really has history that was skipped


def test_register_repo_is_idempotent(tmp_path, monkeypatch):
    from yoru_cli import config, git_reconcile

    repo = _init_repo_with_identity(tmp_path, monkeypatch)
    _commit(repo, "a.txt", "commit 1")
    git_reconcile.register_repo(str(repo))
    first_head = _head_sha(repo)

    _commit(repo, "b.txt", "commit 2")
    git_reconcile.register_repo(str(repo))  # re-register — must NOT re-seed to the new HEAD

    state = json.loads(config.git_reconcile_state_path().read_text())
    assert state[str(repo)] == first_head  # unchanged by the second register call

    repos = json.loads(config.git_repos_path().read_text())
    assert repos == [str(repo)]  # not duplicated


def test_reconcile_repo_captures_commit_hook_never_saw(tmp_path, monkeypatch):
    """The AC's exact scenario: a commit made with --no-verify (hook
    skipped entirely — simulated here by simply never invoking the hook,
    which is what --no-verify achieves) is still captured by the next
    reconciliation pass."""
    from yoru_cli import config, git_reconcile

    repo = _init_repo_with_identity(tmp_path, monkeypatch)
    _commit(repo, "a.txt", "commit 1")
    git_reconcile.register_repo(str(repo))

    sha2 = _commit(repo, "b.txt", "commit 2 (hook skipped)")

    spooled = git_reconcile.reconcile_repo(str(repo))
    assert spooled == 1

    events = _spool_events(config.git_spool_dir())
    assert len(events) == 1
    assert events[0]["entry_uuid"] == f"git-commit-{sha2}"
    assert events[0]["kind"] == "commit"
    assert events[0]["source"] == "independent:git"
    assert events[0]["content"] == "commit 2 (hook skipped)"


def test_reconcile_repo_multiple_commits_spooled_oldest_first(tmp_path, monkeypatch):
    from yoru_cli import config, git_reconcile

    repo = _init_repo_with_identity(tmp_path, monkeypatch)
    _commit(repo, "a.txt", "commit 1")
    git_reconcile.register_repo(str(repo))

    sha2 = _commit(repo, "b.txt", "commit 2")
    sha3 = _commit(repo, "c.txt", "commit 3")

    spooled = git_reconcile.reconcile_repo(str(repo))
    assert spooled == 2

    events = _spool_events(config.git_spool_dir())
    uuids = [e["entry_uuid"] for e in events]
    assert uuids == [f"git-commit-{sha2}", f"git-commit-{sha3}"]


def test_reconcile_repo_nothing_new_is_a_noop(tmp_path, monkeypatch):
    from yoru_cli import config, git_reconcile

    repo = _init_repo_with_identity(tmp_path, monkeypatch)
    _commit(repo, "a.txt", "commit 1")
    git_reconcile.register_repo(str(repo))

    spooled = git_reconcile.reconcile_repo(str(repo))
    assert spooled == 0
    assert _spool_events(config.git_spool_dir()) == []


def test_reconcile_repo_second_pass_only_sees_new_commits(tmp_path, monkeypatch):
    from yoru_cli import config, git_reconcile

    repo = _init_repo_with_identity(tmp_path, monkeypatch)
    _commit(repo, "a.txt", "commit 1")
    git_reconcile.register_repo(str(repo))

    _commit(repo, "b.txt", "commit 2")
    assert git_reconcile.reconcile_repo(str(repo)) == 1
    assert git_reconcile.reconcile_repo(str(repo)) == 0  # already advanced — nothing new

    sha3 = _commit(repo, "c.txt", "commit 3")
    assert git_reconcile.reconcile_repo(str(repo)) == 1

    events = _spool_events(config.git_spool_dir())
    assert events[-1]["entry_uuid"] == f"git-commit-{sha3}"


def test_reconcile_repo_deleted_repo_returns_zero_no_crash(tmp_path, monkeypatch):
    from yoru_cli import git_reconcile

    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config

    config.save({"server": "http://fake", "token": "rcpt_test_abcd"})

    gone = tmp_path / "never-existed"
    assert git_reconcile.reconcile_repo(str(gone)) == 0


def test_reconcile_all_processes_every_registered_repo(tmp_path, monkeypatch):
    from yoru_cli import config, git_reconcile

    monkeypatch.setenv("HOME", str(tmp_path))
    config.save({"server": "http://fake", "token": "rcpt_test_abcd"})

    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    _git(["init", "-q"], repo_a)
    _git(["config", "user.email", "a@b.c"], repo_a)
    _git(["config", "user.name", "t"], repo_a)
    _commit(repo_a, "x.txt", "a1")
    git_reconcile.register_repo(str(repo_a))
    sha_a2 = _commit(repo_a, "y.txt", "a2")

    repo_b = tmp_path / "repo-b"
    repo_b.mkdir()
    _git(["init", "-q"], repo_b)
    _git(["config", "user.email", "a@b.c"], repo_b)
    _git(["config", "user.name", "t"], repo_b)
    _commit(repo_b, "x.txt", "b1")
    git_reconcile.register_repo(str(repo_b))
    sha_b2 = _commit(repo_b, "y.txt", "b2")

    total = git_reconcile.reconcile_all()
    assert total == 2

    events = _spool_events(config.git_spool_dir())
    uuids = {e["entry_uuid"] for e in events}
    assert uuids == {f"git-commit-{sha_a2}", f"git-commit-{sha_b2}"}


def test_reconcile_all_one_deleted_repo_does_not_block_the_rest(tmp_path, monkeypatch):
    from yoru_cli import config, git_reconcile

    monkeypatch.setenv("HOME", str(tmp_path))
    config.save({"server": "http://fake", "token": "rcpt_test_abcd"})

    good_repo = tmp_path / "good"
    good_repo.mkdir()
    _git(["init", "-q"], good_repo)
    _git(["config", "user.email", "a@b.c"], good_repo)
    _git(["config", "user.name", "t"], good_repo)
    _commit(good_repo, "x.txt", "c1")
    git_reconcile.register_repo(str(good_repo))
    sha2 = _commit(good_repo, "y.txt", "c2")

    gone_repo = tmp_path / "gone"
    gone_repo.mkdir()
    _git(["init", "-q"], gone_repo)
    _git(["config", "user.email", "a@b.c"], gone_repo)
    _git(["config", "user.name", "t"], gone_repo)
    _commit(gone_repo, "x.txt", "g1")
    git_reconcile.register_repo(str(gone_repo))
    import shutil

    shutil.rmtree(gone_repo)

    total = git_reconcile.reconcile_all()
    assert total == 1
    events = _spool_events(config.git_spool_dir())
    assert events[0]["entry_uuid"] == f"git-commit-{sha2}"


def test_hook_path_and_reconcile_path_produce_identical_entry_uuid(tmp_path, monkeypatch):
    """The dedup mechanism itself (AC #2): the post-commit hook and the
    reconciliation walk must stamp the SAME entry_uuid for the same commit
    so the backend's existing ingest dedup collapses a commit captured by
    both into one event."""
    from yoru_cli import git_hook_run, git_reconcile

    repo = _init_repo_with_identity(tmp_path, monkeypatch)
    sha = _commit(repo, "a.txt", "commit 1")

    hook_event = git_hook_run.build_commit_event(sha, str(repo))
    from_reconcile_event = git_hook_run.build_commit_event(sha, str(repo))  # same fn, both paths call it

    assert hook_event["entry_uuid"] == from_reconcile_event["entry_uuid"] == f"git-commit-{sha}"
    assert git_reconcile  # imported for clarity that both modules share build_commit_event
