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

from . import __version__, config, init_cmd

# Pinned constants — NEVER a value that can redirect to a renamed/dead org. A
# stale org ref is how a sibling tool once shipped a broken updater (brand-leak
# lesson). The CLI and the server live in their own public repos.
_REPO = "TsukumoHQ/cli-yoru"  # the MIT CLI (this package)
_RELEASES_LATEST = f"https://api.github.com/repos/{_REPO}/releases/latest"
_SERVER_REPO = "TsukumoHQ/yoru"  # the AGPL server (docker image / compose stack)
_SERVER_RELEASES_LATEST = f"https://api.github.com/repos/{_SERVER_REPO}/releases/latest"
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


def _fetch_latest_tag(url: str = _RELEASES_LATEST, *, timeout: float = 5.0) -> str | None:
    resp = httpx.get(
        url,
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
    # --server[=URL] → check the running SERVER's version (notify-only), not the CLI.
    if getattr(args, "server", None) is not None:
        return _check_server(args.server)

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
        print("✓ hook + settings + skill refreshed")
    except Exception as e:  # noqa: BLE001 — side-asset failure must not fail update
        print(f"note: could not refresh the hook assets ({e}); run `yoru init --force`.")

    print(f"✓ updated to {latest_tag}.")

    # --- verify (contract step 10) — confirm the installed version is the target.
    # Additive (the success line above always prints). Runs in a FRESH interpreter
    # so it reports the freshly-installed package on disk, not the stale __version__
    # already imported into THIS process. Best-effort: a verify hiccup never turns a
    # pip install that returned 0 into a failure.
    installed = _installed_version()
    if installed is None:
        print("  run `yoru --version` to confirm.")
    elif _parse_semver(installed) == latest_ver:
        print(f"  verified: {installed}.")
    else:
        print(
            f"⚠ but `yoru --version` reports {installed}, not {latest_tag} — a shell "
            "rehash or a pipx/uv-managed install may be shadowing it.",
            file=sys.stderr,
        )
    return 0


def _installed_version() -> str | None:
    """The version a FRESH interpreter sees for the package — i.e. what's on disk
    after the pip install, not the value imported into the current process.
    Returns None if it can't be read (verify is best-effort)."""
    try:
        out = subprocess.run(
            [sys.executable, "-c", "import importlib.metadata as m; print(m.version('yoru-cli'))"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out_str = out.stdout.strip()
    return out_str or None


def _check_server(server_arg: str) -> int:
    """Notify-only server version check. Compares the running server's reported
    version (GET /api/v1/config) against the latest TsukumoHQ/yoru release and, if
    behind, prints the self-host upgrade steps. NEVER pulls an image or touches the
    self-hoster's container — the server is self-hosted; the operator upgrades on
    their own cadence. Any error is a clean no-op (fail-safe)."""
    server = (server_arg or (config.load() or {}).get("server") or "").rstrip("/")
    if not server:
        print(
            "no server configured — run `yoru init --server <url>` first, "
            "or pass `yoru update --server <url>`.",
            file=sys.stderr,
        )
        return 0

    # running server version (fail-safe)
    try:
        resp = httpx.get(f"{server}/api/v1/config", timeout=5.0, follow_redirects=True)
        resp.raise_for_status()
        server_ver = (resp.json() or {}).get("version")
    except httpx.HTTPError as e:
        print(f"could not reach the server at {server} ({e}); nothing changed.")
        return 0
    if not server_ver:
        print(
            f"the server at {server} did not report a version (likely older than "
            "0.2.0) — upgrade it once to enable this check."
        )
        return 0

    # latest server release (fail-safe)
    try:
        latest_tag = _fetch_latest_tag(_SERVER_RELEASES_LATEST)
    except httpx.HTTPError as e:
        print(f"could not reach GitHub releases ({e}); server stays on {server_ver}.")
        return 0
    if not latest_tag:
        print(f"no published release for {_SERVER_REPO}; server stays on {server_ver}.")
        return 0

    server_v, latest_v = _parse_semver(server_ver), _parse_semver(latest_tag)
    if server_v is None or latest_v is None:
        print(f"server {server_ver} vs latest {latest_tag} — could not compare versions.")
        return 0
    if latest_v <= server_v:
        print(f"server is up to date ({server_ver}).")
        return 0

    # behind → notify-only upgrade note. Yoru ships NO public registry image; the
    # self-host stack builds from source via docker-compose, so the honest upgrade
    # path is a source rebuild — not a `docker pull`.
    print(
        f"server update available: {server_ver} → {latest_tag}\n"
        "\n"
        "Yoru is self-hosted — upgrade on your own cadence by rebuilding from source\n"
        "(no image is pulled and your running container is never touched remotely):\n"
        "\n"
        "  git pull\n"
        "  docker compose -f docker-compose.prod.yml up -d --build\n"
        "\n"
        f"Schema migrations apply automatically on boot. Skim the {_SERVER_REPO} release\n"
        "notes first — breaking changes are tagged 🚨."
    )
    return 0
