"""Thin wrapper over the official serpapi client, with an on-disk cache so repeated runs cost no credits."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Protocol


class Searcher(Protocol):
    def search(self, params: dict) -> dict: ...


class SerpApiSearcher:
    def __init__(self, api_key: str | None = None, cache_dir: str | os.PathLike | None = ".footnote_cache",
                 timeout: float = 120.0, client=None):  # Google searches have taken 80s
        if client is None:
            import serpapi

            key = api_key or os.environ.get("SERPAPI_API_KEY")
            if not key:
                raise RuntimeError("Set SERPAPI_API_KEY (get one at https://serpapi.com/manage-api-key).")
            client = serpapi.Client(api_key=key, timeout=timeout)
        self.client = client
        self.cache = Path(cache_dir) if cache_dir else None
        self.calls = 0  # live SerpApi requests made, i.e. credits spent
        self.hits = 0

    def _path(self, params: dict) -> Path | None:
        if not self.cache:
            return None
        digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:24]
        return self.cache / f"{params.get('engine', 'google')}-{digest}.json"

    def search(self, params: dict) -> dict:
        path = self._path(params)
        if path and path.exists():
            self.hits += 1
            return json.loads(path.read_text())
        try:
            results = self.client.search(dict(params))
        except Exception as e:  # serpapi errors embed the request URL, api_key included; keep it out of tracebacks
            detail = getattr(e, "error", None) or re.sub(r"api_key=[^&\s]+", "api_key=***", str(e))
            raise RuntimeError(f"SerpApi request failed for {params.get('engine')}: {detail}") from None
        data = results.as_dict() if hasattr(results, "as_dict") else dict(results)
        if "error" in data:
            raise RuntimeError(f"SerpApi error for {params.get('engine')}: {data['error']}")
        self.calls += 1
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
        return data


def params_for(engine: str, question: str, hl: str = "en", gl: str = "us") -> dict:
    p = {"engine": engine, "q": question, "hl": hl}
    if engine in ("google", "google_news"):
        p["gl"] = gl
    if engine == "google":
        p["num"] = 10
    return p
