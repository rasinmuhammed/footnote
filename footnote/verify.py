"""Check every claim against the snippets it cites. No model involved: the rules are simple enough to read.

A claim fails if it cites a source that doesn't exist, or states a number that none of its cited sources contain.
A date counts as contained when it agrees with a cited source's own date, which the model is shown and told to use.
Otherwise it is scored by how much of its content vocabulary appears in its best cited source.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .sources import Source

STOP = set("""a an and are as at be been but by for from has have he her his i if in into is it its of on or our
she that the their them they this to was were will with what which who whom whose why how when where than then
there these those also about after before over under more most less such can could may might should would does did
do not no yes all any each other some only very just per via""".split())

NUMBER = re.compile(r"(?<![\w.])[$€£₹]?\d[\d,]*(?:\.\d+)?%?")
WORD = re.compile(r"[a-z0-9]+")

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
# "7 October 2026", "Oct. 7, 2026", "October 2026", "Jun 25, 2026", or a bare year
DATE = re.compile(r"\b(?:(\d{1,2})\s+)?(january|february|march|april|may|june|july|august|september|october|november|"
                  r"december|jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)\b\.?(?:\s+(\d{1,2})(?:st|nd|rd|th)?\b)?"
                  r"(?:,?\s+((?:19|20)\d\d)\b)?|\b((?:19|20)\d\d)\b", re.I)
ISO_DATE = re.compile(r"((?:19|20)\d\d)-(\d\d)-(\d\d)")

SUPPORTED, WEAK = 0.5, 0.25


def numbers(text: str) -> set[str]:
    """Numeric values in a string, normalised so '1,200' and '1200' match and '%'/currency are ignored."""
    out = set()
    for m in NUMBER.findall(text):
        v = m.strip("$€£₹%").replace(",", "")
        if v.endswith(".0"):
            v = v[:-2]
        if v:
            out.add(v)
    return out


def _parts(m: re.Match) -> tuple[int | None, int | None, int | None]:
    """(year, month, day) of a DATE match, None where the text doesn't say."""
    if m.group(5):
        return int(m.group(5)), None, None
    day = m.group(1) or m.group(3)
    return (int(m.group(4)) if m.group(4) else None, MONTHS.index(m.group(2)[:3].lower()) + 1,
            int(day) if day else None)


def source_date(date: str | None) -> tuple[int | None, int | None, int | None] | None:
    if not date:
        return None
    if iso := ISO_DATE.fullmatch(date):
        return tuple(map(int, iso.groups()))
    m = DATE.search(date)
    return _parts(m) if m else None


def without_source_dates(claim: str, cited: list[Source]) -> str:
    """Drop date phrases that agree with a cited source's date in every part they state; other numbers stay."""
    dates = [d for d in (source_date(s.date) for s in cited) if d]

    def agrees(m: re.Match) -> bool:
        return any(all(c is None or c == s for c, s in zip(_parts(m), d)) for d in dates)

    return DATE.sub(lambda m: " " if agrees(m) else m.group(0), claim)


def terms(text: str) -> set[str]:
    words = (w[:-1] if len(w) > 4 and w.endswith("s") else w for w in WORD.findall(text.lower()))
    return {w for w in words if len(w) > 2 and w not in STOP and not w.isdigit()}


@dataclass
class Verdict:
    claim: str
    source_ids: list[str]
    status: str  # supported | weak | unsupported | number_not_found | bad_citation
    support: float
    best_source: str | None
    missing_numbers: list[str]
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "supported"

    def to_dict(self) -> dict:
        return asdict(self)


def _score(claim: str, source: Source) -> float:
    ct = terms(claim)
    if not ct:
        return 1.0
    return len(ct & terms(source.title + " " + source.text)) / len(ct)


def check(claim: str, cited_ids: list[str], sources: list[Source]) -> Verdict:
    by_id = {s.id: s for s in sources}
    cited = [by_id[i] for i in cited_ids if i in by_id]
    unknown = [i for i in cited_ids if i not in by_id]
    if not cited_ids or unknown:
        return Verdict(claim, cited_ids, "bad_citation", 0.0, None, [],
                       f"cites unknown source(s): {', '.join(unknown)}" if unknown else "no citation")

    best = max(cited, key=lambda s: _score(claim, s))
    support = round(_score(claim, best), 3)
    pool = " ".join(s.title + " " + s.text for s in cited)
    missing = sorted(numbers(without_source_dates(claim, cited)) - numbers(pool))
    if missing:
        return Verdict(claim, cited_ids, "number_not_found", support, best.id, missing,
                       "states a number its sources don't contain")
    status = "supported" if support >= SUPPORTED else "weak" if support >= WEAK else "unsupported"
    return Verdict(claim, cited_ids, status, support, best.id, [])


def check_against_pool(claim: str, sources: list[Source]) -> Verdict:
    """For uncited (closed-book) claims: would any retrieved source back this up?"""
    if not sources:
        return Verdict(claim, [], "unsupported", 0.0, None, sorted(numbers(claim)), "no sources")
    ranked = sorted(sources, key=lambda s: _score(claim, s), reverse=True)[:3]
    return check(claim, [s.id for s in ranked], sources)
