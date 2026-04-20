from __future__ import annotations

from typing import Any

import httpx


class ReceiptClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def mint_token(self, user: str) -> dict[str, Any]:
        r = httpx.post(
            f"{self.base_url}/api/v1/auth/hook-token",
            json={"user": user},
            timeout=5.0,
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
