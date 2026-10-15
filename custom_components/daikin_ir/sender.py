"""Coalescing, single-flight IR sender.

The ZS06 takes ~1 s of Zigbee round trips per code, but mqtt.publish returns
immediately. Overlapping transfers corrupt the converter's single in-flight
slot and can wedge the blaster until it is power-cycled. So:

* schedule() only records the latest code; it is sent once no new code has
  arrived for `debounce` seconds (a blueprint's set_hvac_mode +
  set_fan_mode + set_temperature burst becomes one transmission).
* Sends are serialised and spaced at least `min_interval` seconds apart.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

_LOGGER = logging.getLogger(__name__)


class IRSender:
    def __init__(
        self,
        publish: Callable[[str], Awaitable[None]],
        debounce: float = 1.0,
        min_interval: float = 3.0,
    ) -> None:
        self._publish = publish
        self._debounce = debounce
        self._min_interval = min_interval
        self._pending: str | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self._last_sent = float("-inf")

    def schedule(self, code: str) -> None:
        self._pending = code
        if self._timer is not None:
            self._timer.cancel()
        loop = asyncio.get_running_loop()
        self._timer = loop.call_later(self._debounce, self._start_flush)

    def _start_flush(self) -> None:
        self._timer = None
        task = asyncio.get_running_loop().create_task(self._flush())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _flush(self) -> None:
        async with self._lock:
            wait = self._last_sent + self._min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            code, self._pending = self._pending, None
            if code is None:
                return
            try:
                await self._publish(code)
            except Exception:  # noqa: BLE001 — keep the sender alive for later codes
                _LOGGER.exception("Failed to publish IR code")
            finally:
                self._last_sent = time.monotonic()

    async def async_flush_now(self) -> None:
        """Send any pending code without waiting for the debounce (shutdown/tests)."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        await self._flush()

    def cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        for task in self._tasks:
            task.cancel()
