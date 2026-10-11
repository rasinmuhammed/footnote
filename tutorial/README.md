# Check Google AI Mode's citations with Python

Google's AI Mode answers with citations. This tutorial checks them: for each statement in an answer, it reads the
pages the statement cites and looks for every number it states. The script is about 70 lines of Python and uses one
SerpApi search per question. There's no model involved.

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/rasinmuhammed/footnote/blob/main/tutorial/check_ai_mode.ipynb)
The full script is [`check_ai_mode.py`](check_ai_mode.py).

## What you need

- Python 3.10 or later.
- A SerpApi key. The [free plan](https://serpapi.com/pricing) includes 250 searches a month, and each question
  uses one.
- Three packages: `serpapi` for the search, `httpx` to fetch pages, and `trafilatura` to pull the main text out of
  them.

```bash
pip install serpapi httpx trafilatura
export SERPAPI_API_KEY=your_key
```

## Step 1: Ask AI Mode through SerpApi

The [Google AI Mode API](https://serpapi.com/google-ai-mode-api) returns the answer as `text_blocks`, plus a list of
`references`. Each block, or each item in a list block, carries the indexes of the references it cites:

```python
import os

from serpapi import Client

client = Client(api_key=os.environ["SERPAPI_API_KEY"])
answer = client.search(engine="google_ai_mode",
                       q="What did the latest US Federal Reserve meeting decide about interest rates?")
```

Part of a real answer from 11 October 2026, trimmed:

```json
"text_blocks": [
  {"type": "list", "list": [
    {"snippet": "Vote Tally: Approved by a unanimous 12–0 vote."},
    {"snippet": "Primary Credit Rate: Approved a 1/4 percentage point increase to 4.0%, effective September 17, 2026.",
     "reference_indexes": [0, 1]}
  ]}
],
"references": [
  {"index": 0, "title": "Federal Reserve issues FOMC statement",
   "link": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm",
   "snippet": "The Committee decided to raise the target range for the federal funds rate by 1/4 percentage point…",
   "source": "Federal Reserve (.gov)"}
]
```

The first item cites nothing. The second cites references 0 and 1.

## Step 2: Split the answer into statements

Walk the blocks and yield each paragraph and list item with its citations. Answers often end with an offer ("If
you'd like, I can…"), sometimes with a list of options under it. Those aren't claims, so skip them:

```python
def statements(answer):
    """Each paragraph and list item, with the indexes of the references it cites. Offers and their lists are skipped."""
    offer = False
    for block in answer.get("text_blocks", []):
        if block["type"] == "paragraph":
            offer = bool(OFFER.match(block["snippet"]))
            if not offer:
                yield block["snippet"], block.get("reference_indexes", [])
        elif block["type"] == "list" and not offer:
            for item in block["list"]:
                yield item["snippet"], item.get("reference_indexes", [])
```

The offer pattern sits at the top of the script:

```python
OFFER = re.compile(r"(?:if you(?:['’]d| would)? (?:like|want)|would you like)\b", re.I)
```

## Step 3: Pick out the figures

A regular expression finds the numbers. Commas are dropped so "1,200" matches "1200". Single digits and bare years
appear on almost every page, so checking them proves nothing:

```python
NUMBER = re.compile(r"(?<![\w.-])\d[\d,]*(?:\.\d+)*(?!\w)")


def figures(text):
    """Numbers worth checking, commas dropped. Single digits and bare years are on almost every page, so skip them."""
    numbers = {n.strip(",").replace(",", "") for n in NUMBER.findall(text)}
    return {n for n in numbers if len(n) > 1 and not re.fullmatch(r"(19|20)\d\d", n)}
```

The lookbehind keeps names out: the 1 in "H-1B" isn't a figure. Version numbers like 1.99.0 stay whole.

## Step 4: Read the cited pages

Fetch each page once and keep its main text. Return `None` when a page can't be read, rather than an empty string,
because "not on the page" and "couldn't read the page" are different results:

```python
http = httpx.Client(headers={"User-Agent": "ai-mode-checker/0.1"}, timeout=20, follow_redirects=True)


def page_text(url):
    """The page's main text, or None if it can't be read: blocked, not HTML, or no text found."""
    try:
        response = http.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    if "html" not in response.headers.get("content-type", "html"):
        return None
    return trafilatura.extract(response.content, favor_recall=True)
```

The client identifies itself. Some sites refuse scripts, and the script reports those pages as unread rather
than disguising its requests.

## Step 5: Compare

For each statement with figures, gather the cited references' titles, snippets and page text, and look for each
figure in them:

```python
def check(answer, read=page_text):
    references = {r["index"]: r for r in answer.get("references", [])}
    pages = {}
    for text, cited in statements(answer):
        numbers = figures(text)
        if not numbers:
            continue
        print(text, "".join(f"[{i + 1}]" for i in cited) or "(no citation)")
        if not cited:
            continue
        sources, unread = [], False
        for i in cited:
            ref = references[i]
            if ref["link"] not in pages:
                pages[ref["link"]] = read(ref["link"])
            unread |= pages[ref["link"]] is None
            sources.append(f"{ref['title']} {ref.get('snippet', '')} {pages[ref['link']] or ''}")
        found = figures(" ".join(sources))
        # ✓ on a cited page, ✗ on none of them, ? not on the pages that could be read
        print("    " + "   ".join(f"{'✓' if n in found else '?' if unread else '✗'} {n}" for n in sorted(numbers)))
```

## Run it

```bash
python check_ai_mode.py "What is the latest stable release of Rust?"
```

```
The latest stable release of Rust is Rust 1.99.0, which was released on October 1, 2026. [1]
    ✓ 1.99.0
Beta: Rust 1.100.0 is currently in beta. (no citation)
Nightly: Rust 1.01.0 represents the active nightly development channel. [4]
    ✗ 1.01.0
```

Rust 1.99.0 is on the cited page, and the beta's version number cites nothing. The page cited for the nightly,
releases.rs, doesn't mention a Rust 1.01.0, and no such version exists.

`?` means the figure wasn't on any page that could be read, but at least one cited page couldn't be:

```
According to the U.S. Bureau of Labor Statistics' report for September 2026, the U.S. unemployment rate stood at 4.2%. [1]
    ✓ 4.2
Total Unemployed: 7.1 million people. (no citation)
Recent Range: The unemployment rate has remained relatively steady, holding in a narrow range between 4.1% and 4.3% since March. (no citation)
Labor Force Participation Rate: 61.8%. (no citation)
Employment-Population Ratio: 59.2%. [2][3]
    ? 59.2
```

Both pages cited for 59.2% are on bls.gov, which refuses scripts.

## What this version leaves out

It matches numbers as written, so "3.75%" against "3-3/4 percent" or "1.48 billion" against "1,480,243,044" comes
out ✗. It treats dates as plain numbers. It reads HTML only, so a PDF counts as unread. And a number found on a page
isn't proof the page supports the statement: long pages contain many numbers.

[`footnote audit`](../README.md#auditing-google-ai-mode) handles conversions, rounding and dates. Its study of 80
answers found that 38% of statements with a figure cite nothing. The audit flagged 39 figures, and a hand check
found 26 of them on the cited page, mostly on pages a script can't read. That's why `?` is kept apart from ✗, and
either one is a reason to go and look.
