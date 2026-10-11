"""Audit a Google AI Mode answer: is every figure it states in the pages it cites? No model involved.

SerpApi's google_ai_mode engine returns the answer as text blocks (paragraphs, list items, tables), each with the
indexes of the references it cites. Every number, and every date precise to a month, is looked for in the cited
references: title, snippet, the passage Google's link highlights and, when fetched, the page's main text.

A number is found as written ("3.75"), as the same value ("3.750"), or converted ("3-3/4"; "25 basis points" for
"1/4 percentage point"; "146.39 crore" for "1,463,900,000"). A date the cited text doesn't state may still match a
cited page's own date (its published date, a date in its URL, the date before its snippet); that is reported apart.
Bare years are skipped, since almost every page states several.

A found figure is only on the page somewhere; a missing one is a real gap.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from urllib.parse import unquote, urldefrag

from .pages import Page

FOUND = ("literal", "same_value", "converted", "in_text")

OFFER = re.compile(r"(?:if you(?:['’]d| would)? (?:like|want)|(?:would|do) you (?:like|want)|let me know"
                   r"|i can also)\b", re.I)
SNIPPET_DATE = re.compile(r"([A-Z][a-z]{2} \d{1,2}, \d{4}|\d+ (?:hours?|days?|weeks?) ago)\s+[—–-]\s+")

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH = (r"(?:january|february|march|april|may|june|july|august|september|october|november|december"
          r"|jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)")
_DASH = r"[-‐‑–—]"
# "16 September 2026", "Sept. 16, 2026", "October 27–28, 2026", "October 2026", "2026-09-16", "16/09/2026"
DATE = re.compile(
    rf"\b(?:(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?(?:\s*{_DASH}\s*\d{{1,2}})?\s+)?(?P<m>{_MONTH})\b\.?"
    rf"(?:\s+(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?\b(?:\s*{_DASH}\s*\d{{1,2}}\b)?)?(?:,?\s+(?P<y>(?:19|20)\d\d)\b)?"
    rf"|\b(?P<iy>(?:19|20)\d\d){_DASH}(?P<im>\d\d){_DASH}(?P<id>\d\d)\b"
    r"|\b(?P<a>\d{1,2})[/.](?P<b>\d{1,2})[/.](?P<ny>(?:19|20)\d\d)\b", re.I)
YEAR = re.compile(r"(?<![\d.,])(?:19|20)\d\d(?!\d|[.,]\d)")
URL_DATE = re.compile(r"(?<!\d)(20\d\d)[/_-]?(0[1-9]|1[0-2])[/_-]?(0[1-9]|[12]\d|3[01])(?!\d)"
                      r"|(?<!\d)(20\d\d)/(0?[1-9]|1[0-2])/")

_ONES = "one two three four five six seven eight nine".split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
WORDS = {w: i for i, w in enumerate(["zero", *_ONES, *"ten eleven twelve thirteen fourteen fifteen sixteen "
                                                       "seventeen eighteen nineteen".split()])}
WORDS |= {w: 20 + 10 * i for i, w in enumerate(_TENS)}
SCALES = {"thousand": 10**3, "k": 10**3, "lakh": 10**5, "million": 10**6, "mn": 10**6, "m": 10**6, "crore": 10**7,
          "billion": 10**9, "bn": 10**9, "b": 10**9, "trillion": 10**12, "tn": 10**12, "t": 10**12}


def _number_pattern(words: bool) -> re.Pattern:
    spelled = (rf"|(?P<tens>{'|'.join(_TENS)})[-\s](?P<ones>{'|'.join(_ONES)})"
               rf"|(?P<word>{'|'.join(sorted(WORDS, key=len, reverse=True))})") if words else ""
    return re.compile(
        r"(?<![\w.,/])(?<![A-Za-z]-)(?:"  # not part of a name such as "H-1B" or "COVID-19"
        r"(?P<ver>\d+(?:\.\d+){2,})"
        r"|(?P<w>\d+)[-\s](?P<fn>\d+)/(?P<fd>\d+)"
        r"|(?P<n>\d+)/(?P<d>\d+)"
        r"|(?P<num>\d{1,3}(?:,\d{2,3})*,\d{3}(?:\.\d+)?|\d+(?:\.\d+)?)(?:st|nd|rd|th)?" + spelled + ")"
        r"(?:\s?(?:%|percent\b|per\s?cent\b|percentage points?\b))?"
        r"(?:\s?(?P<scale>thousand|lakh|million|crore|billion|trillion|mn|bn|tn)s?\b|(?P<abbr>(?-i:[KMBT]))\b)?"
        r"(?:\s?(?P<bps>basis points?|bps|bp)\b)?(?!\w)", re.I)


DIGITS, ANY_NUMBER = _number_pattern(False), _number_pattern(True)


@dataclass(frozen=True)
class Num:
    written: str
    digits: str | None  # the figure as written, commas dropped; None when spelled out
    value: Decimal | None  # None for a version such as 3.15.0, which only matches as written
    scale: int | None
    plain: bool  # digits with an optional decimal point, so 4.0 and 4.00 are the same value
    bps: bool
    start: int

    @property
    def values(self) -> set[Decimal]:
        """Every value the figure can be read as: scaled, and basis points as percentage points."""
        if self.value is None:
            return set()
        return {self.value * (self.scale or 1)} | ({self.value / 100} if self.bps else set())


def numbers(text: str, words: bool = False) -> list[Num]:
    out = []
    for m in (ANY_NUMBER if words else DIGITS).finditer(text):
        scale = SCALES[(m["scale"] or m["abbr"]).lower()] if m["scale"] or m["abbr"] else None
        if m["ver"]:
            digits, value, plain = m["ver"], None, False
        elif m["fd"]:
            if int(m["fd"]) == 0:
                continue
            digits, plain = f"{m['w']}-{m['fn']}/{m['fd']}", False
            value = int(m["w"]) + Decimal(m["fn"]) / int(m["fd"])
        elif m["d"]:
            if int(m["d"]) == 0:
                continue
            digits, value, plain = f"{m['n']}/{m['d']}", Decimal(m["n"]) / int(m["d"]), False
        elif m["num"]:
            digits = m["num"].replace(",", "")
            value, plain = Decimal(digits), True
        else:
            spelled = WORDS[(m["tens"] or m["word"]).lower()] + (_ONES.index(m["ones"].lower()) + 1 if m["ones"] else 0)
            digits, value, plain = None, Decimal(spelled), False
        out.append(Num(m.group(0), digits, value, scale, plain, bool(m["bps"]), m.start()))
    return out


def _date_parts(m: re.Match) -> list[tuple[int | None, int, int | None]]:
    """(year, month, day) readings of a DATE match; none when it gives a month alone."""
    if m["iy"]:
        y, mo, d = int(m["iy"]), int(m["im"]), int(m["id"])
        return [(y, mo, d)] if 1 <= mo <= 12 and 1 <= d <= 31 else []
    if m["ny"]:  # 09/10/2026 is September or October depending on the country, so both readings count
        a, b, y = int(m["a"]), int(m["b"]), int(m["ny"])
        return list(dict.fromkeys((y, mo, d) for mo, d in ((a, b), (b, a)) if 1 <= mo <= 12 and 1 <= d <= 31))
    day = m["d1"] or m["d2"]
    if not day and not m["y"]:
        return []
    return [(int(m["y"]) if m["y"] else None, MONTHS.index(m["m"][:3].lower()) + 1, int(day) if day else None)]


def dates(text: str) -> list[tuple[str, list[tuple], int]]:
    """(phrase, readings, offset) for every date precise to the month."""
    return [(m.group(0), parts, m.start()) for m in DATE.finditer(text) if (parts := _date_parts(m))]


def _blank(text: str) -> str:
    """Dates and bare years replaced by spaces, so their digits aren't read as figures; offsets are kept."""
    text = DATE.sub(lambda m: " " * len(m.group(0)), text)
    return YEAR.sub(lambda m: " " * len(m.group(0)), text)


