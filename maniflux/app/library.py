"""Sending a saved item to the library service, which writes the finished note in the background."""

import httpx


class LibraryError(Exception):
    """The library service couldn't take the save."""


class Library:
    def __init__(self, url: str, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self.client = httpx.AsyncClient(base_url=url, headers={"Authorization": f"Bearer {token}"},
                                        timeout=httpx.Timeout(10.0), transport=transport)

    async def close(self):
        await self.client.aclose()

    async def save(self, job: dict) -> dict:
        """{"status": "queued"} or {"status": "exists", "note": name}."""
        try:
            r = await self.client.post("/save", json=job)
        except httpx.HTTPError as e:
            raise LibraryError(f"can't reach the library service ({type(e).__name__})") from e
        if r.status_code != 200:
            raise LibraryError(f"library service: {r.status_code}")
        return r.json()
