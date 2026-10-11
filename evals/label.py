"""Hand-label the 20 sampled verdicts, one at a time, to measure how often the checker is right.

Each verdict shows the claim and everything the checker saw. Answers are saved to evals/labels_human.json after
every one, so you can go back, quit and resume.

Usage: python evals/label.py
"""

from __future__ import annotations

import json
import random
import shutil
import textwrap
from pathlib import Path

HERE = Path(__file__).parent
RESULTS = HERE / "results" / "20261011-135840.json"  # the gpt-oss-20b run the sample was drawn from
SEED, N = 20261011, 20
RULE = ("A claim counts as backed only if the cited title, snippet and date together state everything it says. "
        "A result's date shows when something was reported, not when it happened.")
LABELS = {"b": "backed", "n": "not_backed", "c": "close_call"}


def sample(results: dict) -> list[dict]:
    """The same verdicts in the same order as the first labelling round: every grounded claim, then a seeded draw."""
    pool = []
    for row in results["rows"]:
        by_id = {s["id"]: s for s in row["grounded"]["sources"]}
        for v in row["grounded"]["claims"]:
            pool.append({"question_id": row["id"], "question": row["question"], "claim": v["claim"],
                         "checker": v["status"],
                         "cited": [{k: by_id[i][k] for k in ("id", "engine", "title", "url", "date", "text")}
                                   for i in v["source_ids"] if i in by_id]})
    return random.Random(SEED).sample(pool, N)


def load(out: Path, verdicts: list[dict], results_name: str, model: str) -> dict:
    if out.exists():
        data = json.loads(out.read_text())
        if [v["claim"] for v in data["verdicts"]] != [v["claim"] for v in verdicts]:
            raise SystemExit(f"{out} holds a different sample; move it aside to start over.")
        return data
    return {"results": results_name, "model": model, "seed": SEED, "labeller": "human", "rule": RULE,
            "verdicts": [{"n": n, **v, "label": None, "reason": ""} for n, v in enumerate(verdicts, 1)]}


def save(out: Path, data: dict) -> None:
    tmp = out.with_suffix(".tmp")  # write then rename, so quitting mid-write can't corrupt earlier answers
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(out)


def show(v: dict) -> None:
    width = min(shutil.get_terminal_size().columns, 110)

    def line(label: str, text: str) -> str:
        return textwrap.fill(text, width, initial_indent=label, subsequent_indent=" " * len(label))

    print("\n" + "─" * width)
    print(f"Verdict {v['n']} of {N} ({v['question_id']})")
    print(line("Question: ", v["question"]))
    print()
    print(line("Claim:    ", v["claim"]))
    print(line("Checker:  ", v["checker"]))
    print("\nCited sources")
    for s in v["cited"]:
        note = "  (news has no snippet: this is the headline and publisher)" if s["engine"] == "google_news" else ""
        print(f"  [{s['id']}] via {s['engine']}")
        print(line("    Title:   ", s["title"]))
        print(line("    Snippet: ", s["text"] + note))
        print(line("    Date:    ", s["date"] or "none given"))
        print(f"    URL:     {s['url'] or 'none'}")
    print("\n" + line("Rule: ", RULE))
    if v["label"]:
        print(f"\nYour answer: {v['label'].replace('_', ' ')}" + (f" ({v['reason']})" if v["reason"] else ""))


def main(results_path: Path = RESULTS, out: Path = HERE / "labels_human.json", ask=input) -> None:
    results = json.loads(results_path.read_text())
    data = load(out, sample(results), results_path.name, results["summary"]["model"])
    items = data["verdicts"]
    i = next((k for k, v in enumerate(items) if not v["label"]), N)
    print(f"Labelling {N} verdicts. Each answer is saved to {out} as soon as you give it.")
    while i < N:
        v = items[i]
        show(v)
        keep = " Enter = keep your answer," if v["label"] else ""
        try:
            choice = ask(f"\n[b] backed  [n] not backed  [c] close call  [p] previous  [q] quit{keep} > ")
            choice = choice.strip().lower()
            if choice == "" and v["label"]:
                i += 1
                continue
            if choice == "q":
                break
            if choice == "p":
                i = max(0, i - 1)
                continue
            if choice not in LABELS:
                print("Type b, n, c, p or q.")
                continue
            reason = ask("One-line reason (optional, Enter to skip) > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        v["label"], v["reason"] = LABELS[choice], reason
        save(out, data)
        i += 1
    done = sum(bool(v["label"]) for v in items)
    print(f"\n{done} of {N} labelled" + (f", saved in {out}." if done else ".")
          + ("" if done == N else " Run it again to resume."))


if __name__ == "__main__":
    main()
