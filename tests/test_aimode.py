import json
from pathlib import Path

import httpx
import pytest

from footnote import aimode, cli
from footnote.aimode import Reference, audit, check, dates, evidence, numbers, references, statements
from footnote.pages import USER_AGENT, Page, PageFetcher
from footnote.render import audit_text


def _live(name):  # trimmed real SerpApi google_ai_mode responses
    return json.loads((Path(__file__).parent / "fixtures" / "live" / f"google_ai_mode_{name}.json").read_text())


def test_statements_keep_their_own_citations_and_set_offers_aside():
    fed, skipped = statements(_live("fed"))
    assert skipped == []
    assert [s.kind for s in fed] == ["paragraph"] + ["list"] * 7 + ["offer"]
    assert [s.refs for s in fed[1:8]] == [[], [], [], [0, 1], [], [], [0, 1]]
    python, _ = statements(_live("python"))
    assert [s.kind for s in python] == ["paragraph", "table", "table", "table", "paragraph", "offer", "offer", "offer"]
    assert python[1].text == ("Version: Python 3.15.0; Release Date: Oct 9, 2026; "
                              "Support Status: Latest major bugfix release")


def test_references_split_off_the_linked_passage_and_snippet_date():
    r, = references({"references": [{"index": 0, "title": "t", "snippet": "Sep 18, 2025 — Rates rose.",
                                     "link": "https://x.org/a#:~:text=the%20rate-,rose%20to%205%25,-today"}]})
    assert (r.url, r.snippet, r.snippet_date) == ("https://x.org/a", "Rates rose.", "Sep 18, 2025")
    assert r.passage == "the rate … rose to 5% … today"


def test_figures_skip_bare_years_and_names():
    assert [n.written for n in numbers(aimode._blank("In 2026, 12 people paid an H-1B fee of $100,000."))] == \
        ["12", "100,000"]
    assert [n.written for n in numbers("COVID-19 cost 4.2bn, or 25 basis points; Python 3.15.0")] == \
        ["4.2bn", "25 basis points", "3.15.0"]


@pytest.mark.parametrize("claim, page, status", [
    ("5.50%", "to 5.50 per cent", "literal"),
    ("1,200 jobs", "1200 jobs", "literal"),
    ("$4.2B", "4.2 billion dollars", "literal"),
    ("Python 3.15", "Python 3.15.0", "literal"),
    ("4.0%", "4 percent", "same_value"),
    ("3.75%", "3-3/4 to 4 percent", "converted"),
    ("25 basis points", "by 1/4 percentage point", "converted"),
    ("0.25 percentage point", "a 25bps rise", "converted"),
    ("1,463,900,000", "146.39 crore", "converted"),
    ("12 members", "twelve members", "converted"),
    ("the 13th-lowest extent", "thirteenth lowest", "converted"),
    ("Kubernetes v1.37.1", "Kubernetes v1.37.1 is out", "literal"),
    ("1.48 billion", "is 1,480,243,044", "rounded"),
    ("17.8%", "17.83 per cent", "rounded"),
    ("$5.5–5.6 trillion", "worth $5.53 trillion", "rounded"),  # the range's scale applies to both ends
    ("4%", "3.6 percent", "not_found"),  # one significant digit: too coarse to call a rounding
    ("25 jobs", "24.8 jobs", "not_found"),  # whole numbers aren't read as roundings
    ("1.2 billion", "1.2 million", "not_found"),
    ("Python 3.15.0", "Python 3.15", "not_found"),
    ("12 members", "on December 12, 2025", "not_found"),  # a date's digits aren't figures
])
def test_number_provenance(claim, page, status):
    pool = [evidence(Reference(0, "", "https://x.org", ""), Page("https://x.org", page))]
    assert check(claim, [0], pool)[0].status == status


def test_a_repeated_figure_counts_once():
    pool = [evidence(Reference(0, "", "https://x.org", ""), Page("https://x.org", "around $104.28"))]
    assert [m.written for m in check("$104.72 per barrel, in the $104.28 to $104.72 range", [0], pool)] == \
        ["104.72", "104.28"]


def test_dates_need_a_month_and_a_day_or_year():
    text = ("On 16 September 2026, in October, Oct. 9, 2026, on 2026-09-16, in May 2025, October 27–28, 2026, "
            "09/10/2026")
    assert [(d[0], d[1]) for d in dates(text)] == [
        ("16 September 2026", [(2026, 9, 16)]), ("Oct. 9, 2026", [(2026, 10, 9)]), ("2026-09-16", [(2026, 9, 16)]),
        ("May 2025", [(2025, 5, None)]), ("October 27–28, 2026", [(2026, 10, 27)]),
        ("09/10/2026", [(2026, 9, 10), (2026, 10, 9)])]


