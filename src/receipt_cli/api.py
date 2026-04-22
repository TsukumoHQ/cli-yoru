from __future__ import annotations

from typing import Any

import httpx


class ReceiptClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def start_device_code(self, label: str | None = None) -> dict[str, Any]:
        """Begin the device-pairing handshake — no auth needed."""
        r = httpx.post(
            f"{self.base_url}/api/v1/auth/device-code",
            json={"label": label} if label else {},
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