def _agrees(claim: tuple, page: tuple) -> bool:
    (cy, cm, cd), (py, pm, pd) = claim, page
    return cm == pm and (cd is None or cd == pd) and (cy is None or py is None or cy == py)


def url_dates(url: str) -> list[tuple[int, int, int | None]]:
    return [(int(m[1]), int(m[2]), int(m[3])) if m[1] else (int(m[4]), int(m[5]), None)
            for m in URL_DATE.finditer(url)]


@dataclass
class Reference:
    index: int
    title: str
    url: str  # without the text fragment
    snippet: str  # without the date Google sometimes puts in front
    source: str = ""
    passage: str = ""  # the text Google's link highlights on the page
    snippet_date: str | None = None


def _passage(fragment: str) -> str:
    """The quoted text in a '#:~:text=start,end' link, which is the page's own wording."""
    if ":~:" not in fragment:
        return ""
    quoted = [unquote(p.removeprefix("-").removesuffix("-")) for d in fragment.split(":~:", 1)[1].split("&")
              if d.startswith("text=") for p in d[5:].split(",")]
    return " … ".join(quoted)


def references(response: dict) -> list[Reference]:
    out = []
    for r in response.get("references") or []:
        url, fragment = urldefrag(r.get("link") or "")
        snippet = r.get("snippet") or ""
        m = SNIPPET_DATE.match(snippet)
        out.append(Reference(r["index"], r.get("title") or "", url, snippet[m.end():] if m else snippet,
                             r.get("source") or "", _passage(fragment), m.group(1) if m else None))
    return out


