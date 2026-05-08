"""Shared per-IP sliding-window rate limiter.

Used by /api/scanners/claim, /api/scanners/discover, and
/api/oauth/callback. Pre-fix each endpoint had its own copy-pasted
defaultdict(deque) + manual prune loop, plus a hand-rolled
"if len > N: drop empties" eviction pattern that wasn't a proper
bounded LRU.

This module replaces all three. Backed by an OrderedDict so eviction
is real LRU (move-to-end on touch, popitem(last=False) on overflow).

Each Limiter instance has its own bucket dict so different endpoints
don't share state. Construct once per endpoint at module import time
and call ``check(ip)`` per request — raises HTTPException(429) on
overflow.
"""
from __future__ import annotations

import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request


class Limiter:
    def __init__(
        self,
        *,
        max_requests: int,
        window_seconds: float,
        max_buckets: int = 10_000,
        message: str = "too many requests; try again shortly",
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_buckets = max_buckets
        self.message = message
        # OrderedDict gives us O(1) move-to-end + popitem(last=False)
        # for a real LRU. Each value is a deque of timestamps.
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()

    def _client_ip(self, request: Request) -> str:
        client = request.client
        return client.host if client else "unknown"

    def check(self, request: Request) -> None:
        ip = self._client_ip(request)
        now = time.monotonic()

        bucket = self._buckets.get(ip)
        if bucket is None:
            # New IP — bound the dict before inserting.
            if len(self._buckets) >= self.max_buckets:
                self._buckets.popitem(last=False)  # evict LRU
            bucket = deque()
            self._buckets[ip] = bucket
        else:
            # Touch — move to MRU end.
            self._buckets.move_to_end(ip)

        # Sliding-window prune.
        cutoff = now - self.window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= self.max_requests:
            raise HTTPException(status_code=429, detail=self.message)
        bucket.append(now)

    def reset(self) -> None:
        """Clear all buckets — used by tests so the limiter doesn't
        leak state across the session."""
        self._buckets.clear()


def reset_all() -> None:
    """Test helper — clear every Limiter instance registered in
    _instances. Used by conftest's autouse fixture."""
    for limiter in _instances:
        limiter.reset()


_instances: list[Limiter] = []


def make_limiter(**kwargs) -> Limiter:
    """Factory that registers each instance for reset_all()."""
    limiter = Limiter(**kwargs)
    _instances.append(limiter)
    return limiter
