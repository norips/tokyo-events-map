"""Small polite HTTP client with an on-disk cache, shared by all sources."""

from __future__ import annotations

import hashlib
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "http"
USER_AGENT = "TokyoEventsMap/0.1 (personal event map; polite crawler)"


class NotFound(Exception):
    pass


class Blocked(Exception):
    """The site answered with a bot challenge / rate limit. Stop and retry later."""


class Fetcher:
    def __init__(self, min_interval: float = 1.5, cache_ttl: float = 0, retries: int = 3):
        self.min_interval = min_interval
        self.cache_ttl = cache_ttl
        self.retries = retries
        self._lock = threading.Lock()
        self._last = 0.0

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def get(self, url: str, cache_ttl: float | None = None) -> str:
        ttl = self.cache_ttl if cache_ttl is None else cache_ttl
        cache_file = CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".html")
        if ttl and cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
            return cache_file.read_text(encoding="utf-8")

        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        for attempt in range(self.retries):
            self._throttle()
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise NotFound(url) from e
                if e.code in (403, 405):  # AWS WAF challenge page: not something to retry
                    raise Blocked(f"{e.code} for {url}") from e
                if e.code == 429 and attempt == self.retries - 1:
                    raise Blocked(f"429 for {url}") from e
                if attempt == self.retries - 1 or e.code < 500 and e.code != 429:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == self.retries - 1:
                    raise
            time.sleep(5 * 2 ** attempt)

        if "Human Verification" in body[:2000]:
            raise Blocked(f"bot challenge for {url}")
        if ttl:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(body, encoding="utf-8")
        return body
