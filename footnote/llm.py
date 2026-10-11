"""The only place a language model is used: turning sources into short, individually cited claims.

Runs on Groq (https://console.groq.com) through its OpenAI-compatible chat API with forced tool calling.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

from .sources import Source

DEFAULT_MODEL = "openai/gpt-oss-120b"

SYSTEM = """You answer questions using only the numbered search results you are given.

Rules:
- Break the answer into short, atomic claims: one fact per claim, one sentence each.
- Every claim must cite the id(s) of the result(s) that state it, e.g. ["S2"]. Cite only what you actually used.
- Copy numbers, dates and names exactly as they appear in the results. Never calculate, round or convert.
- If results disagree, say so in a claim and cite both sides.
- For time-sensitive facts, include the date the result gives.
- If the results don't answer part of the question, list that part under "gaps" instead of guessing.
- Return between 2 and 8 claims. Always respond by calling submit_answer."""

CLOSED_BOOK = """Answer the question from your own knowledge as short, atomic claims, one fact per sentence.
Return between 2 and 8 claims. List anything you are unsure about under "gaps".
Always respond by calling submit_answer."""


def tool_schema(cited: bool) -> dict:
    claim = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    if cited:
        claim["properties"]["source_ids"] = {"type": "array", "items": {"type": "string"}, "minItems": 1}
        claim["required"].append("source_ids")
    return {
        "type": "function",
        "function": {
            "name": "submit_answer",
            "description": "Submit the final answer as a list of claims.",
            "parameters": {
                "type": "object",
                "properties": {"claims": {"type": "array", "items": claim, "minItems": 1},
                               "gaps": {"type": "array", "items": {"type": "string"}}},
                "required": ["claims"],
            },
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


class GroqAnswerer:
    def __init__(self, model: str | None = None, client=None, max_tokens: int = 2000, retries: int = 2):
        if client is None:
            from groq import Groq

            if not os.environ.get("GROQ_API_KEY"):
                raise RuntimeError("Set GROQ_API_KEY (create one at https://console.groq.com/keys).")
            client = Groq()
        self.client = client
        self.model = model or os.environ.get("FOOTNOTE_MODEL", DEFAULT_MODEL)
        self.max_tokens, self.retries = max_tokens, retries
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def answer(self, question: str, sources: list[Source] | None) -> dict:
        cited = sources is not None
        user = f"Question: {question}"
        if cited:
            user = f"Search results:\n\n{format_sources(sources)}\n\n{user}"
        messages = [{"role": "system", "content": SYSTEM if cited else CLOSED_BOOK},
                    {"role": "user", "content": user}]

        last_error: Exception | None = None
        for _ in range(self.retries + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=[tool_schema(cited)],
                    tool_choice={"type": "function", "function": {"name": "submit_answer"}},
                    temperature=0,
                    max_completion_tokens=self.max_tokens,
                )
            except Exception as e:  # Groq returns 400 "tool_use_failed" when a model emits malformed arguments
                if "tool_use_failed" not in str(e):
                    raise
                last_error = e
                continue
            usage = getattr(resp, "usage", None)
            if usage:
                self.usage["input_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
                self.usage["output_tokens"] += getattr(usage, "completion_tokens", 0) or 0
            calls = resp.choices[0].message.tool_calls or []
            try:
                data = json.loads(calls[0].function.arguments)
            except (IndexError, json.JSONDecodeError) as e:
                last_error = e
                continue
            if data.get("claims"):
                data.setdefault("gaps", [])
                return data
            last_error = ValueError("model returned no claims")
        raise RuntimeError(f"{self.model} did not return a valid answer after {self.retries + 1} tries: {last_error}")


def list_models(client=None) -> list[str]:
    """Model ids currently served to this Groq key. Groq's catalogue changes, so check rather than assume."""
    if client is None:
        from groq import Groq

        client = Groq()
    return sorted(m.id for m in client.models.list().data)
