"""Ausschließlich lesender HTTP-Zugriff mit Wiederholungsversuchen und Größenlimit."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

log = logging.getLogger(__name__)


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None, transient: bool = False):
        super().__init__(message)
        self.status = status
        self.transient = transient


@dataclass
class FetchResult:
    url: str
    status: int
    content: bytes
    etag: str | None = None
    last_modified: str | None = None

    @property
    def not_modified(self) -> bool:
        return self.status == 304


Sleep = Callable[[float], Awaitable[None]]


class HttpFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_retries: int = 3,
        base_delay: float = 2.0,
        max_bytes: int = 3_000_000,
        sleep: Sleep = asyncio.sleep,
    ):
        self.client = client
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_bytes = max_bytes
        self.sleep = sleep

    @classmethod
    def create(cls, user_agent: str, timeout: float, **kw) -> "HttpFetcher":
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            max_redirects=5,
            headers={"User-Agent": user_agent, "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.8, */*;q=0.5"},
        )
        return cls(client, **kw)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def get(self, url: str, etag: str | None = None, last_modified: str | None = None) -> FetchResult:
        if not url.startswith("https://"):
            raise FetchError("Nur HTTPS-URLs werden abgerufen")
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        attempt = 0
        while True:
            try:
                return await self._get_once(url, headers)
            except FetchError as exc:
                if not exc.transient or attempt >= self.max_retries:
                    raise
            attempt += 1
            delay = self.base_delay * (2 ** (attempt - 1)) + random.uniform(0, self.base_delay / 2)
            log.info("Wiederhole Abruf %s (Versuch %d) in %.1fs", url, attempt + 1, delay)
            await self.sleep(delay)

    async def _get_once(self, url: str, headers: dict) -> FetchResult:
        try:
            async with self.client.stream("GET", url, headers=headers) as resp:
                if not str(resp.url).startswith("https://"):
                    raise FetchError("Weiterleitung auf unverschlüsselte URL abgelehnt")
                if resp.status_code == 304:
                    return FetchResult(url, 304, b"", headers.get("If-None-Match"), headers.get("If-Modified-Since"))
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise FetchError(f"HTTP {resp.status_code}", resp.status_code, transient=True)
                if resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code}", resp.status_code, transient=False)
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise FetchError(f"Antwort größer als {self.max_bytes} Bytes", resp.status_code)
                    chunks.append(chunk)
                return FetchResult(
                    url=str(resp.url),
                    status=resp.status_code,
                    content=b"".join(chunks),
                    etag=resp.headers.get("ETag"),
                    last_modified=resp.headers.get("Last-Modified"),
                )
        except FetchError:
            raise
        except httpx.TransportError as exc:  # Timeout, Netzwerk, Proxy, Protokoll
            raise FetchError(f"Netzwerkfehler: {type(exc).__name__}", transient=True) from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"HTTP-Fehler: {type(exc).__name__}") from exc
