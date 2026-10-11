"""Question in, cited and checked answer out."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .llm import Answerer
from .router import plan
from .search import Searcher, params_for
from .sources import Source, normalise
from .verify import Verdict, check, check_against_pool


@dataclass
class Report:
    question: str
    engines: list[str]
    reasons: list[str]
    sources: list[Source]
    verdicts: list[Verdict]
    gaps: list[str]
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def supported_share(self) -> float:
        return sum(v.ok for v in self.verdicts) / len(self.verdicts) if self.verdicts else 0.0

    def to_dict(self) -> dict:
        return {"question": self.question, "engines": self.engines, "reasons": self.reasons,
                "claims": [v.to_dict() for v in self.verdicts], "gaps": self.gaps,
                "sources": [s.to_dict() for s in self.sources], "supported_share": round(self.supported_share, 3),
                "timings": self.timings}


class Footnote:
    def __init__(self, searcher: Searcher, answerer: Answerer, per_engine: int = 6):
        self.searcher, self.answerer, self.per_engine = searcher, answerer, per_engine

    def retrieve(self, question: str, engines: list[str] | None = None) -> tuple[list[str], list[str], list[Source]]:
        p = plan(question)
        chosen = engines or p.engines
        reasons = p.reasons if engines is None else ["engines set by caller"]
        responses = {e: self.searcher.search(params_for(e, question)) for e in chosen}
        return chosen, reasons, normalise(responses, self.per_engine)

    def ask(self, question: str, engines: list[str] | None = None) -> Report:
        t0 = time.perf_counter()
        chosen, reasons, sources = self.retrieve(question, engines)
        t1 = time.perf_counter()
        out = self.answerer.answer(question, sources)
        t2 = time.perf_counter()
        verdicts = [check(c["text"], list(c.get("source_ids") or []), sources) for c in out["claims"]]
        t3 = time.perf_counter()
        return Report(question, chosen, reasons, sources, verdicts, out.get("gaps", []),
                      {"search_s": round(t1 - t0, 3), "answer_s": round(t2 - t1, 3),
                       "verify_s": round(t3 - t2, 3), "total_s": round(t3 - t0, 3)})

    def closed_book(self, question: str, sources: list[Source]) -> list[Verdict]:
        """Baseline: same model, no search. Its claims are then checked against what search found."""
        out = self.answerer.answer(question, None)
        return [check_against_pool(c["text"], sources) for c in out["claims"]]
