from __future__ import annotations

import argparse
import json
import sys

from .llm import ClaudeAnswerer
from .pipeline import Footnote
from .render import markdown, text
from .search import SerpApiSearcher


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="footnote", description="Answer a question from live search results, "
                                 "with every claim cited and every citation checked.")
    ap.add_argument("question")
    ap.add_argument("--engines", help="comma-separated SerpApi engines, e.g. google,google_news")
    ap.add_argument("--format", choices=["text", "md", "json"], default="text")
    ap.add_argument("--no-cache", action="store_true", help="always spend a fresh SerpApi search")
    ap.add_argument("--model", help="Claude model (default: $FOOTNOTE_MODEL)")
    args = ap.parse_args(argv)

    searcher = SerpApiSearcher(cache_dir=None if args.no_cache else ".footnote_cache")
    fn = Footnote(searcher, ClaudeAnswerer(model=args.model))
    report = fn.ask(args.question, args.engines.split(",") if args.engines else None)

    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(markdown(report) if args.format == "md" else text(report))
    print(f"\nSerpApi searches used: {searcher.calls} (cache hits: {searcher.hits})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
