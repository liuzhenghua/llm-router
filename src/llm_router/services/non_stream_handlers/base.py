from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from llm_router.domain.schemas import UsageSnapshot

if TYPE_CHECKING:
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

    from llm_router.domain.models import ApiKey
    from llm_router.domain.schemas import RequestContext, RoutedProvider


class BaseNonStreamHandler(ABC):
    """非流式处理器基类"""

    @abstractmethod
    def prepare_payload(self, payload: dict, provider: "RoutedProvider") -> dict:
        """准备发送到上游的请求体"""

    @abstractmethod
    def build_upstream_headers(self, provider: "RoutedProvider", context: "RequestContext") -> dict:
        """构建发送到上游的请求头"""

    @abstractmethod
    def get_usage(self, body: dict) -> UsageSnapshot | None:
        """从响应体中提取 usage 信息"""

    @abstractmethod
    def get_upstream_request_id(self, body: dict, headers: "httpx.Headers") -> str | None:
        """获取 upstream_request_id，优先从 body 获取"""

    @abstractmethod
    async def proxy(
        self,
        session: "AsyncSession",
        *,
        api_key: "ApiKey",
        context: "RequestContext",
        provider: "RoutedProvider",
        request_path: str,
    ):
        """执行非流式代理请求"""
