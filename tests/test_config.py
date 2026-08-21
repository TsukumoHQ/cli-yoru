from __future__ import annotations

import json
import stat


def test_synthesize_identity_id_deterministic_and_distinct(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config

    a1 = config.synthesize_identity_id("rcpt_AAA")
    a2 = config.synthesize_identity_id("rcpt_AAA")
    b = config.synthesize_identity_id("rcpt_BBB")
    assert a1 == a2
    assert a1 != b


def test_save_load_exists_remove_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config

    assert config.exists() is False
    assert config.load() is None

    config.save({"identity_id": "id-1", "server": "http://fake", "token": "rcpt_A"})
    assert config.exists() is True
    data = config.load()
    assert data["server"] == "http://fake"
    assert data["token"] == "rcpt_A"
    assert data["identity_id"] == "id-1"

    config.remove()
    assert config.exists() is False
    assert config.load() is None


def test_slot_dir_and_file_permissions(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config

    config.save({"identity_id": "id-1", "server": "http://fake", "token": "rcpt_A"})

    root = tmp_path / ".config" / "yoru"
    slot_dir = root / "identities" / "id-1"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(slot_dir.stat().st_mode) == 0o700
    cfg_file = slot_dir / "config.json"
    assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600


def test_two_identities_coexist_without_collision(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config

    config.save({"identity_id": "id-1", "identity_label": "laptop", "server": "http://a", "token": "rcpt_A"})
    config.save({"identity_id": "id-2", "identity_label": "desktop", "server": "http://b", "token": "rcpt_B"})

    # save() makes the most recent one active — but the first identity's
    # slot must survive on disk, untouched.
    assert config.active_identity_id() == "id-2"
    assert config.load()["token"] == "rcpt_B"

    config.set_active("id-1")
    assert config.load()["token"] == "rcpt_A"
    assert config.load()["server"] == "http://a"

    identities = config.list_identities()
    ids = {i["identity_id"] for i in identities}
    assert ids == {"id-1", "id-2"}


def test_legacy_flat_config_auto_migrates_to_slot(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config

    legacy_dir = tmp_path / ".config" / "yoru"
    legacy_dir.mkdir(parents=True)
    legacy = legacy_dir / "config.json"
    legacy.write_text(json.dumps({"server": "http://old", "token": "rcpt_OLD"}), encoding="utf-8")

    assert config.exists() is True  # migration happens transparently
    data = config.load()
    assert data["server"] == "http://old"
    assert data["token"] == "rcpt_OLD"

    # Legacy file is DELETED once migrated — leaving it around lets it get
    # re-migrated (and the identity re-activated) after a later logout.
    assert not legacy.exists()

    # One-time: migrating twice must not duplicate/overwrite anything odd.
    active_before = config.active_identity_id()
    config.load()
    assert config.active_identity_id() == active_before


def test_logout_after_legacy_migration_does_not_resurrect_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config

    legacy_dir = tmp_path / ".config" / "yoru"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.json").write_text(
        json.dumps({"server": "http://old", "token": "rcpt_OLD"}), encoding="utf-8"
    )

    assert config.exists() is True  # triggers migration
    config.remove()  # logout

    assert config.exists() is False
    assert config.load() is None
    assert config.active_identity_id() is None
    # The whole point: a subsequent config access must NOT resurrect it.
    assert config.exists() is False


def test_legacy_migration_preserves_explicit_identity_id(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config

    legacy_dir = tmp_path / ".config" / "yoru"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.json").write_text(
        json.dumps({"server": "http://old", "token": "rcpt_OLD", "identity_id": "server-id-123"}),
        encoding="utf-8",
    )

    assert config.active_identity_id() == "server-id-123"


def test_enforce_and_tail_state_paths_follow_active_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config

    config.save({"identity_id": "id-1", "server": "http://a", "token": "rcpt_A"})
    p1_enforce = config.enforce_marker_path()
    p1_tail = config.tail_state_path()
    assert "id-1" in str(p1_enforce)
    assert "id-1" in str(p1_tail)

    config.save({"identity_id": "id-2", "server": "http://b", "token": "rcpt_B"})
    p2_enforce = config.enforce_marker_path()
    p2_tail = config.tail_state_path()
    assert "id-2" in str(p2_enforce)
    assert "id-2" in str(p2_tail)
    assert p1_enforce != p2_enforce
    assert p1_tail != p2_tail


def test_enforce_and_tail_state_paths_fallback_when_no_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config

    assert config.active_identity_id() is None
    assert config.enforce_marker_path() == tmp_path / ".config" / "yoru" / "enforce.json"
    assert config.tail_state_path() == tmp_path / ".config" / "yoru" / "tail-state.json"
