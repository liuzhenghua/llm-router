import json

from llm_router.domain.schemas import UsageSnapshot
from llm_router.services.streaming_handlers.base import BaseStreamingHandler, StreamChunk


class OpenAIStreamingHandler(BaseStreamingHandler):
    """OpenAI 流式处理器 - 将 chunk 合并为完整的 message"""

    def __init__(self):
        self._chunks: list[dict] = []
        self._usage_dict: dict | None = None
        self._message: dict | None = None
        self._finish_reason = None
        self._model = None
        self._id = None
        self._created = None

    async def process_line(self, line: str) -> StreamChunk | None:
        if line.startswith("data:"):
            data_str = line.split(":", 1)[1].strip()
            if data_str and data_str != "[DONE]":
                data = json.loads(data_str)
                self._chunks.append(data)
                self._merge_chunk(data)
        return None

    def _merge_chunk(self, chunk: dict) -> None:
        if chunk.get("id"):
            self._id = chunk["id"]
        if chunk.get("model"):
            self._model = chunk["model"]
        if chunk.get("created"):
            self._created = chunk["created"]

        if chunk.get("usage"):
            self._usage_dict = chunk["usage"]

        for choice in chunk.get("choices", []):
            delta = choice.get("delta") or {}

            if self._message is None and delta:
                self._message = {}

            if delta.get("content"):
                self._message.setdefault("content", "")
                self._message["content"] += delta["content"]

            if delta.get("role"):
                self._message["role"] = delta["role"]

            if delta.get("tool_calls"):
                self._message.setdefault("tool_calls", [])
                for tc in delta["tool_calls"]:
                    index = tc.get("index", 0)
                    while len(self._message["tool_calls"]) <= index:
                        self._message["tool_calls"].append({
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        })
                    if tc.get("id"):
                        self._message["tool_calls"][index]["id"] = tc["id"]
                    if tc.get("type"):
                        self._message["tool_calls"][index]["type"] = tc["type"]
                    if tc.get("function"):
                        fn = tc["function"]
                        if fn.get("name"):
                            self._message["tool_calls"][index]["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            self._message["tool_calls"][index]["function"]["arguments"] += fn["arguments"]

            if choice.get("finish_reason"):
                self._finish_reason = choice["finish_reason"]

    def get_accumulated_response(self) -> str:
        result = {
            "id": self._id,
            "object": "chat.completion",
            "created": self._created,
            "model": self._model,
            "choices": [
                {
                    "index": 0,
                    "message": self._message or {},
                    "finish_reason": self._finish_reason,
                }
            ],
            "usage": self._usage_dict,
        }
        return json.dumps(result, ensure_ascii=False)

    def get_usage(self) -> UsageSnapshot | None:
        if not self._usage_dict:
            return None
        details = self._usage_dict.get("prompt_tokens_details") or {}
        return UsageSnapshot(
            prompt_tokens=self._usage_dict.get("prompt_tokens", 0),
            completion_tokens=self._usage_dict.get("completion_tokens", 0),
            cache_read_tokens=details.get("cached_tokens", 0),
            cache_write_tokens=0,
        )

    def get_upstream_request_id(self) -> str | None:
        return self._id
