"""Readable output: claims with footnotes, flags on anything the checker couldn't back up."""

from __future__ import annotations

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


def _mark(m: Mention) -> str:
    where = f" (in {''.join(f'[{i + 1}]' for i in m.elsewhere)})" if m.elsewhere else ""
    if m.status in ("converted", "rounded"):
        return f"≈ {m.written} ({m.matched})"
    if m.status in ("metadata_only", "search_date"):
        return f"◷ {m.written} ({m.matched}){where}"
    if m.status == "uncited":
        return f"· {m.written}{where or ' (in no reference)'}"
    return f"{MARKS[m.status]} {m.written}{where}"


def audit_text(a: Audit) -> str:
    lines = [a.question, ""]
    for s in a.statements:
        if s.kind == "offer":
            continue
        refs = "".join(f"[{i + 1}]" for i in s.refs) or ("(no citation)" if s.mentions else "")
        lines.append(f"{'' if s.kind == 'paragraph' else '- '}{s.text} {refs}".rstrip())
        if s.mentions:
            lines.append("    " + "   ".join(map(_mark, s.mentions)))
    lines += ["", "References"]
    for r in a.references:
        p = a.pages.get(r.url)
        note = ("" if p is None else f", not read: {p.error}" if not p.ok
                else f", dated {p.date}" if p.date else "")
        lines.append(f"[{r.index + 1}] {r.title} ({_domain(r.url)}{note})\n    {r.url}")
    n = a.summary()
    f = n["figures"]
    pages = f" | cited pages read: {n['pages_read']}/{n['pages']}" if n["pages"] else " | snippets only"
    found = f.get("literal", 0) + f.get("same_value", 0) + f.get("in_text", 0)
    lines += ["", f"{sum(f.values())} figures: {found} in a cited page, "
              f"{f.get('converted', 0) + f.get('rounded', 0)} converted or rounded, "
              f"{f.get('metadata_only', 0) + f.get('search_date', 0)} only a page's date or the search date, "
              f"{f.get('not_found', 0)} not in the cited pages, {f.get('uncited', 0)} with no citation{pages}",
              "✓ in a cited page  ≈ converted or rounded  ◷ only a page's date or the search date  "
              "✗ not in the cited pages  · no citation  (in [n]) found in that reference instead"]
    return "\n".join(lines)
