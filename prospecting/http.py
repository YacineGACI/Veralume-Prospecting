"""A polite HTTP client: honest user agent, robots.txt, one request per host at a time,
and a disk cache so re-running a step does not hit the same sites again."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib import robotparser
from urllib.parse import urlsplit

import httpx

from prospecting.config import settings
from prospecting.db import now


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    content_type: str
    text: str


class Fetcher:
    def __init__(self, interval_s: float | None = None, use_cache: bool = True):
        self.interval_s = settings.host_interval_s if interval_s is None else interval_s
        self.use_cache = use_cache
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(15.0, connect=8.0),
            headers={
                "User-Agent": settings.user_agent,
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
            },
        )
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._cache_dir = settings.cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        self.client.close()

    def _wait(self, netloc: str) -> None:
        """Reserve the next slot for this host (thread-safe), then sleep until it."""
        with self._lock:
            slot = max(time.monotonic(), self._last.get(netloc, 0.0) + self.interval_s)
            self._last[netloc] = slot
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return False
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp: robotparser.RobotFileParser | None = robotparser.RobotFileParser()
            try:
                r = self.client.get(base + "/robots.txt", timeout=8.0)
                if r.status_code >= 400:
                    rp = None  # no robots.txt: everything allowed
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return rp is None or rp.can_fetch(settings.user_agent, url)

    def _cache_path(self, url: str):
        return self._cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    def get(self, url: str, max_bytes: int = 3_000_000) -> Page | None:
        """An HTML page, or None if disallowed, unreachable or not HTML."""
        path = self._cache_path(url)
        if self.use_cache and path.exists():
            c = json.loads(path.read_text())
            fetched = datetime.fromisoformat(c["fetched_at"])
            if datetime.now(UTC) - fetched < timedelta(days=settings.cache_days):
                return Page(c["url"], c["final_url"], c["status"], c["content_type"], c["text"])
        if not self.allowed(url):
            return None
        self._wait(urlsplit(url).netloc)
        try:
            r = self.client.get(url)
        except (httpx.HTTPError, UnicodeError, ValueError):
            return None
        ctype = r.headers.get("content-type", "")
        if r.status_code >= 400 or "html" not in ctype or len(r.content) > max_bytes:
            return None
        page = Page(url, str(r.url), r.status_code, ctype, r.text)
        path.write_text(json.dumps({**page.__dict__, "fetched_at": now()}, ensure_ascii=False))
        return page

    def get_bytes(self, url: str, max_bytes: int = 8_000_000) -> bytes | None:
        if not self.allowed(url):
            return None
        self._wait(urlsplit(url).netloc)
        try:
            with self.client.stream("GET", url) as r:
                if r.status_code >= 400:
                    return None
                data = bytearray()
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > max_bytes:
                        return None
                return bytes(data)
        except (httpx.HTTPError, UnicodeError, ValueError):
            return None
