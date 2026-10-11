"""Fetch a cited page once, keep its main text and published date on disk.

Failures are cached too, so a rerun audits exactly the same text. Needs the `audit` extra (trafilatura).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

USER_AGENT = "footnote-audit/0.1 (+https://github.com/rasinmuhammed/footnote)"
MAX_BYTES = 5_000_000


@dataclass
class Page:
    url: str
    text: str = ""
    date: str | None = None  # published date as YYYY-MM-DD, when the page states one
    error: str = ""
    fetched_at: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class PageFetcher:
    def __init__(self, cache_dir: str | os.PathLike | None = ".footnote_cache/pages", timeout: float = 20.0,
                 client: httpx.Client | None = None):
        self.cache = Path(cache_dir) if cache_dir else None
        self.client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout,
                                             follow_redirects=True)
        self.fetched = 0
        self._lock = threading.Lock()

    def _path(self, url: str) -> Path | None:
        return self.cache / f"{hashlib.sha256(url.encode()).hexdigest()[:24]}.json" if self.cache else None

    def fetch(self, url: str) -> Page:
        path = self._path(url)
        if path and path.exists():
            return Page(**json.loads(path.read_text()))
        page = self._get(url)
        with self._lock:
            self.fetched += 1
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(page)))
        return page

    def _get(self, url: str) -> Page:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            r = self.client.get(url)
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            return Page(url, error=type(e).__name__, fetched_at=now)
        kind = r.headers.get("content-type", "").split(";")[0].strip()
        if r.status_code >= 400:
            error = f"HTTP {r.status_code}"
        elif kind and "html" not in kind:
            error = f"not HTML: {kind}"
        elif len(r.content) > MAX_BYTES:
            error = "too large"
        else:
            import trafilatura

            try:  # bytes, so trafilatura can detect the encoding the headers leave out
                text = trafilatura.extract(r.content, url=str(r.url), favor_recall=True, include_tables=True) or ""
                meta = trafilatura.extract_metadata(r.content, default_url=str(r.url))
            except Exception as e:  # arbitrary web pages; one bad page shouldn't stop an audit
                return Page(url, error=f"extraction failed: {type(e).__name__}", fetched_at=now)
            return Page(url, text, meta.date if meta else None, "" if text else "no main text", now)
        return Page(url, error=error, fetched_at=now)
