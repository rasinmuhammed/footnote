"""Audit Google AI Mode's answers to the eval questions: is every figure in the pages each statement cites?

Covers the 20 questions Footnote was evaluated on and 60 more (aimode_questions.jsonl). Each answer's cited pages
are read straight after its search, so live prices have as little time as possible to move; questions about live
figures are still reported apart. Every answer is audited twice, from snippets alone and with the pages read.
Footnote's own checker also scores every statement against its snippets, unchanged, so AI Mode can be compared with
the open models Footnote was evaluated with on the same ruler.

Every flag (a cited figure not found in its cited pages) was then checked by hand with evals/label_flags.py.

Usage: python evals/aimode_audit.py [--max-searches 77]   # writes evals/aimode_results.json
       python evals/aimode_audit.py --report               # tables, and aimode_summary.json; no searches, no fetching
       python evals/aimode_audit.py --causes               # why the checker missed figures the hand check found
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
import math
import os
import re
import statistics
from collections import Counter
from pathlib import Path

import httpx
from dotenv import load_dotenv

from footnote import aimode
from footnote.aimode import FOUND
from footnote.pages import USER_AGENT, Page, PageFetcher
from footnote.search import SerpApiSearcher
from footnote.sources import Source
from footnote.verify import Verdict, check
from run import summarise

HERE = Path(__file__).parent
OUT, SUMMARY = HERE / "aimode_results.json", HERE / "aimode_summary.json"
LABELS, CAUSES = HERE / "labels_flags.json", HERE / "flag_causes.json"
LIVE = {"q06", "q13", "a42", "a51", "a52", "a55", "a56"}  # market caps, prices and totals that move by the hour


def questions() -> list[dict]:
    def read(name: str) -> list[dict]:
        return [json.loads(line) for line in (HERE / name).read_text().splitlines() if line.strip()]

    qs = [{**q, "topic": "footnote_eval"} for q in read("questions.jsonl")] + read("aimode_questions.jsonl")
    return [{**q, "live": q["id"] in LIVE} for q in qs]


def searches_left() -> int | None:
    """From SerpApi's Account API, which costs no searches."""
    try:
        r = httpx.get("https://serpapi.com/account.json", params={"api_key": os.environ["SERPAPI_API_KEY"]},
                      timeout=30)
        return r.json()["plan_searches_left"]
    except Exception as e:  # the message would hold the request URL, key included
        print(f"Account check failed: {type(e).__name__}")
        return None


MARKER = re.compile(r"\[(\d+)\]")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _key(text: str) -> str:
    return " ".join(re.sub(r"[^\w%.]+", " ", text.lower()).split())


def markdown_citations(response: dict) -> dict:
    """Do reconstructed_markdown's [n] markers agree with the reference_indexes on each text block?"""
    body = (response.get("reconstructed_markdown") or "").split("### References")[0]
    lines, table_rows_cited = {}, 0
    for raw in body.splitlines():
        line = LINK.sub(r"\1", re.sub(r"\\(.)", r"\1", raw)).strip()
        refs = sorted({int(n) for n in MARKER.findall(line)})
        if line.startswith("|"):
            table_rows_cited += bool(refs)
            continue
        lines[_key(MARKER.sub("", line).removeprefix("- ").removeprefix("* "))] = refs
    out = {"agree": 0, "differ": [], "unmatched": 0, "table_rows_cited": table_rows_cited}
    for s in aimode.statements(response)[0]:
        if s.kind == "table":
            continue
        refs = lines.get(_key(s.text))
        if refs is None:
            out["unmatched"] += 1
        elif refs == sorted(s.refs):
            out["agree"] += 1
        else:
            out["differ"].append({"text": s.text, "json": s.refs, "markdown": refs})
    return out


def footnote_status(statement: aimode.Statement, refs: list[aimode.Reference]) -> str:
    """Footnote's verdict on a statement, from what its checker reads in a search result: title, snippet and date.
    A statement with no citation is a bad citation, as it would be from Footnote's model."""
    sources = [Source(f"S{r.index}", "google_ai_mode", r.title, r.url, " ".join(filter(None, (r.snippet, r.passage))),
                      r.snippet_date) for r in refs]
    return check(statement.text, [f"S{i}" for i in statement.refs], sources).status


def record(q: dict, response: dict, fetch) -> dict:
    full, snippets = aimode.audit(response, fetch), aimode.audit(response)
    data = full.to_dict()
    for s, t in zip(data["statements"], snippets.statements):
        s["footnote_status"] = footnote_status(t, snippets.references)
        for m, n in zip(s["mentions"], t.mentions):
            m["snippet_status"] = n.status
    meta = response.get("search_metadata") or {}
    return {**q, "searched_at": meta.get("created_at"), "serpapi_s": meta.get("total_time_taken"),
            "audit": data, "markdown": markdown_citations(response)}


