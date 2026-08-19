"""`yoru verify-export <bundle.json>` — offline proof that a signed audit-export
bundle is authentic and unaltered.

No network, no private key: it checks the Ed25519/DSSE signature against the
public key embedded in the bundle, optionally PINNED to a fingerprint the
deployer published out-of-band, then re-walks the per-session hash chain and
confirms the human-readable top-level statement matches the SIGNED payload.

Exit codes (mirror dokan verify): 0 VERIFIED · 1 TAMPERED · 2 INCONCLUSIVE ·
3 UNTRUSTED (valid signature, key not the pinned one).
"""
from __future__ import annotations

import argparse
import json
import sys

from . import dsse_verify
from .dsse_verify import Verdict


def _load_bundle(path: str) -> dict:
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def run(args: argparse.Namespace) -> int:
    try:
        bundle = _load_bundle(args.bundle)
    except FileNotFoundError:
        print(f"verify-export: no such file: {args.bundle}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, OSError) as e:
        print(f"verify-export: cannot read bundle: {e}", file=sys.stderr)
        return 2

    verdict, report = dsse_verify.verify_bundle(bundle, args.pubkey_fingerprint)

    # Signature line.
    if verdict is Verdict.INCONCLUSIVE:
        print("INCONCLUSIVE — no Ed25519/DSSE signature to verify offline.")
        return verdict.exit_code
    if verdict is Verdict.TAMPERED:
        print("TAMPERED — the signature does not verify; the bundle was altered.")
        return verdict.exit_code

    fp = report.get("fingerprint", "")
    print(f"signature: valid (Ed25519/DSSE)")
    print(f"key fingerprint: {fp}")
    if args.pubkey_fingerprint is not None:
        print(f"pinned fingerprint: {'MATCH' if report.get('pinned') else 'MISMATCH'}")
    if verdict is Verdict.UNTRUSTED:
        print(
            "UNTRUSTED — signature is valid but the signing key does not match "
            "the pinned fingerprint (could be a forger's own key)."
        )
        return verdict.exit_code

    # Signature is trusted; now check the SIGNED statement's internal integrity.
    signed = dsse_verify.signed_payload(bundle)
    sessions = ((signed or {}).get("predicate") or {}).get("sessions") or []
    chain_issues = dsse_verify.verify_chain(sessions)

    # The human-readable top level must equal the signed payload — otherwise a
    # reader of the top-level fields is trusting un-signed data.
    mirror_ok = True
    if signed is not None and "predicate" in bundle:
        mirror_ok = bundle.get("predicate") == signed.get("predicate")

    if not args.pubkey_fingerprint:
        print(
            "note: no --pubkey-fingerprint given — tamper-evident only; the "
            "embedded key is trusted on faith. Pin the deployer's fingerprint "
            "for authenticity."
        )
    print(f"sessions: {len(sessions)}")

    problems = list(chain_issues)
    if not mirror_ok:
        problems.append(
            "top-level statement differs from the SIGNED payload — the readable "
            "copy was edited; trust only the signed payload."
        )

    if problems:
        for p in problems:
            print(f"  ! {p}")
        print("TAMPERED — signature valid but the bundle's contents are inconsistent.")
        return Verdict.TAMPERED.exit_code

    print("chain: links consistent")
    print("VERIFIED — signature valid and contents consistent.")
    return Verdict.VERIFIED.exit_code
