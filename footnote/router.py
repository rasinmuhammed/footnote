"""Decide which SerpApi engines a question needs. Deterministic on purpose: cheap, testable, explainable."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

RECENT = re.compile(r"\b(today|yesterday|latest|recent(ly)?|this (week|month|year)|current(ly)?|now|news|"
                    r"announce[sd]?|launch(ed|es)?|release[sd]?|price|stock|election|20\d\d)\b", re.I)
RESEARCH = re.compile(r"\b(papers?|stud(y|ies)|research|evidence|trials?|meta-analys[ie]s|arxiv|"
                      r"peer[- ]reviewed|journal)\b", re.I)


@dataclass
class Plan:
    engines: list[str]
    reasons: list[str] = field(default_factory=list)


def plan(question: str) -> Plan:
    engines, reasons = ["google"], ["general web results"]
    if RECENT.search(question):
        engines.append("google_news")
        reasons.append("time-sensitive wording")
    if RESEARCH.search(question):
        engines.append("google_scholar")
        reasons.append("asks about research")
    return Plan(engines, reasons)
