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


def test_search_errors_never_leak_api_key(tmp_path):
    import pytest

    class Client:
        def search(self, params):
            raise Exception("Max retries exceeded with url: /search?engine=google&q=x&api_key=SECRET123 (timeout)")

    with pytest.raises(RuntimeError) as e:
        SerpApiSearcher(cache_dir=tmp_path, client=Client()).search(params_for("google", "x"))
    assert "SECRET123" not in str(e.value) and "api_key=***" in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__


def _resp(args, prompt=10, completion=5):
    from types import SimpleNamespace as NS

    call = NS(function=NS(name="submit_answer", arguments=args))
    return NS(choices=[NS(message=NS(tool_calls=[call]))], usage=NS(prompt_tokens=prompt, completion_tokens=completion))


def test_groq_answerer_request_shape_and_retry(raw):
    from types import SimpleNamespace as NS

    from footnote.llm import GroqAnswerer

    sent, replies = [], [Exception("Error code: 400 - tool_use_failed"), _resp("not json"),
                          _resp(json.dumps({"claims": [{"text": "x", "source_ids": ["S1"]}]}))]

    def create(**kw):
        sent.append(kw)
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    a = GroqAnswerer(model="test-model", client=NS(chat=NS(completions=NS(create=create))))
    out = a.answer("q?", normalise(raw))
    assert out == {"claims": [{"text": "x", "source_ids": ["S1"]}], "gaps": []}
    assert len(sent) == 3  # one API error and one malformed reply were retried
    kw = sent[-1]
    assert kw["model"] == "test-model" and kw["temperature"] == 0
    assert kw["tool_choice"] == {"type": "function", "function": {"name": "submit_answer"}}
    item = kw["tools"][0]["function"]["parameters"]["properties"]["claims"]["items"]
    assert "source_ids" in item["required"]
    assert "[S1]" in kw["messages"][1]["content"]
    assert a.usage == {"input_tokens": 20, "output_tokens": 10}


def test_groq_answerer_closed_book_and_hard_errors():
    from types import SimpleNamespace as NS

    import pytest

    from footnote.llm import GroqAnswerer

    sent = []
    a = GroqAnswerer(model="m", client=NS(chat=NS(completions=NS(
        create=lambda **kw: sent.append(kw) or _resp(json.dumps({"claims": [{"text": "y"}]}))))))
    assert a.answer("q?", None)["claims"] == [{"text": "y"}]
    assert "source_ids" not in sent[0]["tools"][0]["function"]["parameters"]["properties"]["claims"]["items"]["properties"]

    def boom(**kw):
        raise Exception("401 invalid api key")

    with pytest.raises(Exception, match="invalid api key"):
        GroqAnswerer(model="m", client=NS(chat=NS(completions=NS(create=boom)))).answer("q?", None)
