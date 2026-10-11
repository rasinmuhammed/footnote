"""The only place a language model is used: turning sources into short, individually cited claims."""

from __future__ import annotations

import os
from typing import Protocol

from .sources import Source

SYSTEM = """You answer questions using only the numbered search results you are given.

Rules:
- Break the answer into short, atomic claims: one fact per claim, one sentence each.
- Every claim must cite the id(s) of the result(s) that state it, e.g. ["S2"]. Cite only what you actually used.
- Copy numbers, dates and names exactly as they appear in the results. Never calculate, round or convert.
- If results disagree, say so in a claim and cite both sides.
- For time-sensitive facts, include the date the result gives.
- If the results don't answer part of the question, list that part under "gaps" instead of guessing.
- Return between 2 and 8 claims."""

CLOSED_BOOK = """Answer the question from your own knowledge as short, atomic claims, one fact per sentence.
Return between 2 and 8 claims. List anything you are unsure about under "gaps"."""


def _tool(cited: bool) -> dict:
    claim = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    if cited:
        claim["properties"]["source_ids"] = {"type": "array", "items": {"type": "string"}, "minItems": 1}
        claim["required"].append("source_ids")
    return {
        "name": "submit_answer",
        "description": "Submit the final answer as a list of claims.",
        "input_schema": {
            "type": "object",
            "properties": {"claims": {"type": "array", "items": claim, "minItems": 1},
                           "gaps": {"type": "array", "items": {"type": "string"}}},
            "required": ["claims"],
        },
    }


def format_sources(sources: list[Source]) -> str:
    lines = []
    for s in sources:
        meta = ", ".join(x for x in (s.engine, s.date) if x)
        lines.append(f"[{s.id}] ({meta}) {s.title}\n{s.text}\n{s.url}")
    return "\n\n".join(lines)


class Answerer(Protocol):
    def answer(self, question: str, sources: list[Source] | None) -> dict: ...


class ClaudeAnswerer:
    def __init__(self, model: str | None = None, client=None, max_tokens: int = 1500):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model or os.environ.get("FOOTNOTE_MODEL", "claude-sonnet-5-5")
        self.max_tokens = max_tokens
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def answer(self, question: str, sources: list[Source] | None) -> dict:
        cited = sources is not None
        system = SYSTEM if cited else CLOSED_BOOK
        content = f"Question: {question}"
        if cited:
            content = f"Search results:\n\n{format_sources(sources)}\n\n{content}"
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[_tool(cited)],
            tool_choice={"type": "tool", "name": "submit_answer"},
            messages=[{"role": "user", "content": content}],
        )
        for k in self.usage:
            self.usage[k] += getattr(resp.usage, k, 0) or 0
        block = next(b for b in resp.content if b.type == "tool_use")
        data = dict(block.input)
        data.setdefault("gaps", [])
        return data