def test_dates_found_in_text_or_only_as_a_page_date():
    ref = Reference(0, "Fed statement", "https://fed.gov/monetary20260916a.htm", "Rates rose.",
                    snippet_date="Sep 18, 2025")
    pool = [evidence(ref, Page(ref.url, "The meeting ends October 28.", "2026-09-17"))]
    got = {m.written: (m.status, m.matched) for m in check(
        "On September 16, 2026, September 17, 2026, Sep 18, 2025, October 28, 2026, May 1, 2026, "
        "as of 11 October 2026.", [0], pool, searched="2026-10-11")}
    assert got == {"September 16, 2026": ("metadata_only", "date in URL"),
                   "September 17, 2026": ("metadata_only", "published 2026-09-17"),
                   "Sep 18, 2025": ("metadata_only", "snippet dated Sep 18, 2025"),
                   "October 28, 2026": ("in_text", "October 28"),
                   "May 1, 2026": ("not_found", ""),
                   "11 October 2026": ("search_date", "searched 2026-10-11")}


def test_fed_answer_from_snippets_then_pages():
    by = lambda a: {(s.text[:12], m.written): (m.status, m.elsewhere) for s in a.statements for m in s.mentions}
    snippets = by(audit(_live("fed")))
    assert snippets[("At its lates", "September 16, 2026")] == ("metadata_only", [])  # only in the URL
    assert snippets[("Target Range", "3.75%")] == ("uncited", [0])  # the "3-3/4" reference 0 states
    assert snippets[("Next Meeting", "October 27–28, 2026")] == ("not_found", [])

    minutes = "the next meeting of the Committee would be held on Tuesday–Wednesday, October 27–28, 2026."
    pages = by(audit(_live("fed"), lambda url: Page(url, minutes if "minutes" in url else "", None)))
    assert pages[("Next Meeting", "October 27–28, 2026")] == ("in_text", [])


def test_audit_text_marks_every_figure():
    a = audit(_live("fed"))
    out = audit_text(a)
    assert "◷ September 16, 2026 (date in URL)" in out and "✗ October 27–28, 2026" in out
    assert "· 3.75% (in [1])" in out and "· 12 (in no reference)" in out
    assert "Economic Activity" in out and "If you'd like" not in out
    assert a.summary() == {"statements": 8, "with_figures": 6, "uncited_with_figures": 3, "pages": 0,
                           "pages_read": 0, "figures": {"metadata_only": 1, "literal": 4, "uncited": 8,
                                                        "same_value": 1, "in_text": 1, "not_found": 1}}


def test_page_fetcher_reads_main_text_and_caches_failures(tmp_path):
    body = "<p>The Committee decided to raise the target range by 1/4 percentage point to 3-3/4 to 4 percent.</p>"
    html = ("<html><head><title>FOMC</title><meta property='article:published_time' content='2026-09-16'></head>"
            f"<body><article><h1>Rates</h1>{body * 5}</article></body></html>")
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/ok":
            return httpx.Response(200, html=html)
        if request.url.path == "/pdf":
            return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        return httpx.Response(403)

    f = PageFetcher(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)))
    page = f.fetch("https://x.org/ok")
    assert page.ok and "3-3/4 to 4 percent" in page.text and page.date == "2026-09-16"
    assert f.fetch("https://x.org/pdf").error == "not HTML: application/pdf"
    assert f.fetch("https://x.org/no").error == "HTTP 403"
    assert [f.fetch(u) for u in ("https://x.org/ok", "https://x.org/no")] == [page, f.fetch("https://x.org/no")]
    assert seen == ["/ok", "/pdf", "/no"] and f.fetched == 3  # each page read once, failures included
    assert PageFetcher(None).client.headers["user-agent"] == USER_AGENT


def test_cli_audit(monkeypatch, capsys):
    class Searcher:
        def __init__(self, cache_dir):
            self.calls, self.hits = 1, 0

        def search(self, params):
            assert params == {"engine": "google_ai_mode", "q": "Fed?", "hl": "en", "gl": "us"}
            return _live("fed")

    class Fetcher:
        fetched = 0

        def __init__(self, cache_dir):
            pass

        def fetch(self, url):
            return Page(url, error="HTTP 403")

    monkeypatch.setattr(cli, "SerpApiSearcher", Searcher)
    monkeypatch.setattr(cli, "PageFetcher", Fetcher)
    assert cli.main(["audit", "Fed?", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["pages_read"] == 0 and data["pages"][data["references"][0]["url"]]["error"] == "HTTP 403"
