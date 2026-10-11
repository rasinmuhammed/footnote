"""Turn raw SerpApi responses into one flat, numbered list of sources."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Source:
    id: str
    engine: str
    title: str
    url: str
    text: str
    date: str | None = None
    position: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _join(*parts: object) -> str:
    return " ".join(str(p).strip() for p in parts if p).strip()


def _google(results: dict) -> list[dict]:
    out: list[dict] = []
    box = results.get("answer_box") or {}
    if box:
        text = _join(box.get("answer"), box.get("snippet"))
        if text:
            out.append({"title": box.get("title") or "Answer box", "url": box.get("link") or "",
                        "text": text, "date": box.get("date")})
    kg = results.get("knowledge_graph") or {}
    if kg.get("description"):
        out.append({"title": kg.get("title") or "Knowledge graph",
                    "url": (kg.get("source") or {}).get("link") or kg.get("website") or "",
                    "text": _join(kg.get("type"), kg.get("description")), "date": None})
    for r in results.get("organic_results") or []:
        out.append({"title": r.get("title", ""), "url": r.get("link", ""), "text": _join(r.get("snippet")),
                    "date": r.get("date"), "position": r.get("position")})
    return out


def _news(results: dict) -> list[dict]:
    out: list[dict] = []
    for r in results.get("news_results") or []:
        for s in r.get("stories") or [r]:  # clustered results carry their articles under "stories"
            src = s.get("source") or {}
            name = src.get("name") if isinstance(src, dict) else src
            out.append({"title": s.get("title", ""), "url": s.get("link", ""),
                        "text": _join(s.get("title"), s.get("snippet"), f"({name})" if name else ""),
                        "date": s.get("date"), "position": s.get("position")})
    return out


def _scholar(results: dict) -> list[dict]:
    out: list[dict] = []
    for r in results.get("organic_results") or []:
        pub = (r.get("publication_info") or {}).get("summary")
        cited = ((r.get("inline_links") or {}).get("cited_by") or {}).get("total")
        out.append({"title": r.get("title", ""), "url": r.get("link", ""),
                    "text": _join(r.get("title"), r.get("snippet"), pub, f"Cited by {cited}." if cited else ""),
                    "date": None, "position": r.get("position")})
    return out


PARSERS = {"google": _google, "google_news": _news, "google_scholar": _scholar}


def normalise(responses: dict[str, dict], per_engine: int = 6) -> list[Source]:
    """Flatten responses keyed by engine into numbered sources, dropping duplicates and empty snippets."""
    seen: set[str] = set()
    sources: list[Source] = []
    for engine, results in responses.items():
        parse = PARSERS.get(engine)
        if parse is None:
            raise ValueError(f"No parser for engine {engine!r}")
        kept = 0
        for item in parse(results):
            if kept >= per_engine:
                break
            key = item["url"] or item["text"]
            if not item["text"] or key in seen:
                continue
            seen.add(key)
            kept += 1
            sources.append(Source(id=f"S{len(sources) + 1}", engine=engine, title=item["title"], url=item["url"],
                                  text=item["text"], date=item.get("date"), position=item.get("position")))
    return sources
