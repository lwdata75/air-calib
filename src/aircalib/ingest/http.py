"""A polite asynchronous HTTP client shared by every downloader.

Sensor.Community runs on volunteer-funded servers, so every request goes
through the same rules: an explicit User-Agent, a global concurrency cap,
and retries with exponential backoff (plus jitter) on transient failures.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from aircalib.config import Http
from aircalib.logging import get_logger

log = get_logger(__name__)

# 404 is a definitive answer (file absent), never retried.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class PoliteClient:
    """Thin wrapper around :class:`httpx.AsyncClient` with a semaphore and retries."""

    def __init__(self, client: httpx.AsyncClient, cfg: Http) -> None:
        self._client = client
        self._cfg = cfg
        self._semaphore = asyncio.Semaphore(cfg.max_concurrency)

    def backoff_s(self, attempt: int) -> float:
        """Exponential backoff with full jitter, capped at ``backoff_max_s``."""
        ceiling = min(self._cfg.backoff_max_s, self._cfg.backoff_base_s * 2**attempt)
        return random.uniform(ceiling / 2, ceiling)

    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        """GET ``url``; retry transient errors; return the final response (any status)."""
        last_error: Exception | None = None
        for attempt in range(self._cfg.max_retries + 1):
            try:
                async with self._semaphore:
                    response = await self._client.get(url, headers=headers)
                if response.status_code not in RETRYABLE_STATUS:
                    return response
                last_error = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )
            except (httpx.TransportError, httpx.RemoteProtocolError) as exc:
                last_error = exc
            if attempt < self._cfg.max_retries:
                wait = self.backoff_s(attempt)
                log.debug("retry %d for %s in %.1fs (%s)", attempt + 1, url, wait, last_error)
                await asyncio.sleep(wait)
        raise RuntimeError(f"giving up on {url} after {self._cfg.max_retries} retries") from last_error

    async def post_json(self, url: str, payload: dict) -> httpx.Response:
        async with self._semaphore:
            response = await self._client.post(url, json=payload)
        response.raise_for_status()
        return response


@asynccontextmanager
async def polite_client(cfg: Http) -> AsyncIterator[PoliteClient]:
    """Open a :class:`PoliteClient` (HTTP/1.1: the archive breaks HTTP/2 framing)."""
    limits = httpx.Limits(max_connections=cfg.max_concurrency)
    async with httpx.AsyncClient(
        headers={"User-Agent": cfg.user_agent},
        timeout=httpx.Timeout(cfg.timeout_s),
        limits=limits,
        follow_redirects=True,
        http2=False,
    ) as client:
        yield PoliteClient(client, cfg)
