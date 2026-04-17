from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from fastapi import HTTPException, status


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[int, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, api_key_id: int, qps_limit: int) -> None:
        if qps_limit <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            bucket = self._buckets[api_key_id]
            while bucket and now - bucket[0] >= 1:
                bucket.popleft()
            if len(bucket) >= qps_limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="QPS limit exceeded",
                )
            bucket.append(now)


rate_limiter = InMemoryRateLimiter()
