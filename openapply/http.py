"""A polite HTTP client shared by every network call in openapply.

Being polite means three things here:
1. An honest, identifiable User-Agent (no browser impersonation). Set
   OPENAPPLY_CONTACT in .env to add your own contact URL or email to it.
2. A minimum delay between requests to the same host, so we never hammer an API.
3. Retries with backoff on 429/5xx, honoring the server's Retry-After header.
   If a site keeps refusing us, we give up rather than try to get around it.
"""

from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import httpx

from . import __version__

DEFAULT_CONTACT = "https://github.com/kadenhuang102007/openapply"


def user_agent() -> str:
    contact = os.environ.get("OPENAPPLY_CONTACT") or DEFAULT_CONTACT
    return f"openapply/{__version__} (personal internship search tool; +{contact})"


class PoliteClient:
    """httpx wrapper with per-host throttling and gentle retries."""

    def __init__(self, min_delay: float = 1.0, timeout: float = 30.0, max_retries: int = 3):
        self.min_delay = min_delay
        self.max_retries = max_retries
        self._last_hit: dict[str, float] = {}
        self._client = httpx.Client(
            headers={"User-Agent": user_agent(), "Accept": "application/json, text/plain, */*"},
            timeout=timeout,
            follow_redirects=True,
        )

    def _wait_for_host(self, url: str) -> None:
        host = urlparse(url).netloc
        elapsed = time.monotonic() - self._last_hit.get(host, 0.0)
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self._last_hit[host] = time.monotonic()

    def get(self, url: str, **kwargs) -> httpx.Response:
        backoff = 2.0
        for attempt in range(self.max_retries + 1):
            self._wait_for_host(url)
            resp = self._client.get(url, **kwargs)
            if resp.status_code not in (429, 500, 502, 503, 504) or attempt == self.max_retries:
                return resp
            retry_after = resp.headers.get("Retry-After", "")
            time.sleep(float(retry_after) if retry_after.isdigit() else backoff)
            backoff *= 2
        return resp  # pragma: no cover

    def get_json(self, url: str, **kwargs):
        resp = self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def get_text(self, url: str, **kwargs) -> str:
        resp = self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.text

    def close(self) -> None:
        self._client.close()
