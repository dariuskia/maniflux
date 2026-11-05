"""Thin client for the Miniflux REST API (checked against Miniflux 2.3.4, internal/api/api.go)."""

import httpx


class MinifluxError(Exception):
    """Miniflux couldn't be reached or answered with an error."""


class Miniflux:
    def __init__(self, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-Auth-Token": api_key},
            timeout=httpx.Timeout(15.0),
            transport=transport,
        )

    async def close(self):
        await self.client.aclose()

    async def _call(self, method: str, path: str, *, timeout: float | None = None, **kw):
        try:
            r = await self.client.request(method, path, timeout=timeout or httpx.USE_CLIENT_DEFAULT, **kw)
        except httpx.HTTPError as e:
            raise MinifluxError(f"Can't reach Miniflux at {self.base_url} ({type(e).__name__})") from e
        if r.status_code == 401:
            raise MinifluxError("Miniflux rejected the API key (check MINIFLUX_API_KEY in .env)")
        if r.status_code >= 400:
            msg = ""
            try:
                msg = r.json().get("error_message", "")
            except ValueError:
                pass
            raise MinifluxError(f"Miniflux answered {r.status_code} for {path}" + (f": {msg}" if msg else ""))
        return r.json() if r.content else None

    async def unread(self, limit: int) -> list[dict]:
        data = await self._call("GET", "/entries", params={"status": "unread", "limit": limit})
        return data["entries"] or []

    async def entries(self, **params) -> dict:
        """GET /v1/entries with any of its filters (status, changed_after, order, limit, offset…)."""
        return await self._call("GET", "/entries", params=params)

    async def archived(self, limit: int, offset: int = 0) -> tuple[list[dict], int]:
        """Read entries, most recently archived first."""
        params = {"status": "read", "order": "changed_at", "direction": "desc", "limit": limit, "offset": offset}
        data = await self._call("GET", "/entries", params=params)
        return data["entries"] or [], data["total"]

    async def entry(self, entry_id: int) -> dict:
        return await self._call("GET", f"/entries/{entry_id}")

    async def fetch_content(self, entry_id: int) -> dict:
        """Full text from the original page. update_content=true makes Miniflux keep it."""
        return await self._call(
            "GET", f"/entries/{entry_id}/fetch-content", params={"update_content": "true"}, timeout=40.0
        )

    async def set_status(self, entry_ids: list[int], status: str):
        await self._call("PUT", "/entries", json={"entry_ids": entry_ids, "status": status})

    async def feed_entries(self, feed_id: int, **params) -> list[dict]:
        data = await self._call("GET", f"/feeds/{feed_id}/entries", params=params)
        return data["entries"] or []

    async def refresh_feed(self, feed_id: int):
        await self._call("PUT", f"/feeds/{feed_id}/refresh")

    async def categories(self) -> list[dict]:
        return await self._call("GET", "/categories")

    async def subscribe(self, feed_url: str, category_id: int) -> int:
        data = await self._call("POST", "/feeds", json={"feed_url": feed_url, "category_id": category_id},
                                timeout=60.0)
        return data["feed_id"]

    async def feeds(self) -> list[dict]:
        return await self._call("GET", "/feeds")

    async def version(self) -> str:
        return (await self._call("GET", "/version"))["version"]
