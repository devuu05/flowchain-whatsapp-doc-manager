"""Fixed-window rate limiting for public endpoints (webhooks, signed media, login).

Counters are process-local. That is acceptable here because (a) these limits are
defense-in-depth against floods, not a correctness mechanism, and (b) the pilot runs a single
API instance. Login brute-force protection is separately enforced in Postgres
(`login_attempts`), so it holds across instances. Behind a CDN/WAF, configure edge limits too.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable

from fastapi import Request

from app.core.errors import TooManyRequests


class FixedWindowLimiter:
    def __init__(self, limit: int, window_seconds: int, max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._buckets: OrderedDict[str, tuple[int, int]] = OrderedDict()
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        now_window = int(time.time() // self.window)
        with self._lock:
            window, count = self._buckets.get(key, (now_window, 0))
            if window != now_window:
                window, count = now_window, 0
            count += 1
            self._buckets[key] = (window, count)
            self._buckets.move_to_end(key)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
            return count <= self.limit

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


LIMITERS = {
    "webhook": FixedWindowLimiter(limit=600, window_seconds=60),
    "media": FixedWindowLimiter(limit=120, window_seconds=60),
    "login": FixedWindowLimiter(limit=30, window_seconds=60),
}


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def limit(name: str) -> Callable[[Request], None]:
    limiter = LIMITERS[name]

    def dependency(request: Request) -> None:
        if not limiter.hit(f"{name}:{_client_key(request)}"):
            raise TooManyRequests()

    return dependency
