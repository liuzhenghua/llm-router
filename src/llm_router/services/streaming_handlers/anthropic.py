import json

from llm_router.domain.schemas import UsageSnapshot
from llm_router.services.streaming_handlers.base import BaseStreamingHandler, StreamChunk


class AnthropicStreamingHandler(BaseStreamingHandler):
    """Anthropic 流式处理器 - 将 SSE 事件合并为完整的 message"""

    def __init__(self):
        self._usage_dict: dict | None = None
        self._message_blocks: list[dict] = []
        self._current_event: str | None = None
        self._current_block_type: str | None = None
        self._current_text: str = ""
        self._current_tool_name: str = ""
        self._current_tool_args: str = ""
        self._current_tool_call_id: str = ""
        self._final_message: dict | None = None
        self._stop_reason: str | None = None
        self._model: str | None = None
        self._id: str | None = None
        self._type: str | None = None

    async def process_line(self, line: str) -> StreamChunk | None:
        if line.startswith("event:"):
            self._current_event = line.split(":", 1)[1].strip()
            return None
        elif line.startswith("data:"):
            data_str = line.split(":", 1)[1].strip()
            if data_str:
                data = json.loads(data_str)
                self._process_event(data)
        elif line == "":
            self._current_event = None
        return None

    def _process_event(self, data: dict) -> None:
        event = self._current_event

        if event == "message_start":
            self._id = data.get("message", {}).get("id")
            self._model = data.get("message", {}).get("model")
            self._type = data.get("message", {}).get("type")
            if data.get("message", {}).get("usage"):
                self._usage_dict = data["message"]["usage"]

        elif event == "content_block_start":
            block = data.get("content_block") or {}
            self._current_block_type = block.get("type")
            if self._current_block_type == "text":
                self._current_text = ""
            elif self._current_block_type == "tool_use":
                self._current_tool_name = block.get("name", "")
                self._current_tool_call_id = block.get("id", "")
                self._current_tool_args = ""

        elif event == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "text_delta":
                self._current_text += delta.get("text", "")
            elif delta.get("type") == "input_json_delta":
                self._current_tool_args += delta.get("partial_json", "")

        elif event == "content_block_end":
            if self._current_block_type == "text":
                self._message_blocks.append({"type": "text", "text": self._current_text})
            elif self._current_block_type == "tool_use":
                self._message_blocks.append({
                    "type": "tool_use",
                    "id": self._current_tool_call_id,
                    "name": self._current_tool_name,
                    "input": json.loads(self._current_tool_args) if self._current_tool_args else {},
                })
            self._current_block_type = None

        elif event == "message_delta":
            if data.get("usage"):
                self._usage_dict = data["usage"]
            self._stop_reason = data.get("delta", {}).get("stop_reason")

        elif event == "message_end":
            self._final_message = {
                "id": self._id,
                "type": self._type,
                "model": self._model,
                "role": "assistant",
                "content": self._message_blocks,
                "stop_reason": self._stop_reason,
                "stop_sequence": None,
                "usage": self._usage_dict,
            }

    def get_accumulated_response(self) -> str:
        if self._final_message:
            return json.dumps(self._final_message, ensure_ascii=False)
        return json.dumps({"content": self._message_blocks}, ensure_ascii=False)

    def get_usage(self) -> UsageSnapshot | None:
        if not self._usage_dict:
            return None
        return UsageSnapshot(
            prompt_tokens=self._usage_dict.get("input_tokens", 0),
            completion_tokens=self._usage_dict.get("output_tokens", 0),
            cache_read_tokens=self._usage_dict.get("cache_read_input_tokens", 0),
            cache_write_tokens=self._usage_dict.get("cache_creation_input_tokens", 0),
        )
