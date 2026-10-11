"""Check whether the numbers in a Google AI Mode answer appear on the pages it cites.

Usage: SERPAPI_API_KEY=... python check_ai_mode.py "What is the latest stable release of Rust?"
Needs: pip install serpapi httpx trafilatura
"""

import os
import re
import sys

import httpx
import trafilatura
from serpapi import Client

# A number such as 3.75, 1,463,900,000 or 1.99.0, but not the 1 in "H-1B"
NUMBER = re.compile(r"(?<![\w.-])\d[\d,]*(?:\.\d+)*(?!\w)")
# Answers often end by offering to do more: "If you'd like, I can…"
OFFER = re.compile(r"(?:if you(?:['’]d| would)? (?:like|want)|would you like)\b", re.I)
http = httpx.Client(headers={"User-Agent": "ai-mode-checker/0.1"}, timeout=20, follow_redirects=True)


def figures(text):
    """Numbers worth checking, commas dropped. Single digits and bare years are on almost every page, so skip them."""
    numbers = {n.strip(",").replace(",", "") for n in NUMBER.findall(text)}
    return {n for n in numbers if len(n) > 1 and not re.fullmatch(r"(19|20)\d\d", n)}


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


if __name__ == "__main__":
    client = Client(api_key=os.environ["SERPAPI_API_KEY"])
    check(client.search(engine="google_ai_mode", q=sys.argv[1]))
