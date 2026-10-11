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
