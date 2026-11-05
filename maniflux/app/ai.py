"""Fills a saved note's type and description through OpenRouter, with the same instructions the Obsidian scripts used."""

import json
import re

import httpx

from .kinds import TYPES

TYPE_INSTRUCTION = (
    "Classify this page as exactly one of: video, article, paper, podcast, music, thread, book, other. "
    "Reply with only that word."
)
DESCRIPTION_INSTRUCTION = "A 2-3 sentence plain summary of what this page covers."
# Same instruction as Link Ingest
AUTHORS_INSTRUCTION = "array of the people who made it (use the organization if no person is credited); [] if unknown"
# The Obsidian scripts sent at most this much page text
TEXT_LIMIT = 20000


class AIError(Exception):
    """The summary couldn't be written."""


class Summarizer:
    def __init__(self, api_key: str, model: str, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self.model = model
        self.client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            headers={"Authorization": f"Bearer {api_key}", "X-Title": "Maniflux"},
            timeout=httpx.Timeout(60.0),
            transport=transport,
        )

    async def close(self):
        await self.client.aclose()

    async def chat(self, prompt: str, model: str | None = None, max_tokens: int = 1000,
                   timeout: float = 60.0) -> str:
        """One prompt in, the model's text out."""
        if not self.api_key:
            raise AIError("no OPENROUTER_API_KEY in .env")
        try:
            r = await self.client.post("/chat/completions", timeout=timeout, json={
                "model": model or self.model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            })
        except httpx.HTTPError as e:
            raise AIError(f"can't reach OpenRouter ({type(e).__name__})") from e
        if r.status_code != 200:
            try:
                msg = r.json().get("error", {}).get("message", "")
            except ValueError:
                msg = r.text[:200]
            raise AIError(f"OpenRouter {r.status_code}: {msg}")
        content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
        if isinstance(content, list):
            content = "".join(c.get("text", "") for c in content)
        return content

    async def fill(self, props: dict, text: str, need_type: bool, need_authors: bool = False,
                   need_description: bool = True) -> dict:
        """Returns {"description": …} if need_description, {"type": …} if need_type and {"authors": […]} if need_authors."""
        if not self.api_key:
            raise AIError("no OPENROUTER_API_KEY in .env")
        keys = {"description": DESCRIPTION_INSTRUCTION} if need_description else {}
        if need_type:
            keys = {"type": TYPE_INSTRUCTION, **keys}
        if need_authors:
            keys["authors"] = AUTHORS_INSTRUCTION
        known = [f"{k}: {str(v)[:300]}" for k, v in props.items() if v]
        prompt = "\n".join([
            "You are filling in properties of a note about a web page.",
            "Reply with only a JSON object with exactly these keys, each answered as its instruction says:",
            *[f'- "{k}": {v}' for k, v in keys.items()],
            "",
            'Base answers only on the information below. If something can\'t be told from it, use "".',
            "Describe the piece itself; don't mention what text was or wasn't available.",
            "",
            "Note properties:",
            *(known or ["(none)"]),
            "",
            "Page text:",
            re.sub(r"\s+", " ", text).strip()[:TEXT_LIMIT] or "(none saved)",
        ])
        content = await self.chat(prompt, max_tokens=1000)
        m = re.search(r"\{[\s\S]*\}", content)
        if not m:
            raise AIError("the model didn't return JSON")
        try:
            answers = json.loads(m.group(0))
        except ValueError as e:
            raise AIError("the model returned broken JSON") from e

        out = {"description": str(answers.get("description") or "").strip()} if need_description else {}
        if need_type:
            t = str(answers.get("type") or "").strip().lower()
            t = "article" if t in ("essay", "blog-post") else t
            out["type"] = t if t in TYPES else "other"
        if need_authors:
            got = answers.get("authors") or []
            got = [got] if isinstance(got, str) else got
            out["authors"] = [str(a).strip() for a in got if str(a).strip()][:10]
        return out
