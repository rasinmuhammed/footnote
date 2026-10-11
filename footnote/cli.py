from __future__ import annotations

import argparse
import json
import sys

from dotenv import find_dotenv, load_dotenv

from . import aimode
from .llm import DEFAULT_MODEL, GroqAnswerer, list_models
from .pages import PageFetcher
from .pipeline import Footnote
from .render import audit_text, markdown, text
from .search import SerpApiSearcher


def main(argv: list[str] | None = None) -> int:
    load_dotenv(find_dotenv(usecwd=True))  # variables already set in the environment win
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["audit"]:
        return audit(argv[1:])
    ap = argparse.ArgumentParser(prog="footnote", description="Answer a question from live search results, "
                                 "with every claim cited and every citation checked.")
    ap.add_argument("question", nargs="?")
    ap.add_argument("--engines", help="comma-separated SerpApi engines, e.g. google,google_news")
    ap.add_argument("--format", choices=["text", "md", "json"], default="text")
    ap.add_argument("--no-cache", action="store_true", help="always spend a fresh SerpApi search")
    ap.add_argument("--model", help=f"Groq model id (default: $FOOTNOTE_MODEL or {DEFAULT_MODEL})")
    ap.add_argument("--list-models", action="store_true", help="show models available to your Groq key")
    args = ap.parse_args(argv)
    if args.list_models:
        print("\n".join(list_models()))
        return 0
    if not args.question:
        ap.error("a question is required")

    searcher = SerpApiSearcher(cache_dir=None if args.no_cache else ".footnote_cache")
    fn = Footnote(searcher, GroqAnswerer(model=args.model))
    report = fn.ask(args.question, args.engines.split(",") if args.engines else None)

    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(markdown(report) if args.format == "md" else text(report))
    print(f"\nSerpApi searches used: {searcher.calls} (cache hits: {searcher.hits})", file=sys.stderr)
    return 0


def audit(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="footnote audit", description="Check every figure in a Google AI Mode "
                                 "answer against the pages it cites.")
    ap.add_argument("question")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--no-cache", action="store_true", help="spend a fresh SerpApi search and refetch the pages")
    args = ap.parse_args(argv)
    try:
        import trafilatura  # noqa: F401
    except ModuleNotFoundError:
        ap.error("reading the cited pages needs the audit extra: pip install 'footnote[audit]'")

    searcher = SerpApiSearcher(cache_dir=None if args.no_cache else ".footnote_cache")
    fetcher = PageFetcher(cache_dir=None if args.no_cache else ".footnote_cache/pages")
    result = aimode.audit(searcher.search(aimode.params(args.question)), fetcher.fetch)

    print(json.dumps(result.to_dict(), indent=2) if args.format == "json" else audit_text(result))
    print(f"\nSerpApi searches used: {searcher.calls} (cache hits: {searcher.hits}) | pages fetched: "
          f"{fetcher.fetched}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
