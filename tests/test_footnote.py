import json
from pathlib import Path

import pytest

from footnote import Footnote
from footnote.render import markdown, text
from footnote.router import plan
from footnote.search import SerpApiSearcher, params_for
from footnote.sources import normalise
from footnote.verify import check, check_against_pool, numbers


def test_normalise_flattens_dedupes_and_numbers(raw):
    sources = normalise(raw)
    assert [s.id for s in sources] == [f"S{i}" for i in range(1, len(sources) + 1)]
    urls = [s.url for s in sources]
    assert urls.count("https://en.wikipedia.org/wiki/Mount_Example") == 1  # answer box, KG, organic deduped
    assert "https://empty.example.com" not in urls  # empty snippet dropped
    assert "https://times.example.com/record" in urls  # clustered news stories are unpacked
    scholar = next(s for s in sources if s.engine == "google_scholar")
    assert "Cited by 42" in scholar.text and "2021" in scholar.text


def _live(name):  # trimmed real SerpApi responses
    return json.loads((Path(__file__).parent / "fixtures" / "live" / f"{name}.json").read_text())


def test_live_google_keeps_ai_overview_references_not_its_text():
    s = normalise({"google": _live("google_rbi")})
    texts = " ".join(x.text for x in s)
    assert "announced a hike in the key rep" in texts  # Times of India page the overview cites
    assert "after a 25 basis point hike announced" not in texts  # the overview's own generated paragraph
    assert [x.position for x in s] == [None, 1, None, 2, None, 3]  # references alternate with organic results
    token_only = {"ai_overview": {"page_token": "t"}, "organic_results": [{"title": "t", "link": "u", "snippet": "x"}]}
    assert [x.url for x in normalise({"google": token_only})] == ["u"]


def test_live_news_dates_are_unambiguous():
    s = normalise({"google_news": _live("google_news_rbi")})
    assert [x.date for x in s] == ["2026-08-05", "2026-10-07", "2026-08-05", "2026-10-07"]
    assert s[1].text == "RBI raises the Repo Rate by 25 bps to 5.5% in October, as expected (FXStreet)"


def test_live_scholar_year_and_citations():
    s = normalise({"google_scholar": _live("google_scholar_rag")})
    assert [x.date for x in s] == ["2021", "2025", "2024"]
    assert "Cited by 1635." in s[0].text and "aclanthology.org" in s[0].text


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


@pytest.mark.parametrize("claim", [
    "I do not have the specific outcome of that meeting.",
    "I don't know who won the race.",
    "I don’t know who won the race.",  # curly apostrophe, as models write it
    "I am unable to determine the current repo rate.",
    "The repo rate is unable to be confirmed; I'm unable to verify it.",
    "As of my knowledge cutoff, the repo rate was 6.5%.",
    "The winner of that Grand Prix was [driver name].",
])
def test_abstentions_are_not_answers(raw, claim):
    s = normalise(raw)
    assert check(claim, ["S99"], s).status == "abstain"  # checked before the citation
    assert check_against_pool(claim, s).status == "abstain"
    assert check_against_pool(claim, []).status == "abstain"


@pytest.mark.parametrize("claim", ["The lander was unable to touch down after a sensor fault.",
                                   "Mount Example is 4,321 metres tall [S1]."])
def test_claims_that_only_look_like_abstentions(raw, claim):
    assert check(claim, ["S1"], normalise(raw)).status != "abstain"


