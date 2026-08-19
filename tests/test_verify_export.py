"""M5b-2: `yoru verify-export` — offline DSSE/Ed25519 verify + chain re-walk.

The bundles here are signed in-test with `cryptography` using the SAME wire
format the (AGPL) backend and dokan sign with — the CLI's verifier is an
independent MIT reimplementation, so a green test proves the two agree. The
frozen KAT (same fixed seed as the backend's test) is the cross-scheme anchor.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from yoru_cli import dsse_verify, verify_export_cmd
from yoru_cli.dsse_verify import Verdict

# Same fixed seed 0x00..0x1f as the backend KAT — proves both sign identically.
KAT_SEED_B64 = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
KAT_PUB_B64 = "A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg="
KAT_PAYLOAD = b'{"hello":"yoru"}'
KAT_SIG = (
    "bf8922bde24cf08efac18ca925941731b702bc3b1166989e8edffcddf2b081a6"
    "ddc36aa9c9dbe00b4a118d02a6d60173488994636d004af0c288659cc106fe0b"
)


def _sign_bundle(statement: dict, seed_b64: str = KAT_SEED_B64) -> dict:
    seed = base64.standard_b64decode(seed_b64)
    priv = Ed25519PrivateKey.from_private_bytes(seed)
    pub_raw = priv.public_key().public_bytes_raw()
    pub_b64 = base64.standard_b64encode(pub_raw).decode("ascii")
    keyid = hashlib.sha256(pub_raw).hexdigest()[:16]
    payload = json.dumps(
        statement, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    sig = priv.sign(
        dsse_verify.dsse_pae(dsse_verify.DSSE_PAYLOAD_TYPE, payload)
    ).hex()
    return {
        **statement,
        "dsse": {
            "payloadType": dsse_verify.DSSE_PAYLOAD_TYPE,
            "payload": base64.standard_b64encode(payload).decode("ascii"),
            "signatures": [{"keyid": keyid, "sig": sig}],
        },
        "ed25519": {"public_key": pub_b64, "keyid": keyid},
    }


def _ns(path, fingerprint=None):
    return argparse.Namespace(bundle=str(path), pubkey_fingerprint=fingerprint)


def _write(tmp_path, bundle) -> str:
    p = tmp_path / "bundle.json"
    p.write_text(json.dumps(bundle))
    return p


# ── cross-scheme anchor ─────────────────────────────────────────────────────

def test_kat_cross_scheme_verify():
    """The CLI's independent verifier accepts the backend/dokan-produced KAT."""
    assert dsse_verify.ed_verify(
        KAT_PUB_B64, dsse_verify.DSSE_PAYLOAD_TYPE, KAT_PAYLOAD, KAT_SIG
    )
    assert dsse_verify.fingerprint(KAT_PUB_B64) == hashlib.sha256(
        base64.standard_b64decode(KAT_PUB_B64)
    ).hexdigest()


# ── verdict matrix ──────────────────────────────────────────────────────────

def _good_statement():
    return {
        "predicate": {
            "session_count": 1,
            "sessions": [
                {
                    "session_id": "s1",
                    "events": [
                        {"entry_hash": "h1", "prev_hash": None},
                        {"entry_hash": "h2", "prev_hash": "h1"},
                        {"entry_hash": "h3", "prev_hash": "h2"},
                    ],
                }
            ],
        }
    }


def test_verified(tmp_path, capsys):
    bundle = _sign_bundle(_good_statement())
    rc = verify_export_cmd.run(_ns(_write(tmp_path, bundle)))
    out = capsys.readouterr().out
    assert rc == 0
    assert "VERIFIED" in out
    assert "chain: links consistent" in out


def test_tampered_signature(tmp_path, capsys):
    bundle = _sign_bundle(_good_statement())
    # Corrupt the signed payload without re-signing → signature no longer valid.
    bundle["dsse"]["payload"] = base64.standard_b64encode(
        b'{"predicate":{"sessions":[]}}'
    ).decode("ascii")
    rc = verify_export_cmd.run(_ns(_write(tmp_path, bundle)))
    assert rc == Verdict.TAMPERED.exit_code
    assert "TAMPERED" in capsys.readouterr().out


def test_untrusted_wrong_fingerprint(tmp_path, capsys):
    bundle = _sign_bundle(_good_statement())
    rc = verify_export_cmd.run(_ns(_write(tmp_path, bundle), fingerprint="deadbeef"))
    assert rc == Verdict.UNTRUSTED.exit_code
    assert "UNTRUSTED" in capsys.readouterr().out


def test_verified_with_correct_pin(tmp_path, capsys):
    bundle = _sign_bundle(_good_statement())
    fp = dsse_verify.fingerprint(bundle["ed25519"]["public_key"])
    rc = verify_export_cmd.run(_ns(_write(tmp_path, bundle), fingerprint=fp))
    out = capsys.readouterr().out
    assert rc == 0
    assert "MATCH" in out and "VERIFIED" in out


def test_inconclusive_unsigned(tmp_path, capsys):
    rc = verify_export_cmd.run(_ns(_write(tmp_path, {"predicate": {"sessions": []}})))
    assert rc == Verdict.INCONCLUSIVE.exit_code
    assert "INCONCLUSIVE" in capsys.readouterr().out


def test_broken_chain_link(tmp_path, capsys):
    st = _good_statement()
    st["predicate"]["sessions"][0]["events"][2]["prev_hash"] = "WRONG"
    bundle = _sign_bundle(st)  # signature is valid over the broken content
    rc = verify_export_cmd.run(_ns(_write(tmp_path, bundle)))
    out = capsys.readouterr().out
    assert rc == Verdict.TAMPERED.exit_code
    assert "prev_hash does not link" in out


def test_toplevel_mirror_divergence(tmp_path, capsys):
    bundle = _sign_bundle(_good_statement())
    # Edit the human-readable top-level copy only — the signature still verifies,
    # but the readable predicate no longer matches the signed one.
    bundle["predicate"] = {"session_count": 999, "sessions": []}
    rc = verify_export_cmd.run(_ns(_write(tmp_path, bundle)))
    out = capsys.readouterr().out
    assert rc == Verdict.TAMPERED.exit_code
    assert "differs from the SIGNED payload" in out


def test_missing_file(tmp_path, capsys):
    rc = verify_export_cmd.run(_ns(tmp_path / "nope.json"))
    assert rc == 2
