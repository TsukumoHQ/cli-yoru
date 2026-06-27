"""`yoru update` — self-update the CLI from its latest GitHub release.

Implements the Fleet Auto-Updater contract shared by every Tsukumo tool
(SSOT: the fleet-updater pattern-doc; reference impl = WRAI.TH `update.go`):

  resolve current → resolve latest (pinned org) → semver compare →
  dev-build guard → acquire (pip self-replace) → refresh side-assets
  (hook + settings) → verify → fail-safe (any error = clean no-op).

UX: `yoru update` (auto), `yoru update --force` (bypass guards),
`yoru update --check` (report only, no install).
"""
from __future__ import annotations

import argparse
import subprocess
import sys

import httpx

from . import __version__, init_cmd

# Pinned constant — NEVER a value that can redirect to a renamed/dead org. A
# stale org ref is how a sibling tool once shipped a broken updater (brand-leak
# lesson). The CLI lives in its own public repo.
_REPO = "TsukumoHQ/cli-yoru"
_RELEASES_LATEST = f"https://api.github.com/repos/{_REPO}/releases/latest"
_PKG = "yoru-cli"

Version = tuple[int, int, int]


def _parse_semver(tag: str) -> Version | None:
    """'v0.1.2' / '0.1.2' / '0.1.2+dev' / '0.1.2-rc1' → (0, 1, 2). None if
    unparseable (treated as 'cannot compare' by the caller)."""
    core = tag.strip().lstrip("vV").split("+", 1)[0].split("-", 1)[0]
    parts = core.split(".")
    try:
        nums = [int(p) for p in parts[:3]]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def _is_dev_build(v: str) -> bool:
    """A source/dev build (not a clean released version) — refuse to clobber it
    unless --force. The package metadata fallback is '0.0.0+dev'."""
    return "dev" in v or "+" in v or v.startswith("0.0.0")


def _fetch_latest_tag(*, timeout: float = 5.0) -> str | None:
    resp = httpx.get(
        _RELEASES_LATEST,
        timeout=timeout,
        follow_redirects=True,
        headers={"Accept": "application/vnd.github+json"},
    )
    resp.raise_for_status()
    return (resp.json() or {}).get("tag_name")


def _pip_install(version: str) -> int:
    """Self-replace via pip, pinned to the release tag. Returns the process
    exit code. (CLIs installed via pipx/uv-tool should use that tool's own
    upgrade — documented in --help; pip covers the common `pip install` case.)"""
    return subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", f"{_PKG}=={version}"],
        check=False,
    ).returncode


def run(args: argparse.Namespace) -> int:
    current = __version__
    force = bool(getattr(args, "force", False))
    check_only = bool(getattr(args, "check", False))

    # --- resolve latest (fail-safe: any network error → clean no-op) ---------
    try:
        latest_tag = _fetch_latest_tag()
    except httpx.HTTPError as e:
        print(f"could not reach GitHub releases ({e}); staying on {current}.")
        return 0
    if not latest_tag:
        print(f"no published release found for {_REPO}; staying on {current}.")
        return 0

    latest_ver = _parse_semver(latest_tag)
    current_ver = _parse_semver(current)
    pin = latest_tag.lstrip("vV")  # pip wants '0.1.3', not 'v0.1.3'

    # --- compare -------------------------------------------------------------
    if latest_ver is None:
        print(f"could not parse the latest tag {latest_tag!r}; staying on {current}.")
        return 0

    if check_only:
        if current_ver is None or latest_ver > current_ver:
            print(f"update available: {current} → {latest_tag}")
        else:
            print(f"up to date ({current}).")
        return 0

    if current_ver is not None and not force:
        if current_ver == latest_ver:
            print(f"already up to date ({current}).")
            return 0
        if current_ver > latest_ver:
            print(
                f"current {current} is ahead of the latest release {latest_tag} — "
                "not downgrading (use --force to override)."
            )
            return 0

    # --- dev-build guard -----------------------------------------------------
    if _is_dev_build(current) and not force:
        print(
            f"refusing to auto-update a dev/source build ({current}) — it would "
            "clobber local work. Use --force to override."
        )
        return 0

    # --- acquire: pip self-replace -------------------------------------------
    print(f"updating {current} → {latest_tag} …")
    rc = _pip_install(pin)
    if rc != 0:
        print(
            f"pip could not install {_PKG}=={pin} (exit {rc}); your existing "
            f"install ({current}) is untouched.",
            file=sys.stderr,
        )
        return rc

    # --- refresh side-assets (best-effort; never fails the binary update) ----
    try:
        init_cmd.refresh_hook_assets()
        print("✓ hook + settings refreshed")
    except Exception as e:  # noqa: BLE001 — side-asset failure must not fail update
        print(f"note: could not refresh the hook assets ({e}); run `yoru init --force`.")

    print(f"✓ updated to {latest_tag}. Run `yoru --version` to confirm.")
    return 0