@dataclass
class Mention:
    written: str
    kind: str  # number | date
    status: str  # literal | same_value | converted | in_text | metadata_only | not_found | uncited
    ref: int | None = None  # the cited reference it was found in
    matched: str = ""  # what it matched: the source's wording, or which page date
    context: str = ""  # the source text around the match
    elsewhere: list[int] = field(default_factory=list)  # uncited references of this answer whose text states it


@dataclass
class Statement:
    text: str
    refs: list[int]
    kind: str  # paragraph | list | table | offer
    mentions: list[Mention] = field(default_factory=list)


def statements(response: dict) -> tuple[list[Statement], list[str]]:
    """Paragraphs, list items and table rows, in order, plus the types of any blocks not read."""
    out, skipped, offering = [], [], False

    def items(block: dict):
        for item in block.get("list") or []:
            yield item
            yield from items(item)

    for block in response.get("text_blocks") or []:
        kind = block.get("type")
        if kind == "paragraph":
            offering = bool(OFFER.match(block.get("snippet") or ""))  # "If you'd like, I can…" and its list
            out.append(Statement(block.get("snippet") or "", list(block.get("reference_indexes") or []),
                                 "offer" if offering else "paragraph"))
        elif kind == "list":
            out += [Statement(i.get("snippet") or "", list(i.get("reference_indexes") or []),
                              "offer" if offering else "list") for i in items(block)]
        elif kind == "table":
            head, *rows = block.get("table") or [[]]
            out += [Statement("; ".join(f"{h}: {c}" for h, c in zip(head, row)), [], "table") for row in rows]
            offering = False
        else:
            skipped.append(kind)
    return out, skipped


@dataclass
class Evidence:
    ref: int
    text: str
    numbers: list[Num]
    dates: list[tuple[str, list[tuple], int]]
    meta: list[tuple[str, tuple]]  # (which page date, reading)


def evidence(ref: Reference, page: Page | None) -> Evidence:
    text = " \n".join(t for t in (ref.title, ref.snippet, ref.passage, page.text if page else "") if t)
    meta = [(f"published {page.date}", p) for _, ps, _ in dates(page.date or "") for p in ps] if page else []
    meta += [("date in URL", p) for p in url_dates(ref.url)]
    if ref.snippet_date:
        meta += [(f"snippet dated {ref.snippet_date}", p) for _, ps, _ in dates(ref.snippet_date) for p in ps]
    return Evidence(ref.index, text, numbers(_blank(text), words=True), dates(text), meta)


def _context(text: str, start: int, length: int, pad: int = 70) -> str:
    return " ".join(text[max(0, start - pad):start + length + pad].split())


