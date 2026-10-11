import json

from footnote import Footnote
from footnote.render import markdown, text
from footnote.router import plan
from footnote.search import SerpApiSearcher, params_for
from footnote.sources import normalise
from footnote.verify import check, numbers


def test_normalise_flattens_dedupes_and_numbers(raw):
    sources = normalise(raw)
    assert [s.id for s in sources] == [f"S{i}" for i in range(1, len(sources) + 1)]
    urls = [s.url for s in sources]
    assert urls.count("https://en.wikipedia.org/wiki/Mount_Example") == 1  # answer box, KG, organic deduped
    assert "https://empty.example.com" not in urls  # empty snippet dropped
    assert "https://times.example.com/record" in urls  # clustered news stories are unpacked
    scholar = next(s for s in sources if s.engine == "google_scholar")
    assert "Cited by 42" in scholar.text and "2021" in scholar.text


def test_router():
    assert plan("How tall is Mount Example?").engines == ["google"]
    assert plan("latest news on Mount Example").engines == ["google", "google_news"]
    assert plan("research on glacier retreat in 2026").engines == ["google", "google_news", "google_scholar"]


def test_params():
    assert params_for("google", "q")["num"] == 10
    assert "gl" not in params_for("google_scholar", "q")


def test_numbers_normalise():
    assert numbers("about 1,200 climbers, 18% and $4.0") == {"1200", "18", "4"}


def test_verdicts(raw):
    s = normalise(raw)
    ids = {x.url: x.id for x in s}
    wiki, club = ids["https://en.wikipedia.org/wiki/Mount_Example"], ids["https://alpine.example.org/mount-example"]
    assert check("Mount Example is 4,321 metres tall.", [wiki], s).status == "supported"
    v = check("Mount Example is 5,000 metres tall.", [wiki], s)
    assert v.status == "number_not_found" and v.missing_numbers == ["5000"]
    assert check("Mount Example is tall.", ["S99"], s).status == "bad_citation"
    assert check("Penguins enjoy jazz festivals in Lisbon.", [club], s).status == "unsupported"


def test_pipeline_end_to_end(searcher, make_answerer):
    claims = [
        {"text": "Mount Example is 4,321 metres tall.", "source_ids": ["S1"]},
        {"text": "Officials reported 1,450 summit attempts this year.", "source_ids": ["S4"]},
        {"text": "Mount Example had 2,000 summit attempts this year.", "source_ids": ["S4"]},
    ]
    fn = Footnote(searcher, make_answerer(claims, closed=[{"text": "Mount Example is 4,000 metres tall."}]))
    r = fn.ask("latest climbing numbers for Mount Example")
    assert r.engines == ["google", "google_news"]
    assert [v.status for v in r.verdicts] == ["supported", "supported", "number_not_found"]
    assert round(r.supported_share, 2) == 0.67
    baseline = fn.closed_book(r.question, r.sources)
    assert baseline[0].status == "number_not_found"
    out = text(r)
    assert "[1]" in out and "(number not in source)" in out and "2/3 claims backed" in out
    assert "[^1]:" in markdown(r)
    json.dumps(r.to_dict())


def test_cache_spends_one_search(tmp_path):
    class Client:
        calls = 0

        def search(self, params):
            Client.calls += 1
            return {"organic_results": [], "search_metadata": {"status": "Success"}}

    s = SerpApiSearcher(cache_dir=tmp_path, client=Client())
    p = params_for("google", "same question")
    s.search(p)
    s.search(p)
    assert (Client.calls, s.calls, s.hits) == (1, 1, 1)


def test_claude_answerer_request_shape(raw):
    from types import SimpleNamespace

    from footnote.llm import ClaudeAnswerer

    sent = {}

    class Messages:
        def create(self, **kw):
            sent.update(kw)
            block = SimpleNamespace(type="tool_use", input={"claims": [{"text": "x", "source_ids": ["S1"]}]})
            return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=10, output_tokens=5))

    a = ClaudeAnswerer(model="test-model", client=SimpleNamespace(messages=Messages()))
    out = a.answer("q?", normalise(raw))
    assert out == {"claims": [{"text": "x", "source_ids": ["S1"]}], "gaps": []}
    assert sent["tool_choice"] == {"type": "tool", "name": "submit_answer"}
    assert "source_ids" in sent["tools"][0]["input_schema"]["properties"]["claims"]["items"]["required"]
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    a.answer("q?", None)
    assert "source_ids" not in sent["tools"][0]["input_schema"]["properties"]["claims"]["items"]["properties"]
    assert a.usage == {"input_tokens": 20, "output_tokens": 10}
