from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from llm_router.domain.schemas import UsageSnapshot

if TYPE_CHECKING:
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

    from llm_router.domain.models import ApiKey
    from llm_router.domain.schemas import RequestContext, RoutedProvider


@dataclass
class StreamChunk:
    """流式响应的单个数据块"""
    raw_line: str
    event_type: str | None
    data: dict | None
    content: str | None
    content_type: str | None = None  # "text", "tool_use"


class BaseStreamingHandler(ABC):
    """流式处理器基类"""

    @abstractmethod
    def prepare_payload(self, payload: dict, provider: "RoutedProvider") -> dict:
        """准备发送到上游的请求体"""

    @abstractmethod
    def build_upstream_headers(self, provider: "RoutedProvider", context: "RequestContext") -> dict:
        """构建发送到上游的请求头"""

    @abstractmethod
    async def process_line(self, line: str) -> StreamChunk | None:
        """处理一行数据"""

    @abstractmethod
    def get_accumulated_response(self) -> str:
        """获取合并后的非流式原始报文（JSON字符串）"""

    @abstractmethod
    def get_usage(self) -> UsageSnapshot | None:
        """获取 usage 快照"""

    @abstractmethod
    def get_upstream_request_id(self) -> str | None:
        """获取 upstream_request_id"""

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
        """执行流式代理请求，返回 StreamingResponse"""
