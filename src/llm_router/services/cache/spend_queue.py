from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Redis key for spend delta queue
QUEUE_KEY = "llm_router:queue:spend_delta"


@dataclass(slots=True)
class SpendDelta:
    """Token 费用增量"""
    api_key_id: int
    delta_amount: Decimal  # 本次增量（为负数，表示扣款）
    request_id: str
    timestamp: float = field(default_factory=time.time)
    delta_id: str = field(default_factory=lambda: uuid.uuid4().hex)


class SpendDeltaQueue:
    """
    增量队列：用于收集 token 费用增量，定期批量写入 DB

    设计要点：
    1. local 模式：内存队列 asyncio.Queue，直接写入 DB
    2. server 模式：Redis ZSET 队列，批量消费
    """

    def __init__(self, is_server_mode: bool, redis_client: redis.Redis | None = None):
        self._is_server_mode = is_server_mode
        self._redis = redis_client
        self._local_queue: asyncio.Queue[SpendDelta] | None = None
        if not is_server_mode:
            self._local_queue = asyncio.Queue()

    async def push(self, delta: SpendDelta) -> None:
        """推入增量"""
        if self._is_server_mode and self._redis:
            # Redis ZSET: score=timestamp, member=json
            member = json.dumps({
                "api_key_id": delta.api_key_id,
                "delta_amount": str(delta.delta_amount),
                "request_id": delta.request_id,
                "timestamp": delta.timestamp,
                "delta_id": delta.delta_id,
            })
            await self._redis.zadd(QUEUE_KEY, {member: delta.timestamp})
        elif self._local_queue:
            await self._local_queue.put(delta)

    async def pop_batch(self, batch_size: int = 100) -> list[SpendDelta]:
        """批量取出增量"""
        deltas: list[SpendDelta] = []

        if self._is_server_mode and self._redis:
            # 取出最早的 N 条
            members = await self._redis.zrange(QUEUE_KEY, 0, batch_size - 1)
            for member in members:
                data = json.loads(member)
                delta = SpendDelta(
                    api_key_id=data["api_key_id"],
                    delta_amount=Decimal(data["delta_amount"]),
                    request_id=data["request_id"],
                    timestamp=data["timestamp"],
                    delta_id=data["delta_id"],
                )
                deltas.append(delta)
            # 删除已取出的
            if members:
                await self._redis.zrem(QUEUE_KEY, *members)
        elif self._local_queue:
            for _ in range(batch_size):
                try:
                    delta = self._local_queue.get_nowait()
                    deltas.append(delta)
                except asyncio.QueueEmpty:
                    break

        return deltas

    async def size(self) -> int:
        """队列大小"""
        if self._is_server_mode and self._redis:
            return await self._redis.zcard(QUEUE_KEY)
        elif self._local_queue:
            return self._local_queue.qsize()
        return 0


# 全局实例
spend_queue: SpendDeltaQueue | None = None


def get_spend_queue() -> SpendDeltaQueue | None:
    return spend_queue


def set_spend_queue(queue: SpendDeltaQueue) -> None:
    global spend_queue
    spend_queue = queue