def _find_number(n: Num, pool: list[Evidence]) -> tuple[str, Evidence | None, Num | None]:
    best: tuple[int, Evidence | None, Num | None] = (len(FOUND), None, None)
    for e in pool:
        for t in e.numbers:
            same_scale = n.scale is None or t.scale is None or n.scale == t.scale
            if same_scale and n.digits and t.digits and (n.digits == t.digits or  # or "3.15" from "3.15.0"
                                                         t.value is None and t.digits.startswith(n.digits + ".")):
                return "literal", e, t
            rank = (1 if same_scale and n.plain and t.plain and n.value == t.value
                    else 2 if n.values & t.values else len(FOUND))
            if rank < best[0]:
                best = (rank, e, t)
    return (FOUND[best[0]] if best[0] < len(FOUND) else "not_found"), best[1], best[2]


def _find_date(readings: list[tuple], pool: list[Evidence]) -> tuple[str, Evidence | None, str, int]:
    for e in pool:
        for phrase, parts, start in e.dates:
            if any(_agrees(c, p) for c in readings for p in parts):
                return "in_text", e, phrase, start
    for e in pool:
        for label, p in e.meta:
            if any(_agrees(c, p) for c in readings):
                return "metadata_only", e, label, -1
    return "not_found", None, "", -1


def check(text: str, refs: list[int], pool: list[Evidence]) -> list[Mention]:
    cited = [e for e in pool if e.ref in refs]
    others = [e for e in pool if e.ref not in refs]
    found: list[tuple[int, Mention]] = []
    for phrase, readings, start in dates(text):
        status, e, matched, at = _find_date(readings, cited) if refs else ("uncited", None, "", -1)
        m = Mention(phrase, "date", status, e.ref if e else None, matched,
                    _context(e.text, at, len(matched)) if e and at >= 0 else "")
        if status not in FOUND:
            m.elsewhere = [o.ref for o in others if _find_date(readings, [o])[0] == "in_text"]
        found.append((start, m))
    for n in numbers(_blank(text)):
        status, e, t = _find_number(n, cited) if refs else ("uncited", None, None)
        m = Mention(n.written, "number", status, e.ref if e else None, t.written if t else "",
                    _context(e.text, t.start, len(t.written)) if e and t else "")
        if status not in FOUND:
            m.elsewhere = [o.ref for o in others if _find_number(n, [o])[0] != "not_found"]
        found.append((n.start, m))
    return [m for _, m in sorted(found, key=lambda x: x[0])]


@dataclass
class Audit:
    question: str
    statements: list[Statement]
    references: list[Reference]
    pages: dict[str, Page]  # by URL; empty when only snippets were read
    skipped: list[str]  # text block types the parser doesn't read

    def summary(self) -> dict:
        checked = [s for s in self.statements if s.kind != "offer"]
        return {"statements": len(checked), "with_figures": sum(bool(s.mentions) for s in checked),
                "uncited_with_figures": sum(bool(s.mentions) and not s.refs for s in checked),
                "figures": dict(Counter(m.status for s in checked for m in s.mentions)),
                "pages": len(self.pages), "pages_read": sum(p.ok for p in self.pages.values())}

    def to_dict(self) -> dict:
        return {"question": self.question, "summary": self.summary(),
                "statements": [asdict(s) for s in self.statements],
                "references": [asdict(r) for r in self.references],
                "pages": {u: {"date": p.date, "error": p.error, "chars": len(p.text), "fetched_at": p.fetched_at}
                          for u, p in self.pages.items()},
                "skipped": self.skipped}


def params(question: str, hl: str = "en", gl: str = "us") -> dict:
    return {"engine": "google_ai_mode", "q": question, "hl": hl, "gl": gl}


def audit(response: dict, fetch: Callable[[str], Page] | None = None, workers: int = 8) -> Audit:
    refs = references(response)
    pages: dict[str, Page] = {}
    if fetch:
        urls = list(dict.fromkeys(r.url for r in refs if r.url))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            pages = dict(zip(urls, ex.map(fetch, urls)))
    pool = [evidence(r, pages.get(r.url)) for r in refs]
    stmts, skipped = statements(response)
    for s in stmts:
        if s.kind != "offer":
            s.mentions = check(s.text, s.refs, pool)
    return Audit((response.get("search_parameters") or {}).get("q", ""), stmts, refs, pages, skipped)