def save(rows: list[dict]) -> None:
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps({"rows": rows}, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(OUT)


def run(max_searches: int) -> None:
    searcher, fetcher = SerpApiSearcher(), PageFetcher()
    qs = questions()
    todo = [q for q in qs if not searcher.cached(aimode.params(q["q"]))]
    if len(todo) > max_searches:
        raise SystemExit(f"{len(todo)} questions aren't cached, over the limit of {max_searches}. Nothing spent.")
    print(f"{len(qs)} questions, {len(todo)} SerpApi searches to spend. Searches left on the plan: {searches_left()}")
    rows = []
    for i, q in enumerate(qs, 1):
        params = aimode.params(q["q"])
        if not searcher.cached(params) and searcher.calls >= max_searches:
            raise SystemExit("Search limit reached.")
        try:
            rows.append(record(q, searcher.search(params), fetcher.fetch))
        except RuntimeError as e:  # a SerpApi error; the key never appears in it
            rows.append({**q, "error": str(e)})
        save(rows)
        s = rows[-1].get("audit", {}).get("summary", {})
        print(f"[{i}/{len(qs)}] {q['id']} {rows[-1].get('error') or s.get('figures')} "
              f"pages {s.get('pages_read')}/{s.get('pages')} | searches {searcher.calls}, pages fetched "
              f"{fetcher.fetched}", flush=True)
    print(f"Done. SerpApi searches spent: {searcher.calls}. Searches left on the plan: {searches_left()}")


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({round(100 * n / d)}%)" if d else "0/0"


def stats(rows: list[dict]) -> dict:
    answers = [r for r in rows if "audit" in r]
    stmts = [s for r in answers for s in r["audit"]["statements"] if s["kind"] != "offer"]
    figured = [s for s in stmts if s["mentions"]]
    cited = [m for s in figured if s["refs"] for m in s["mentions"]]
    uncited = [m for s in figured if not s["refs"] for m in s["mentions"]]
    full, snip = Counter(m["status"] for m in cited), Counter(m["snippet_status"] for m in cited)
    missing = [m for m in cited if m["status"] == "not_found"]
    pages = {u: p for r in answers for u, p in r["audit"]["pages"].items()}
    md = [r["markdown"] for r in answers]
    times = sorted(r["serpapi_s"] for r in answers if r.get("serpapi_s") is not None)
    return {
        "answers": len(answers), "errors": len(rows) - len(answers),
        "statements": len(stmts), "with_figures": len(figured),
        "uncited_with_figures": sum(not s["refs"] for s in figured),
        "table_rows": sum(s["kind"] == "table" for s in stmts),
        "answers_with_uncited_figures": sum(any(s["mentions"] and not s["refs"] for s in r["audit"]["statements"]
                                                if s["kind"] != "offer") for r in answers),
        "cited_figures": len(cited), "full": dict(full), "snippets": dict(snip),
        "found_only_in_pages": sum(m["snippet_status"] == "not_found" and m["status"] in FOUND for m in cited),
        "missing_but_in_another_reference": sum(bool(m["elsewhere"]) for m in missing),
        "cited_dates": sum(m["kind"] == "date" for m in cited),
        "uncited_figures": len(uncited), "uncited_in_some_reference": sum(bool(m["elsewhere"]) for m in uncited),
        "references": sum(len(r["audit"]["references"]) for r in answers),
        "references_with_snippet_date": sum(bool(x["snippet_date"]) for r in answers for x in r["audit"]["references"]),
        "references_with_passage": sum(bool(x["passage"]) for r in answers for x in r["audit"]["references"]),
        "references_never_cited": sum(x["index"] not in {i for s in r["audit"]["statements"] for i in s["refs"]}
                                      for r in answers for x in r["audit"]["references"]),
        "pages": len(pages), "pages_read": sum(not p["error"] for p in pages.values()),
        "page_errors": dict(Counter(p["error"].split(":")[0] for p in pages.values() if p["error"])),
        "markdown_agree": sum(m["agree"] for m in md), "markdown_differ": sum(len(m["differ"]) for m in md),
        "markdown_unmatched": sum(m["unmatched"] for m in md),
        "markdown_table_rows_cited": sum(m["table_rows_cited"] for m in md),
        "serpapi_s": {"median": round(statistics.median(times), 1), "p90": times[math.ceil(0.9 * len(times)) - 1],
                      "max": times[-1]} if times else {},
        "footnote_checker": summarise([Verdict(s["text"], [], s["footnote_status"], 0.0, None, []) for s in stmts]),
        "footnote_checker_cited": summarise([Verdict(s["text"], [], s["footnote_status"], 0.0, None, [])
                                             for s in stmts if s["refs"]]),
    }


def statement_flags(rows: list[dict], status_key: str) -> tuple[int, int]:
    """Cited statements stating a number (dates aside) whose cited text lacks at least one of their numbers."""
    stmts = [s for r in rows if "audit" in r for s in r["audit"]["statements"]
             if s["kind"] != "offer" and s["refs"] and any(m["kind"] == "number" for m in s["mentions"])]
    return sum(any(m["kind"] == "number" and m[status_key] == "not_found" for m in s["mentions"]) for s in stmts), \
        len(stmts)


def _all_pages_read(audit: dict, statement: dict) -> bool:
    urls = {r["index"]: r["url"] for r in audit["references"]}
    return all(not audit["pages"].get(urls.get(i), {"error": "missing"})["error"] for i in statement["refs"])


def hand_check(rows: list[dict]) -> dict:
    """The hand labels of every flag, split by whether the script could read every page the statement cites."""
    by_id = {r["id"]: r for r in rows}
    out = {"all": Counter(), "every_page_read": Counter(), "a_page_not_read": Counter()}
    for f in json.loads(LABELS.read_text())["flags"]:
        a = by_id[f["question_id"]]["audit"]
        read = _all_pages_read(a, a["statements"][f["at"][0]])
        out["all"][f["label"]] += 1
        out["every_page_read" if read else "a_page_not_read"][f["label"]] += 1
    return {k: dict(v) for k, v in out.items()}


def causes() -> None:
    """For each flag the hand check found stated: was a cited page unread, was the figure outside the extracted main
    text, or is it not in the page's HTML at all? Re-reads those pages' full HTML text now, which may have changed."""
    import trafilatura

    rows = {r["id"]: r for r in json.loads(OUT.read_text())["rows"]}
    fetcher = PageFetcher()
    client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)

    def html_text(url: str) -> str:
        try:
            return trafilatura.html2txt(client.get(url).content) or ""
        except httpx.HTTPError:
            return ""

    out = []
    for f in json.loads(LABELS.read_text())["flags"]:
        if f["label"] != "stated":
            continue
        row = rows[f["question_id"]]
        statement = row["audit"]["statements"][f["at"][0]]
        cited = [aimode.Reference(**r) for r in row["audit"]["references"] if r["index"] in statement["refs"]]
        errors = [e for r in cited if (e := fetcher.fetch(r.url).error)]
        if errors:
            cause = "page not read"
        else:
            pool = [aimode.evidence(r, Page(r.url, html_text(r.url), fetcher.fetch(r.url).date)) for r in cited]
            m = next(m for m in aimode.check(statement["text"], statement["refs"], pool, row["searched_at"][:10])
                     if m.written == f["figure"])
            cause = "outside the extracted main text" if m.status in FOUND else "not in the page's HTML"
        out.append({"n": f["n"], "question_id": f["question_id"], "figure": f["figure"], "cause": cause,
                    "errors": errors, "urls": [r.url for r in cited]})
    CAUSES.write_text(json.dumps({"refetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "causes": out}, indent=1, ensure_ascii=False) + "\n")
    print(Counter(c["cause"] for c in out))


def report() -> None:
    rows = json.loads(OUT.read_text())["rows"]
    groups = {"All": rows, "Footnote's 20": [r for r in rows if r["topic"] == "footnote_eval"],
              "New 60": [r for r in rows if r["topic"] != "footnote_eval"],
              "Live figures": [r for r in rows if r["live"]], "Not live": [r for r in rows if not r["live"]]}
    out = {name: stats(rs) for name, rs in groups.items()}
    a = out["All"]
    if LABELS.exists():
        out["hand_check"] = hand_check(rows)
    SUMMARY.write_text(json.dumps(out, indent=1) + "\n")
    print(f"\nAnswers: {a['answers']} ({a['errors']} errors). Statements: {a['statements']}, "
          f"{a['with_figures']} with figures, {_pct(a['uncited_with_figures'], a['with_figures'])} of those uncited "
          f"({a['table_rows']} table rows).")
    print("\n| Cited figures | Snippets only | Pages read |\n|---|---|---|")
    for label, keys in (("As written or same value", ("literal", "same_value", "in_text")),
                        ("Converted", ("converted",)), ("Rounded", ("rounded",)),
                        ("Only a page's own date", ("metadata_only",)), ("The day of the search", ("search_date",)),
                        ("Not in the cited pages", ("not_found",))):
        print(f"| {label} | {_pct(sum(a['snippets'].get(k, 0) for k in keys), a['cited_figures'])} | "
              f"{_pct(sum(a['full'].get(k, 0) for k in keys), a['cited_figures'])} |")
    for name, rs in groups.items():
        print(f"{name}: cited statements with a number missing from cited text, snippets "
              f"{_pct(*statement_flags(rs, 'snippet_status'))}, pages {_pct(*statement_flags(rs, 'status'))}")
    if "hand_check" in out:
        print("\nHand check of every flag:", json.dumps(out["hand_check"]))


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-searches", type=int, default=77)
    ap.add_argument("--report", action="store_true", help="summarise evals/aimode_results.json; no API calls")
    ap.add_argument("--causes", action="store_true", help="diagnose flags the hand check found; re-reads pages")
    args = ap.parse_args()
    report() if args.report else causes() if args.causes else run(args.max_searches)


if __name__ == "__main__":
    main()
