"""Hand-check a sample of the AI Mode audit's flags: is a flagged figure really missing from the pages it cites?

A flag is a figure in a cited statement that the audit found in none of the cited pages, or only as a page's own
date or the day of the search. Open the cited pages and judge from what they say now. Which kind of flag it is
isn't shown or saved, so every one is judged the same way. Answers are saved after each one; you can go back, quit
and resume.

Usage: python evals/label_flags.py
"""

from __future__ import annotations

import json
import random
import shutil
import textwrap
from pathlib import Path

from footnote.aimode import FLAGS

HERE = Path(__file__).parent
N, SEED = 30, 20261013
RULE = ("A figure counts as stated if a cited page gives the same value in any form (3.75%, 3-3/4 percent). "
        "A date counts only if the page's text gives it; a date the page shows as when it was published or "
        "updated doesn't count.")
LABELS = {"s": "stated", "r": "rounded", "n": "not_stated", "u": "cant_tell"}
PROMPT = ("\n[s] a cited page states it  [r] only rounded or approximate  [n] no cited page states it  "
          "[u] can't tell  [p] previous  [q] quit")


def sample(results: Path) -> list[dict]:
    pool = []
    for row in json.loads(results.read_text())["rows"]:
        if "audit" not in row:
            continue
        refs = {r["index"]: r for r in row["audit"]["references"]}
        for si, s in enumerate(row["audit"]["statements"]):
            for mi, m in enumerate(s["mentions"]):
                if s["refs"] and m["status"] in FLAGS:
                    pool.append({"question_id": row["id"], "question": row["q"], "statement": s["text"],
                                 "figure": m["written"], "at": [si, mi],
                                 "cited": [{"n": i + 1, "title": refs[i]["title"], "url": refs[i]["url"]}
                                           for i in s["refs"] if i in refs]})
    return random.Random(SEED).sample(pool, min(N, len(pool)))


def save(out: Path, data: dict) -> None:
    tmp = out.with_suffix(".tmp")  # write then rename, so quitting mid-write can't corrupt earlier answers
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(out)


def show(f: dict, total: int) -> None:
    width = min(shutil.get_terminal_size().columns, 110)

    def line(label: str, text: str) -> str:
        return textwrap.fill(text, width, initial_indent=label, subsequent_indent=" " * len(label))

    print("\n" + "─" * width)
    print(f"Flag {f['n']} of {total} ({f['question_id']})")
    print(line("Question:  ", f["question"]))
    print(line("Statement: ", f["statement"]))
    print(line("Figure:    ", f["figure"]))
    print("\nCited pages")
    for c in f["cited"]:
        print(line(f"  [{c['n']}] ", c["title"]))
        print(f"      {c['url']}")
    print("\n" + line("Rule: ", RULE))
    if f["label"]:
        print(f"\nYour answer: {f['label'].replace('_', ' ')}" + (f" ({f['reason']})" if f["reason"] else ""))


def main(results: Path = HERE / "aimode_results.json", out: Path = HERE / "labels_flags.json", ask=input) -> None:
    flags = sample(results)
    if out.exists():
        data = json.loads(out.read_text())
        if [(f["question_id"], f["at"]) for f in data["flags"]] != [(f["question_id"], f["at"]) for f in flags]:
            raise SystemExit(f"{out} holds a different sample; move it aside to start over.")
    else:
        data = {"results": results.name, "seed": SEED, "labeller": "human", "rule": RULE,
                "flags": [{"n": n, **f, "label": None, "reason": ""} for n, f in enumerate(flags, 1)]}
    items = data["flags"]
    i = next((k for k, f in enumerate(items) if not f["label"]), len(items))
    print(f"Checking {len(items)} flags. Each answer is saved to {out} as soon as you give it.")
    while i < len(items):
        f = items[i]
        show(f, len(items))
        try:
            choice = ask(PROMPT + (" Enter = keep your answer" if f["label"] else "") + " > ").strip().lower()
            if choice == "" and f["label"]:
                i += 1
                continue
            if choice == "q":
                break
            if choice == "p":
                i = max(0, i - 1)
                continue
            if choice not in LABELS:
                print("Type s, r, n, u, p or q.")
                continue
            reason = ask("One-line reason (optional, Enter to skip) > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        f["label"], f["reason"] = LABELS[choice], reason
        save(out, data)
        i += 1
    done = sum(bool(f["label"]) for f in items)
    print(f"\n{done} of {len(items)} checked" + (f", saved in {out}." if done else ".")
          + ("" if done == len(items) else " Run it again to resume."))


if __name__ == "__main__":
    main()
