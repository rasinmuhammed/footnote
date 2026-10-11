import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("label", Path(__file__).parents[1] / "evals" / "label.py")
label = importlib.util.module_from_spec(spec)
spec.loader.exec_module(label)


def test_labels_save_after_each_answer_go_back_and_resume(tmp_path):
    source = {"id": "S1", "engine": "google", "title": "t", "url": "u", "text": "x", "date": None, "position": 1}
    rows = [{"id": f"q{i}", "question": f"Q{i}?", "grounded": {"sources": [source], "claims": [
        {"claim": f"c{i}.{j}", "source_ids": ["S1"], "status": "supported"} for j in range(3)]}} for i in range(10)]
    results, out = tmp_path / "results.json", tmp_path / "labels_human.json"
    results.write_text(json.dumps({"summary": {"model": "m"}, "rows": rows}))

    def run(*answers):
        replies = iter(answers)
        label.main(results, out, ask=lambda _: next(replies))
        return json.loads(out.read_text())

    # label, go back, keep it with Enter, label the next, quit before the third
    data = run("b", "says so", "p", "", "n", "", "q")
    assert data["labeller"] == "human" and data["seed"] == label.SEED
    assert [v["label"] for v in data["verdicts"][:3]] == ["backed", "not_backed", None]
    assert data["verdicts"][0]["reason"] == "says so"
    assert run("c", "", "q")["verdicts"][2]["label"] == "close_call"  # resumes at the first unlabelled verdict
