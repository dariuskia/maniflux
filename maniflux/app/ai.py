"""OpenRouter calls (for digests). Saved notes are worked out by the library service now."""

import httpx


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
