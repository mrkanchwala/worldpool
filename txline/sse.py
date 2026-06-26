"""TxLINE SSE stream client — scores + odds, async, with reconnect."""
from __future__ import annotations
import asyncio
import json
import logging
import os
from typing import AsyncIterator, Callable, Awaitable

import httpx

from .auth import auth_headers, load_token, TOKEN_PATH
from .parser import ScoreEvent, OddsEvent, parse_score_event, parse_odds_event

logger = logging.getLogger(__name__)

RECONNECT_DELAY = 3  # seconds, doubles each attempt up to MAX_DELAY
MAX_RECONNECT_DELAY = 60


def _base_url() -> str:
    """Read at call time so .env / env changes always take effect (not frozen at import)."""
    return os.getenv("TXLINE_BASE_URL", "https://txline.txodds.com")

ScoreCallback = Callable[[ScoreEvent], Awaitable[None]]
OddsCallback = Callable[[OddsEvent], Awaitable[None]]


async def _stream_lines(url: str, headers: dict) -> AsyncIterator[str]:
    """Yield SSE lines from a stream URL. Reconnects on drop."""
    delay = RECONNECT_DELAY
    while True:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", url, headers={**headers, "Accept": "text/event-stream", "Cache-Control": "no-cache"}) as r:
                    if r.status_code != 200:
                        logger.error("SSE stream %s returned %s", url, r.status_code)
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, MAX_RECONNECT_DELAY)
                        continue
                    delay = RECONNECT_DELAY  # reset on success
                    async for line in r.aiter_lines():
                        yield line
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as e:
            logger.warning("SSE stream dropped (%s), reconnecting in %ss", e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_DELAY)


def _parse_sse_data(lines: list[str]) -> dict | None:
    """Parse accumulated SSE event lines into a data dict."""
    data_parts = []
    for line in lines:
        if line.startswith("data:"):
            data_parts.append(line[5:].strip())
    if not data_parts:
        return None
    try:
        return json.loads("".join(data_parts))
    except json.JSONDecodeError:
        return None


class TxLINEStreamer:
    def __init__(self, processed_ids: set[str] | None = None):
        self._processed_ids: set[str] = processed_ids or set()
        self._score_callbacks: list[ScoreCallback] = []
        self._odds_callbacks: list[OddsCallback] = []
        self._token: dict | None = None  # loaded lazily in run()

    def on_score(self, fn: ScoreCallback) -> ScoreCallback:
        self._score_callbacks.append(fn)
        return fn

    def on_odds(self, fn: OddsCallback) -> OddsCallback:
        self._odds_callbacks.append(fn)
        return fn

    def _is_duplicate(self, event_id: str) -> bool:
        if event_id in self._processed_ids:
            return True
        self._processed_ids.add(event_id)
        # Keep set bounded — drop oldest if over 10K
        if len(self._processed_ids) > 10_000:
            self._processed_ids = set(list(self._processed_ids)[-5_000:])
        return False

    async def _run_score_stream(self) -> None:
        headers = auth_headers(self._token)
        buffer: list[str] = []
        async for line in _stream_lines(f"{_base_url()}/api/scores/stream", headers):
            if line == "":
                # Empty line = end of SSE event block
                data = _parse_sse_data(buffer)
                buffer.clear()
                if data:
                    event = parse_score_event(data)
                    if event and event.event_id and not self._is_duplicate(event.event_id):
                        for cb in self._score_callbacks:
                            await cb(event)
            else:
                buffer.append(line)

    async def _run_odds_stream(self) -> None:
        headers = auth_headers(self._token)
        buffer: list[str] = []
        async for line in _stream_lines(f"{_base_url()}/api/odds/stream", headers):
            if line == "":
                data = _parse_sse_data(buffer)
                buffer.clear()
                if data:
                    event = parse_odds_event(data)
                    if event and event.event_id and not self._is_duplicate(f"odds_{event.event_id}"):
                        for cb in self._odds_callbacks:
                            await cb(event)
            else:
                buffer.append(line)

    async def run(self) -> None:
        """Run both streams concurrently. No-op (bot stays up) until a real TxLINE token exists."""
        if not TOKEN_PATH.exists():
            logger.warning(
                "No TxLINE token at %s — SSE streams disabled. "
                "Bot UI + betting work; run scripts/subscribe.mjs once IDL is available to enable live scores.",
                TOKEN_PATH,
            )
            return
        self._token = load_token()
        await asyncio.gather(
            self._run_score_stream(),
            self._run_odds_stream(),
        )
