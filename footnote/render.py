"""Readable output: claims with footnotes, flags on anything the checker couldn't back up."""

from __future__ import annotations

from urllib.parse import urlparse

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
