from abc import ABC, abstractmethod
from dataclasses import dataclass

from llm_router.domain.schemas import UsageSnapshot


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