def test_dates_count_when_they_match_the_cited_source_date():
    s = normalise({"google_news": _live("google_news_rbi")})  # S2: Oct 7 hike to 5.5%, title has no date
    hike = next(x.id for x in s if x.date == "2026-10-07" and "5.5%" in x.text)
    for claim in ("On 7 October 2026, the RBI raised the repo rate by 25 bps to 5.5%.",
                  "On Oct. 7, 2026, the RBI raised the repo rate by 25 bps to 5.5%.",
                  "In October 2026 the RBI raised the repo rate to 5.5%.",
                  "On 2026‑10‑07 the RBI raised the repo rate by 25 bps to 5.5%."):  # non-breaking hyphens
        assert check(claim, [hike], s).status == "supported", claim
    wrong_day = check("On 8 October 2026, the RBI raised the repo rate to 5.5%.", [hike], s)
    assert wrong_day.missing_numbers == ["2026", "8"]  # the date disagrees as a whole
    assert check("In 2025, the RBI raised the repo rate to 5.5%.", [hike], s).missing_numbers == ["2025"]
    assert check("The RBI raised the repo rate 7 times to 5.5%.", [hike], s).missing_numbers == ["7"]


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
    assert r.timings["serpapi_s"] == 2.5 and r.timings["model_s"] is None  # SerpApi's reported time; fake model
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
    return NS(choices=[NS(message=NS(tool_calls=[call]))],
              usage=NS(prompt_tokens=prompt, completion_tokens=completion, queue_time=0.25, total_time=0.5))


def test_groq_answerer_request_shape_and_retry(raw):
    from types import SimpleNamespace as NS

    from footnote.llm import GroqAnswerer

    class Rejected(Exception):  # shaped like groq.BadRequestError
        body = {"error": {"code": "tool_use_failed", "message": "attempted to call tool 'open'",
                          "failed_generation": '{"name": "open", "arguments": {"cursor": 0, "id": 7}}'}}

    sent, replies = [], [Rejected("Error code: 400 - tool_use_failed"), _resp("not json"),
                          _resp(json.dumps({"claims": [{"text": "x", "source_ids": ["S1"]}]}))]

    def create(**kw):
        sent.append(kw)
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    a = GroqAnswerer(model="test-model", client=NS(chat=NS(completions=NS(create=create))))
    out = a.answer("q?", normalise(raw))
    assert out == {"claims": [{"text": "x", "source_ids": ["S1"]}], "gaps": [], "model_s": 0.75}  # Groq's own time
    assert len(sent) == 3  # one API error and one malformed reply were retried
    assert len(sent[0]["messages"]) == 2
    rejected, why = sent[1]["messages"][2:]  # the retry shows the model its rejected call and the reason
    assert rejected == {"role": "assistant", "content": Rejected.body["error"]["failed_generation"]}
    assert "attempted to call tool 'open'" in why["content"] and why["role"] == "user"
    kw = sent[-1]
    assert kw["model"] == "test-model" and kw["temperature"] == 0
    assert kw["tool_choice"] == {"type": "function", "function": {"name": "submit_answer"}}
    item = kw["tools"][0]["function"]["parameters"]["properties"]["claims"]["items"]
    assert "source_ids" in item["required"]
    assert "[S1]" in kw["messages"][1]["content"]
    assert "Today's date is 20" in kw["messages"][0]["content"]
    assert a.usage == {"input_tokens": 20, "output_tokens": 10}


def test_groq_answerer_gives_more_room_when_reasoning_ran_out():
    from types import SimpleNamespace as NS

    from footnote.llm import GroqAnswerer

    class Exhausted(Exception):  # what Groq returns when the cap ran out before the tool call
        body = {"error": {"code": "tool_use_failed", "failed_generation": "",
                          "message": "Tool choice is required, but model did not call a tool"}}

    sent, replies = [], [Exhausted("Error code: 400 - tool_use_failed"), _resp(json.dumps({"claims": [{"text": "y"}]}))]

    def create(**kw):
        sent.append(kw)
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    a = GroqAnswerer(model="m", client=NS(chat=NS(completions=NS(create=create))))
    assert a.answer("q?", None)["claims"] == [{"text": "y"}]
    assert [kw["max_completion_tokens"] for kw in sent] == [2000, 6000]
    assert sent[0]["messages"] == sent[1]["messages"]  # same request, more room; nothing to correct


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
