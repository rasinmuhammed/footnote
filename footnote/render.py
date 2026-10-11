"""Readable output: claims with footnotes, flags on anything the checker couldn't back up."""

from __future__ import annotations

from collections import Counter
from urllib.parse import urlparse

from .aimode import Audit, Mention
from .pipeline import Report

FLAG = {"supported": "", "weak": " (weak support)", "unsupported": " (not supported by its source)",
        "number_not_found": " (number not in source)", "bad_citation": " (bad citation)", "abstain": " (not an answer)"}


def _domain(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.") or "-"


def _numbering(report: Report) -> dict[str, int]:
    order: dict[str, int] = {}
    for v in report.verdicts:
        for sid in v.source_ids:
            if sid not in order and any(s.id == sid for s in report.sources):
                order[sid] = len(order) + 1
    return order


def text(report: Report) -> str:
    num = _numbering(report)
    lines = [report.question, ""]
    for v in report.verdicts:
        refs = "".join(f"[{num[s]}]" for s in v.source_ids if s in num)
        lines.append(f"- {v.claim} {refs}{FLAG[v.status]}")
    if report.gaps:
        lines += ["", "Not answered by the sources:"] + [f"- {g}" for g in report.gaps]
    by_id = {s.id: s for s in report.sources}
    lines += ["", "Sources"]
    for sid, n in num.items():
        s = by_id[sid]
        date = f", {s.date}" if s.date else ""
        lines.append(f"[{n}] {s.title} ({_domain(s.url)}{date}, via {s.engine})\n    {s.url}")
    ok = sum(v.ok for v in report.verdicts)
    lines += ["", f"{ok}/{len(report.verdicts)} claims backed by their cited source | engines: "
              f"{', '.join(report.engines)} | {report.timings.get('total_s', 0)}s"]
    return "\n".join(lines)


def markdown(report: Report) -> str:
    num = _numbering(report)
    by_id = {s.id: s for s in report.sources}
    out = [f"**{report.question}**", ""]
    for v in report.verdicts:
        refs = "".join(f"[^{num[s]}]" for s in v.source_ids if s in num)
        flag = f" _{FLAG[v.status].strip(' ()')}_" if FLAG[v.status] else ""
        out.append(f"- {v.claim}{refs}{flag}")
    out.append("")
    for sid, n in num.items():
        s = by_id[sid]
        out.append(f"[^{n}]: [{s.title}]({s.url}){', ' + s.date if s.date else ''}")
    return "\n".join(out)


MARKS = {"literal": "✓", "same_value": "✓", "in_text": "✓", "converted": "≈", "rounded": "≈",
         "metadata_only": "◷", "search_date": "◷", "not_found": "✗", "uncited": "·"}
MEANING = {"✓": "in a cited page", "≈": "converted or rounded", "◷": "only a page's date or the search date",
           "✗": "not in the cited pages", "·": "with no citation"}
COLOR = {"✓": 32, "≈": 33, "◷": 33, "✗": 31, "·": 36}  # green, yellow, yellow, red, cyan


def _paint(mark: str, color: bool) -> str:
    return f"\033[{COLOR[mark]}m{mark}\033[0m" if color else mark


def _mark(m: Mention, color: bool) -> str:
    mark = MARKS[m.status]
    where = f" (in {''.join(f'[{i + 1}]' for i in m.elsewhere)})" if m.elsewhere else ""
    note = (f" ({m.matched})" if mark in "≈◷" else "") + (where or (" (in no reference)" if mark == "·" else ""))
    return f"{_paint(mark, color)} {m.written}{note}"


def audit_text(a: Audit, color: bool = False) -> str:
    lines = [a.question, ""]
    for s in a.statements:
        if s.kind == "offer":
            continue
        refs = "".join(f"[{i + 1}]" for i in s.refs) or ("(no citation)" if s.mentions else "")
        lines.append(f"{'' if s.kind == 'paragraph' else '- '}{s.text} {refs}".rstrip())
        if s.mentions:
            lines.append("    " + "   ".join(_mark(m, color) for m in s.mentions))
    lines += ["", "References"]
    for r in a.references:
        p = a.pages.get(r.url)
        note = ("" if p is None else f", not read: {p.error}" if not p.ok
                else f", dated {p.date}" if p.date else "")
        lines.append(f"[{r.index + 1}] {r.title} ({_domain(r.url)}{note})\n    {r.url}")
    n = a.summary()
    by_mark = Counter()
    for status, k in n["figures"].items():
        by_mark[MARKS[status]] += k
    parts = ", ".join(f"{by_mark[m]} {meaning}" for m, meaning in MEANING.items() if by_mark[m])
    pages = f" | cited pages read: {n['pages_read']}/{n['pages']}" if n["pages"] else " | snippets only"
    lines += ["", f"{sum(by_mark.values())} figures{': ' + parts if parts else ''}{pages}",
              "  ".join(f"{_paint(m, color)} {meaning.removeprefix('with ')}" for m, meaning in MEANING.items())
              + "  (in [n]) found there"]
    return "\n".join(lines)
