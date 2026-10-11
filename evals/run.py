"""Run Footnote over evals/questions.jsonl and compare it with the same model answering without search.

Metrics are about verifiability, not truth: they measure how many claims are backed by a live source.
Calibrate the checker before trusting it: hand-label ~50 verdicts from results/*.json and report agreement.

Usage: python evals/run.py [--limit N]
       python evals/run.py --rescore evals/results/<file>.json   # re-check saved claims; no searches, no model calls
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path

from dotenv import load_dotenv

from footnote.llm import GroqAnswerer
from footnote.pipeline import Footnote
from footnote.search import SerpApiSearcher
from footnote.sources import Source
from footnote.verify import Verdict, check, check_against_pool, numbers

HERE = Path(__file__).parent
# Search and model time are what SerpApi and Groq report, so they stay real when searches come from the cache and
# leave out free-tier rate-limit waits. Wall clock includes both effects.
STAGES = (("SerpApi search, as SerpApi reports it", "serpapi_s"), ("Model, as Groq reports it", "model_s"),
          ("Wall clock in this run", "total_s"))


def summarise(verdicts: list[Verdict]) -> dict:
    """Raw counts. Shares are only for display: a few dozen claims can't support decimals."""
    numeric = [v for v in verdicts if v.status != "abstain" and numbers(v.claim)]

    def count(vs: list[Verdict], *statuses: str) -> int:
        return sum(v.status in statuses for v in vs)

    return {"claims": len(verdicts), "supported": count(verdicts, "supported"),
            "weak_or_unsupported": count(verdicts, "weak", "unsupported"), "abstain": count(verdicts, "abstain"),
            "numeric_claims": len(numeric), "numbers_not_in_sources": count(numeric, "number_not_found"),
            "bad_citations": count(verdicts, "bad_citation")}


def latency(rows: list[dict]) -> dict:
    """Median and nearest-rank p90 per stage of the grounded answer, so one slow search isn't hidden in a total."""
    out = {}
    for _, key in STAGES:
        xs = sorted(x for r in rows if (x := r["grounded"]["timings"].get(key)) is not None)
        if xs:  # results from before a stage was recorded simply omit it
            out[key] = {"median": round(statistics.median(xs), 1), "p90": round(xs[math.ceil(0.9 * len(xs)) - 1], 1)}
    return out


def table(summary: dict) -> str:
    g, c = summary["grounded"], summary["closed_book"]

    def of(d: dict, key: str, total: str = "claims") -> str:
        return f"{d[key]} of {d[total]} ({d[key] / d[total]:.0%})" if d[total] else "0 of 0"

    rows = [("Claims", g["claims"], c["claims"]),
            ("Backed by a source", of(g, "supported"), of(c, "supported")),
            ("Weak or unsupported", of(g, "weak_or_unsupported"), of(c, "weak_or_unsupported")),
            ("Abstained (refusal or placeholder)", of(g, "abstain"), of(c, "abstain")),
            ("Numeric claims with a number not in their sources", of(g, "numbers_not_in_sources", "numeric_claims"),
             of(c, "numbers_not_in_sources", "numeric_claims")),
            ("Bad citations", of(g, "bad_citations"), "n/a")]
    lat = summary["latency_s"]
    return "\n".join(["| | Grounded (Footnote) | Closed-book |", "|---|---|---|",
                      *(f"| {label} | {grounded} | {closed} |" for label, grounded, closed in rows),
                      "", "| Grounded latency per question | Median | p90 |", "|---|---|---|",
                      *(f"| {stage} | {lat[key]['median']}s | {lat[key]['p90']}s |" for stage, key in STAGES
                        if key in lat)])


def rescore(path: Path) -> dict:
    """Re-check the claims saved in a results file with the current checker, updating it in place."""
    data = json.loads(path.read_text())
    grounded, closed = [], []
    for row in data["rows"]:
        sources = [Source(**s) for s in row["grounded"]["sources"]]
        g = [check(v["claim"], v["source_ids"], sources) for v in row["grounded"]["claims"]]
        b = [check_against_pool(v["claim"], sources) for v in row["closed_book"]]
        row["grounded"]["claims"], row["closed_book"] = [v.to_dict() for v in g], [v.to_dict() for v in b]
        row["grounded"]["supported_share"] = round(sum(v.ok for v in g) / len(g), 3) if g else 0.0
        grounded += g
        closed += b
    data["summary"].update(grounded=summarise(grounded), closed_book=summarise(closed), latency_s=latency(data["rows"]))
    path.write_text(json.dumps(data, indent=2))
    return data["summary"]


def main() -> None:
    load_dotenv(HERE.parent / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--rescore", type=Path, help="re-check a saved results file with the current checker")
    ap.add_argument("--resume", type=Path, help="continue an interrupted run, skipping questions it finished")
    args = ap.parse_args()
    if args.rescore:
        summary = rescore(args.rescore)
        print(f"{table(summary)}\n\n{summary['model']}, {summary['questions']} questions, "
              f"SerpApi searches {summary['serpapi_searches']}. Re-scored {args.rescore}")
        return

    questions = [json.loads(l) for l in (HERE / "questions.jsonl").read_text().splitlines() if l.strip()]
    questions = questions[: args.limit] if args.limit else questions
    searcher, answerer = SerpApiSearcher(), GroqAnswerer()
    fn = Footnote(searcher, answerer)

    out = args.resume or HERE / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    prev = json.loads(out.read_text()) if args.resume else {"summary": {}, "rows": []}
    if prev["summary"].get("model", answerer.model) != answerer.model:
        raise SystemExit(f"{out} was run with {prev['summary']['model']}, not {answerer.model}")
    rows, done = prev["rows"], {r["id"] for r in prev["rows"]}
    before = prev["summary"].get("tokens", {"input_tokens": 0, "output_tokens": 0})

    def save() -> dict:  # after every question, so an interrupted run loses at most one
        summary = {
            "questions": len(rows),
            "model": answerer.model,
            "grounded": summarise([Verdict(**v) for r in rows for v in r["grounded"]["claims"]]),
            "closed_book": summarise([Verdict(**v) for r in rows for v in r["closed_book"]]),
            "latency_s": latency(rows),
            "serpapi_searches": prev["summary"].get("serpapi_searches", 0) + searcher.calls,
            "serpapi_cache_hits": prev["summary"].get("serpapi_cache_hits", 0) + searcher.hits,
            "tokens": {k: before[k] + answerer.usage[k] for k in before},
        }
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
        return summary

    for item in questions:
        if item["id"] in done:
            continue
        report = fn.ask(item["q"])
        baseline = fn.closed_book(item["q"], report.sources)
        rows.append({"id": item["id"], "question": item["q"], "grounded": report.to_dict(),
                     "closed_book": [v.to_dict() for v in baseline]})
        save()
        g, b = summarise(report.verdicts), summarise(baseline)
        print(f"{item['id']}: grounded {g['supported']}/{g['claims']} supported, "
              f"closed-book {b['supported']}/{b['claims']}", flush=True)

    summary = save()
    print(f"\n{table(summary)}\n\n{answerer.model}, {summary['questions']} questions, SerpApi searches "
          f"{summary['serpapi_searches']} (cache hits {summary['serpapi_cache_hits']}). Saved {out}")


if __name__ == "__main__":
    main()
