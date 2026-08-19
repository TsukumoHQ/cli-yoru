"""Offline verification of a yoru signed audit-export bundle (MIT).

This is the third-party / deployer-side check for `yoru verify-export`: given
only the bundle file and (optionally) an out-of-band public-key fingerprint, it
proves — with NO network and NO private key — that the bundle was signed by a
given instance and has not been altered since.

It is an independent MIT reimplementation of the same DSSE/Ed25519 wire format
the (AGPL) server signs with; the CLI must not import server code, so the ~30
lines of PAE + Ed25519 verification live here. The format matches dokan's
receipt scheme byte-for-byte, so `dokan verify` and this verb agree.
"""
from __future__ import annotations

import base64
import enum
import hashlib
import hmac
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

DSSE_PAYLOAD_TYPE = "application/vnd.in-toto+json"


class Verdict(enum.Enum):
    VERIFIED = "VERIFIED"          # signature valid (and trusted if a pin was given)
    TAMPERED = "TAMPERED"          # signature present but does not verify
    UNTRUSTED = "UNTRUSTED"        # valid signature, but key not the pinned one
    INCONCLUSIVE = "INCONCLUSIVE"  # no DSSE/Ed25519 material to check offline

    @property
    def exit_code(self) -> int:
        return {
            Verdict.VERIFIED: 0,
            Verdict.TAMPERED: 1,
            Verdict.INCONCLUSIVE: 2,
            Verdict.UNTRUSTED: 3,
        }[self]


def dsse_pae(payload_type: str, payload: bytes) -> bytes:
    """DSSE Pre-Authentication Encoding — the exact bytes that were signed.
    ``b"DSSEv1 " + len(type) + b" " + type + b" " + len(payload) + b" " + payload``
    (lengths are ASCII decimal of the UTF-8 byte length; each SP is 0x20)."""
    type_bytes = payload_type.encode("utf-8")
    return b"".join(
        [
            b"DSSEv1 ",
            str(len(type_bytes)).encode("ascii"),
            b" ",
            type_bytes,
            b" ",
            str(len(payload)).encode("ascii"),
            b" ",
            payload,
        ]
    )


def ed_verify(
    public_b64: str, payload_type: str, payload: bytes, sig_hex: str
) -> bool:
    """Verify an Ed25519 signature over the PAE with the public key only.
    False on any malformed input (never raises)."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(
            base64.standard_b64decode(public_b64)
        )
        sig = bytes.fromhex(sig_hex)
    except Exception:
        return False
    if len(sig) != 64:
        return False
    try:
        pub.verify(sig, dsse_pae(payload_type, payload))
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False


def fingerprint(public_b64: str) -> str:
    """Full SHA-256 hex over the raw public key — the value a deployer publishes
    out-of-band to pin against. (The DSSE ``keyid`` is its first 16 chars.)"""
    raw = base64.standard_b64decode(public_b64)
    return hashlib.sha256(raw).hexdigest()


def has_signature(bundle: dict) -> bool:
    try:
        dsse = bundle["dsse"]
        return bool(
            dsse["signatures"][0]["sig"]
            and dsse.get("payload")
            and bundle["ed25519"]["public_key"]
        )
    except Exception:
        return False


def verify_bundle(
    bundle: dict, expected_fingerprint: Optional[str] = None
) -> tuple[Verdict, dict]:
    """Verify the bundle's DSSE/Ed25519 signature offline.

    ``expected_fingerprint`` (out-of-band pin): when given, the bundle's
    embedded public key must match it — otherwise a VALID signature is
    UNTRUSTED (a forger can self-sign with their own key; the embedded key is
    only authenticated by an out-of-band pin). Without a pin the result is
    tamper-EVIDENT: a valid signature is VERIFIED but its authenticity rests on
    the embedded key being taken on faith.
    """
    report: dict = {"signature_valid": False, "keyid": None, "fingerprint": None,
                    "pinned": None, "public_key": None}
    if not has_signature(bundle):
        return Verdict.INCONCLUSIVE, report

    dsse = bundle["dsse"]
    public_b64 = bundle["ed25519"]["public_key"]
    payload_type = dsse.get("payloadType", DSSE_PAYLOAD_TYPE)
    sig_hex = dsse["signatures"][0]["sig"]
    report["public_key"] = public_b64
    report["keyid"] = dsse["signatures"][0].get("keyid")
    try:
        payload = base64.standard_b64decode(dsse["payload"])
        report["fingerprint"] = fingerprint(public_b64)
    except Exception:
        return Verdict.TAMPERED, report

    if not ed_verify(public_b64, payload_type, payload, sig_hex):
        return Verdict.TAMPERED, report
    report["signature_valid"] = True

    if expected_fingerprint is not None:
        pinned = hmac.compare_digest(
            report["fingerprint"], expected_fingerprint.strip().lower()
        )
        report["pinned"] = pinned
        if not pinned:
            return Verdict.UNTRUSTED, report

    return Verdict.VERIFIED, report


def signed_payload(bundle: dict) -> Optional[dict]:
    """The authoritative statement — decoded from the SIGNED base64 payload, not
    the human-readable top-level mirror (which a tamperer could edit without
    touching the signature). Returns None if unreadable."""
    import json
    try:
        return json.loads(base64.standard_b64decode(bundle["dsse"]["payload"]))
    except Exception:
        return None


def verify_chain(sessions: list) -> list[str]:
    """Check per-session hash-chain LINK consistency: each event's ``prev_hash``
    must equal the previous event's ``entry_hash``. The Ed25519 signature
    already guarantees every ``entry_hash`` is exactly what the instance signed,
    so this catches a reordered/truncated chain WITHIN the signed set without
    recomputing hashes from content. Returns a list of human-readable issues
    (empty = the chain links cleanly)."""
    issues: list[str] = []
    for s in sessions or []:
        sid = s.get("session_id", "?")
        prev_entry: Optional[str] = None
        for i, ev in enumerate(s.get("events") or []):
            entry = ev.get("entry_hash")
            prev = ev.get("prev_hash")
            if entry is None:
                # v0/unhashed event — nothing to link (older chain versions).
                prev_entry = None
                continue
            if i > 0 and prev_entry is not None and prev != prev_entry:
                issues.append(
                    f"session {sid} event #{i}: prev_hash does not link to the "
                    f"previous event's entry_hash"
                )
            prev_entry = entry
    return issues
