import importlib.util
import inspect
import json
from pathlib import Path

import pytest

pytest.importorskip("trafilatura")  # the tutorial's own dependency, installed with the audit extra
spec = importlib.util.spec_from_file_location("tutorial", Path(__file__).parents[1] / "tutorial" / "check_ai_mode.py")
tutorial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tutorial)


def _live(name):
    return json.loads((Path(__file__).parent / "fixtures" / "live" / f"google_ai_mode_{name}.json").read_text())


def test_figures_skip_single_digits_years_and_names():
    assert tutorial.figures("In 2026, 1 H-1B fee rose to $100,000, or 3.75%; Rust 1.99.0 is out.") == \
        {"100000", "3.75", "1.99.0"}


def test_statements_skip_offers_and_their_lists():
    assert [t for t, _ in tutorial.statements(_live("python"))] == [
        "The latest stable release of Python is Python 3.15.0, which was released on October 9, 2026.",
        "You can download the newest files and check release details directly from the Python Downloads Page."]
    fed = list(tutorial.statements(_live("fed")))
    assert len(fed) == 8 and fed[4][1] == [0, 1]


def test_check_marks_found_missing_and_unread(capsys):
    minutes = ("the next meeting would be held on October 27–28, 2026. "
               "Primary credit rate of 4.0 percent, September 17")
    tutorial.check(_live("fed"), read=lambda url: minutes if "minutes" in url else None)
    out = capsys.readouterr().out
    assert "Vote Tally: Approved by a unanimous 12–0 vote. (no citation)" in out
    assert "✓ 27   ✓ 28" in out  # in the minutes, though the statement page couldn't be read
    assert "? 16" in out  # cites only the unread statement page


def test_tutorial_text_and_notebook_show_the_script_as_it_is():
    here = Path(__file__).parents[1] / "tutorial"
    readme = (here / "README.md").read_text()
    notebook = "".join("".join(c["source"]) for c in json.loads((here / "check_ai_mode.ipynb").read_text())["cells"])
    for fn in (tutorial.figures, tutorial.statements, tutorial.page_text, tutorial.check):
        assert inspect.getsource(fn).rstrip() in readme and inspect.getsource(fn).rstrip() in notebook
