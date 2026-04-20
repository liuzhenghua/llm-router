"""
降级路由恢复机制

定时扫描 degraded 的路由，尝试恢复。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx
from sqlalchemy import select

from llm_router.core.config import get_settings
from llm_router.core.security import Encryptor
from llm_router.domain.models import LogicalModelRoute, ProviderModel
from llm_router.services.cache.degraded_cache import DegradedRouteCache, DegradedType, RouteDegradedStatus
from llm_router.services.cache.dual_cache import get_dual_cache


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)

# 恢复探测成功次数阈值
RECOVERY_SUCCESS_THRESHOLD = 3

# 探测请求超时（秒）
PROBE_TIMEOUT = 5


class DegradedRouteRecovery:
    """
    降级路由恢复器

    定时扫描 degraded 的路由，根据降级类型选择合适的探测方式尝试恢复。
    """

    def __init__(self):
        dual_cache = get_dual_cache()
        self._degraded_cache = DegradedRouteCache(dual_cache) if dual_cache else None
        self._dual_cache = dual_cache
        settings = get_settings()
        self._encryptor = Encryptor(settings.app_encryption_key)

    async def scan_and_recover(self, session: AsyncSession) -> int:
        """
        扫描所有降级路由并尝试恢复

        Returns:
            恢复成功的路由数量
        """
        if not self._degraded_cache or not self._dual_cache:
            logger.warning("DegradedRouteRecovery: cache not available")
            return 0

        # 获取所有降级路由 ID
        degraded_route_ids = await self._dual_cache.get_all_degraded_route_ids()
        if not degraded_route_ids:
            return 0

        logger.info(f"DegradedRouteRecovery: scanning {len(degraded_route_ids)} degraded routes")

        recovered_count = 0
        for route_id in degraded_route_ids:
            try:
                success = await self.attempt_recovery(route_id, session)
                if success:
                    recovered_count += 1
            except Exception as e:
                logger.error(f"DegradedRouteRecovery: error recovering route {route_id}: {e}")

        logger.info(f"DegradedRouteRecovery: scanned {len(degraded_route_ids)} routes, recovered {recovered_count}")
        return recovered_count

    async def attempt_recovery(
        self,
        route_id: int,
        session: AsyncSession,
    ) -> bool:
        """
        尝试恢复单个路由

        Args:
            route_id: 路由 ID
            session: 数据库会话

        Returns:
            True 如果恢复成功，False 否则
        """
        if not self._degraded_cache:
            return False

        status = await self._degraded_cache.get_status(route_id)
        if status is None:
            return False

        degraded_type = status.degraded_type

        # 根据降级类型选择探测方式
        if degraded_type == DegradedType.QUOTA_EXHAUSTED:
            # 配额问题：需要发送推理请求探测
            success = await self._probe_with_inference(route_id, session)
        else:
            # 连通性问题：可以用轻量接口探测（如模型列表）
            success = await self._probe_with_health_check(route_id, session)

        if success:
            # 恢复路由
            await self._degraded_cache.recover(route_id)
            logger.info(f"Route {route_id} recovered successfully")
            return True

        return False

    async def _probe_with_inference(
        self,
        route_id: int,
        session: AsyncSession,
    ) -> bool:
        """
        使用推理请求探测配额是否恢复

        发送一个 max_tokens=1 的最小请求，验证是否能正常返回
        """
        # 获取路由和 provider 信息
        route = await session.get(LogicalModelRoute, route_id)
        if not route:
            logger.warning(f"Route {route_id} not found in DB")
            return False

        provider = route.provider_model
        if not provider or not provider.is_active:
            logger.warning(f"Provider for route {route_id} not available")
            return False

        # 构建探测请求
        try:
            api_key = self._encryptor.decrypt(provider.encrypted_api_key)
        except Exception as e:
            logger.error(f"Failed to decrypt API key for route {route_id}: {e}")
            return False

        # 根据协议选择端点
        endpoint = provider.endpoint.rstrip("/")
        if provider.protocol == "openai":
            url = f"{endpoint}/chat/completions"
            payload = {
                "model": provider.upstream_model_name,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }
        else:
            # anthropic
            url = f"{endpoint}/messages"
            payload = {
                "model": provider.upstream_model_name,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
                response = await client.post(url, json=payload, headers=headers)

                if response.status_code == 200:
                    # 成功，配额已恢复
                    return True
                elif response.status_code == 429 or response.status_code == 403:
                    # 仍然限流/无权限，配额未恢复
                    return False
                else:
                    # 其他错误，可能是暂时性问题，继续保持降级
                    logger.warning(f"Probe request for route {route_id} returned {response.status_code}")
                    return False
        except httpx.TimeoutException:
            logger.warning(f"Probe request for route {route_id} timed out")
            return False
        except Exception as e:
            logger.error(f"Probe request for route {route_id} failed: {e}")
            return False

    async def _probe_with_health_check(
        self,
        route_id: int,
        session: AsyncSession,
    ) -> bool:
        """
        使用轻量接口探测连通性

        可以调用模型的 list 接口或类似的健康检查
        """
        # 获取路由和 provider 信息
        route = await session.get(LogicalModelRoute, route_id)
        if not route:
            logger.warning(f"Route {route_id} not found in DB")
            return False

        provider = route.provider_model
        if not provider or not provider.is_active:
            logger.warning(f"Provider for route {route_id} not available")
            return False

        # 构建探测请求
        try:
            api_key = self._encryptor.decrypt(provider.encrypted_api_key)
        except Exception as e:
            logger.error(f"Failed to decrypt API key for route {route_id}: {e}")
            return False

        # 构建健康检查请求
        endpoint = provider.endpoint.rstrip("/")

        if provider.protocol == "openai":
            # OpenAI: /models 接口
            url = f"{endpoint}/models"
        else:
            # Anthropic: 没有轻量健康检查接口，使用 /messages 构造最小请求
            # 这里复用推理探测
            return await self._probe_with_inference(route_id, session)

        headers = {
            "Authorization": f"Bearer {api_key}",
        }

        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
                response = await client.get(url, headers=headers)

                if response.status_code == 200:
                    # 连通性恢复
                    return True
                else:
                    logger.warning(f"Health check for route {route_id} returned {response.status_code}")
                    return False
        except httpx.TimeoutException:
            logger.warning(f"Health check for route {route_id} timed out")
            return False
        except Exception as e:
            logger.error(f"Health check for route {route_id} failed: {e}")
            return False


# 定时任务运行器
async def run_recovery_task(session_factory, interval_seconds: int = 300) -> None:
    """
    定期运行降级路由恢复任务

    Args:
        session_factory: 数据库会话工厂
        interval_seconds: 运行间隔（秒），默认 5 分钟
    """
    recovery = DegradedRouteRecovery()

    while True:
        try:
            async with session_factory() as session:
                count = await recovery.scan_and_recover(session)
                if count > 0:
                    logger.info(f"Recovery task: recovered {count} routes")
        except Exception as e:
            logger.error(f"Recovery task error: {e}")

        await asyncio.sleep(interval_seconds)
