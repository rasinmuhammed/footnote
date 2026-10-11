"""Run Footnote over evals/questions.jsonl and compare it with the same model answering without search.

Metrics are about verifiability, not truth: they measure how many claims are backed by a live source.
Calibrate the checker before trusting it: hand-label ~50 verdicts from results/*.json and report agreement.

Usage: python evals/run.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from dotenv import load_dotenv

from footnote.llm import GroqAnswerer
from footnote.pipeline import Footnote
from footnote.search import SerpApiSearcher
from footnote.verify import numbers

HERE = Path(__file__).parent


def summarise(verdicts: list) -> dict:
    n = len(verdicts) or 1
    with_numbers = [v for v in verdicts if numbers(v.claim)]
    return {
        "claims": len(verdicts),
        "supported": round(sum(v.status == "supported" for v in verdicts) / n, 3),
        "weak_or_unsupported": round(sum(v.status in ("weak", "unsupported") for v in verdicts) / n, 3),
        "numeric_claims": len(with_numbers),
        "numbers_not_in_sources": round(sum(v.status == "number_not_found" for v in with_numbers)
                                        / (len(with_numbers) or 1), 3),
        "bad_citations": sum(v.status == "bad_citation" for v in verdicts),
    }


def main() -> None:
    load_dotenv(HERE.parent / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    questions = [json.loads(l) for l in (HERE / "questions.jsonl").read_text().splitlines() if l.strip()]
    questions = questions[: args.limit] if args.limit else questions
    searcher, answerer = SerpApiSearcher(), GroqAnswerer()
    fn = Footnote(searcher, answerer)

    rows, grounded_all, closed_all, latencies = [], [], [], []
    for item in questions:
        report = fn.ask(item["q"])
        baseline = fn.closed_book(item["q"], report.sources)
        grounded_all += report.verdicts
        closed_all += baseline
        latencies.append(report.timings["total_s"])
        rows.append({"id": item["id"], "question": item["q"], "grounded": report.to_dict(),
                     "closed_book": [v.to_dict() for v in baseline]})
        print(f"{item['id']}: grounded {summarise(report.verdicts)['supported']:.0%} supported, "
              f"closed-book {summarise(baseline)['supported']:.0%}")

    summary = {
        "questions": len(questions),
        "model": answerer.model,
        "grounded": summarise(grounded_all),
        "closed_book": summarise(closed_all),
        "latency_s": {"median": statistics.median(latencies),
                      "p90": sorted(latencies)[max(0, int(len(latencies) * 0.9) - 1)]},
        "serpapi_searches": searcher.calls,
        "tokens": answerer.usage,
    }
    out = HERE / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))

    g, c = summary["grounded"], summary["closed_book"]
    print(f"\n| | Grounded (Footnote) | Closed-book |\n|---|---|---|")
    for k in ("claims", "supported", "weak_or_unsupported", "numeric_claims", "numbers_not_in_sources", "bad_citations"):
        print(f"| {k} | {g[k]} | {c[k]} |")
    print(f"\nMedian latency {summary['latency_s']['median']}s, SerpApi searches {searcher.calls}. Saved {out}")


if __name__ == "__main__":
    main()
