from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class CacheEntry(Generic[T]):
    """缓存条目"""
    data: T
    expires_at: float  # 绝对时间戳（monotonic）


class InMemoryCache:
    """
    纯内存 LRU 缓存

    特性：
    - 使用 OrderedDict 实现 LRU 淘汰
    - asyncio.Lock 保护并发访问
    - 自动过期检查
    """

    def __init__(self, max_size: int = 10000, default_ttl: int = 60):
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._max_size = max_size
        self._default_ttl = default_ttl

    async def get(self, key: str) -> T | None:
        """获取缓存值，miss 或过期返回 None"""
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if time.monotonic() > entry.expires_at:
                del self._cache[key]
                return None
            # LRU: 移到末尾
            self._cache.move_to_end(key)
            return entry.data

    async def set(self, key: str, data: T, ttl: int | None = None) -> None:
        """设置缓存值"""
        ttl = ttl if ttl is not None else self._default_ttl
        async with self._lock:
            self._cache[key] = CacheEntry(
                data=data,
                expires_at=time.monotonic() + ttl,
            )
            self._cache.move_to_end(key)
            # LRU 淘汰
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    async def delete(self, key: str) -> None:
        """删除缓存"""
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        """清空所有缓存"""
        async with self._lock:
            self._cache.clear()

    async def size(self) -> int:
        """返回缓存条目数"""
        async with self._lock:
            return len(self._cache)