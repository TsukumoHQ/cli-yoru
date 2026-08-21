from __future__ import annotations

from typing import Any

import httpx


class ReceiptClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def start_device_code(
        self, label: str | None = None, hostname: str | None = None
    ) -> dict[str, Any]:
        """Begin the device-pairing handshake — no auth needed."""
        body: dict[str, Any] = {}
        if label:
            body["label"] = label
        if hostname:
            body["hostname"] = hostname
        r = httpx.post(
            f"{self.base_url}/api/v1/auth/device-code",
            json=body,
            timeout=5.0,
        )
        r.raise_for_status()
        return r.json()

    def poll_device_code(self, device_code: str) -> dict[str, Any]:
        """Poll for approval — returns {status, token?}."""
        r = httpx.post(
            f"{self.base_url}/api/v1/auth/device-code/poll",
            json={"device_code": device_code},
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()

    def post_events(self, events: list[dict[str, Any]]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        return httpx.post(
            f"{self.base_url}/api/v1/sessions/events",
            json={"events": events},
            headers=headers,
            timeout=5.0,
        )

    def share_session(self, session_id: str) -> dict[str, Any]:
        """Flip a session public (#79). Requires bearer token — 401 otherwise.

        Backend is idempotent: re-POST on an already-public session returns
        the same `public_url`. 404 on cross-user (token's user doesn't own
        this session) — callers should treat that as "not your session".
        """
        if not self.token:
            raise RuntimeError("share_session requires authentication (run `yoru init`)")
        r = httpx.post(
            f"{self.base_url}/api/v1/sessions/{session_id}/share",
            json={"source": "cli"},
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()

    def revoke_share(self, session_id: str) -> dict[str, Any]:
        """Flip a session back to private (#79). Idempotent."""
        if not self.token:
            raise RuntimeError("revoke_share requires authentication (run `yoru init`)")
        r = httpx.post(
            f"{self.base_url}/api/v1/sessions/{session_id}/share/revoke",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()

    def logout(self) -> bool:
        """Revoke this client's own token server-side (POST /auth/logout).

        Self-revoke via bearer — no need to know the token's server-side row
        id, which the CLI never learns (the poll response only ever returns
        the raw token). Returns True if the token was revoked, False if it
        was already invalid/revoked (a 401 here just means "nothing to
        revoke", not a failure worth surfacing). Any other error status or
        network failure raises, so the caller can warn loudly instead of
        silently treating a failed revoke as done.
        """
        if not self.token:
            raise RuntimeError("logout requires authentication")
        r = httpx.post(
            f"{self.base_url}/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=5.0,
        )
        if r.status_code == 401:
            return False
        r.raise_for_status()
        return True

    def get_share_consent(self) -> dict[str, Any]:
        """Returns {consented: bool, at: str|None} for the authenticated user."""
        if not self.token:
            raise RuntimeError("get_share_consent requires authentication")
        r = httpx.get(
            f"{self.base_url}/api/v1/account/share-consent",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=5.0,
        )
        r.raise_for_status()
        return r.json()

    def post_share_consent(self) -> dict[str, Any]:
        """Stamp share consent for the authenticated user (idempotent)."""
        if not self.token:
            raise RuntimeError("post_share_consent requires authentication")
        r = httpx.post(
            f"{self.base_url}/api/v1/account/share-consent",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=5.0,
        )
        r.raise_for_status()
        return r.json()
