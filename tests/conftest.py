import json
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


class FakeSearcher:
    def __init__(self):
        self.seen = []

    def search(self, params):
        self.seen.append(params)
        return json.loads((FIX / f"{params['engine']}.json").read_text())


class FakeAnswerer:
    """Returns canned claims so the pipeline can be tested without a model."""

    def __init__(self, cited, closed=None):
        self.cited, self.closed = cited, closed or []

    def answer(self, question, sources):
        return {"claims": self.cited if sources is not None else self.closed, "gaps": ["exact closing date"]}


@pytest.fixture
def raw():
    return {e: json.loads((FIX / f"{e}.json").read_text()) for e in ("google", "google_news", "google_scholar")}


@pytest.fixture
def searcher():
    return FakeSearcher()


@pytest.fixture
def make_answerer():
    return FakeAnswerer
