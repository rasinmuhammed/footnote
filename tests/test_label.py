import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("label", Path(__file__).parents[1] / "evals" / "label.py")
label = importlib.util.module_from_spec(spec)
spec.loader.exec_module(label)


def _runs(tmp_path):
    source = {"id": "S1", "engine": "google", "title": "t", "url": "u", "text": "x", "date": None, "position": 1}
    for k, name in enumerate(label.BATCHES[2][0]):
        rows = [{"id": f"q{i}", "question": f"Q{i}?", "grounded": {"sources": [source], "claims": [
            {"claim": f"c{k}.{i}.{j}", "source_ids": ["S1"], "status": "supported"} for j in range(3)]}}
            for i in range(10)]
        (tmp_path / name).write_text(json.dumps({"summary": {"model": f"m{k}"}, "rows": rows}))
    return tmp_path


def test_labels_save_after_each_answer_go_back_and_resume(tmp_path, capsys):
    runs = _runs(tmp_path)

    def run(*answers):
        replies = iter(answers)
        label.main(1, runs, tmp_path, ask=lambda _: next(replies))
        return json.loads((tmp_path / "labels_human.json").read_text())

    # label, go back, keep it with Enter, label the next, quit before the third
    data = run("b", "says so", "p", "", "n", "", "q")
    assert data["labeller"] == "human" and data["seed"] == label.BATCHES[1][1]
    assert [v["label"] for v in data["verdicts"][:3]] == ["backed", "not_backed", None]
    assert data["verdicts"][0]["reason"] == "says so"
    assert run("c", "", "q")["verdicts"][2]["label"] == "close_call"  # resumes at the first unlabelled verdict
    assert "Checker:  supported" in capsys.readouterr().out


def test_second_batch_is_new_verdicts_from_every_run_labelled_blind(tmp_path, capsys):
    runs = _runs(tmp_path)
    first, second = label.sample(1, runs), label.sample(2, runs)
    assert len(second) == label.N and not {v["claim"] for v in first} & {v["claim"] for v in second}
    assert len({v["model"] for v in second}) > 1
    assert second == label.sample(2, runs)  # seeded
    label.main(2, runs, tmp_path, ask=lambda _: "q")
    assert not (tmp_path / "labels_human_2.json").exists()  # nothing saved until an answer is given
    replies = iter(["b", "", "q"])
    label.main(2, runs, tmp_path, ask=lambda _: next(replies))
    data = json.loads((tmp_path / "labels_human_2.json").read_text())
    assert data["verdicts"][0]["label"] == "backed" and not data["checker_shown"]
    assert not any("checker" in v for v in data["verdicts"])  # neither saved
    assert "Checker" not in capsys.readouterr().out  # nor shown


def test_flags_sample_only_cited_flags_and_never_save_their_kind(tmp_path):
    spec = importlib.util.spec_from_file_location("label_flags", Path(__file__).parents[1] / "evals" / "label_flags.py")
    flags = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(flags)
    mention = lambda status: {"written": status, "status": status}  # noqa: E731
    statements = [{"text": "cited", "refs": [0], "mentions": [mention(s) for s in ("not_found", "metadata_only",
                                                                                    "literal", "converted")]},
                  {"text": "uncited", "refs": [], "mentions": [mention("uncited")]}]
    rows = [{"id": f"a{i}", "q": f"Q{i}?", "audit": {"statements": statements,
                                                     "references": [{"index": 0, "title": "t", "url": "u"}]}}
            for i in range(20)] + [{"id": "a99", "q": "Q?", "error": "SerpApi error"}]
    results = tmp_path / "results.json"
    results.write_text(json.dumps({"rows": rows}))
    sample = flags.sample(results)
    assert len(sample) == 40 and sample == flags.sample(results)  # every flag, in a seeded order
    assert {f["figure"] for f in sample} == {"not_found", "metadata_only"}
    replies = iter(["n", "gone", "q"])
    flags.main(results, tmp_path / "labels.json", ask=lambda _: next(replies))
    data = json.loads((tmp_path / "labels.json").read_text())
    assert data["flags"][0]["label"] == "not_stated" and data["flags"][1]["label"] is None
    assert not any("status" in f for f in data["flags"])
