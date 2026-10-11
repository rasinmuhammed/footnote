"""Hand-label a batch of 20 sampled verdicts, one at a time, to measure how often the checker is right.

Each verdict shows the claim and everything the checker saw. Answers are saved after every one, so you can go back,
quit and resume. Batch 1 (evals/labels_human.json) picked the checker's fixes; batch 2 (evals/labels_human_2.json)
is 20 new verdicts drawn after them, with none from batch 1, as an independent check.

Usage: python evals/label.py [--batch 2]
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import textwrap
from pathlib import Path

HERE = Path(__file__).parent
N = 20
BATCHES = {  # batch: (saved runs to draw from, seed, output file)
    1: (["20261011-135840.json"], 20261011, "labels_human.json"),  # gpt-oss-20b
    2: (["20261011-133041.json", "20261011-134538.json", "20261011-135840.json"],  # gpt-oss-120b, qwen, gpt-oss-20b
        20261012, "labels_human_2.json"),
}
RULE = ("A claim counts as backed only if the cited title, snippet and date together state everything it says. "
        "A result's date shows when something was reported, not when it happened.")
LABELS = {"b": "backed", "n": "not_backed", "c": "close_call"}


def sample(batch: int, results_dir: Path) -> list[dict]:
    """Every grounded claim in the batch's runs, minus earlier batches' claims, then a seeded draw.

    Batch 1 comes out exactly as it did in the first labelling round."""
    files, seed, _ = BATCHES[batch]
    earlier = {v["claim"] for b in BATCHES if b < batch for v in sample(b, results_dir)}
    pool = []
    for name in files:
        results = json.loads((results_dir / name).read_text())
        for row in results["rows"]:
            by_id = {s["id"]: s for s in row["grounded"]["sources"]}
            for v in row["grounded"]["claims"]:
                if v["claim"] in earlier:
                    continue
                pool.append({"question_id": row["id"], "question": row["question"], "claim": v["claim"],
                             "checker": v["status"], "model": results["summary"]["model"], "results": name,
                             "cited": [{k: by_id[i][k] for k in ("id", "engine", "title", "url", "date", "text")}
                                       for i in v["source_ids"] if i in by_id]})
    return random.Random(seed).sample(pool, N)


def load(out: Path, verdicts: list[dict], batch: int) -> dict:
    if out.exists():
        data = json.loads(out.read_text())
        if [v["claim"] for v in data["verdicts"]] != [v["claim"] for v in verdicts]:
            raise SystemExit(f"{out} holds a different sample; move it aside to start over.")
        return data
    files, seed, _ = BATCHES[batch]
    return {"batch": batch, "results": files, "seed": seed, "labeller": "human", "rule": RULE,
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


def main(batch: int = 1, results_dir: Path = HERE / "results", out_dir: Path = HERE, ask=input) -> None:
    out = out_dir / BATCHES[batch][2]
    data = load(out, sample(batch, results_dir), batch)
    items = data["verdicts"]
    i = next((k for k, v in enumerate(items) if not v["label"]), N)
    print(f"Labelling batch {batch}, {N} verdicts. Each answer is saved to {out} as soon as you give it.")
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
    ap = argparse.ArgumentParser(description="Hand-label sampled verdicts.")
    ap.add_argument("--batch", type=int, choices=sorted(BATCHES), default=1)
    main(ap.parse_args().batch)
