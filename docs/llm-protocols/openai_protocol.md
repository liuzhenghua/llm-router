# 流式返回的原始报文：
```json
{"id":"0632b3a45cc84223d5387e5feb1f2628","created":1776517285,"model":"minimax-m2.7-highspeed","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"name":"MiniMax AI","audio_content":"","content":"<think>\nThe user","role":"assistant"}}],"input_sensitive":false,"output_sensitive":false,"input_sensitive_type":0,"output_sensitive_type":0,"output_sensitive_int":0}
{"id":"0632b3a45cc84223d5387e5feb1f2628","created":1776517285,"model":"minimax-m2.7-highspeed","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"name":"MiniMax AI","audio_content":"","content":" wants me to execute a bash command `ls -l`. This is a simple file listing command"}}],"input_sensitive":false,"output_sensitive":false,"input_sensitive_type":0,"output_sensitive_type":0,"output_sensitive_int":0}
{"id":"0632b3a45cc84223d5387e5feb1f2628","created":1776517285,"model":"minimax-m2.7-highspeed","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"name":"MiniMax AI","audio_content":"","content":".\n</think>\n\n\n"}}],"input_sensitive":false,"output_sensitive":false,"input_sensitive_type":0,"output_sensitive_type":0,"output_sensitive_int":0}
{"id":"0632b3a45cc84223d5387e5feb1f2628","created":1776517285,"model":"minimax-m2.7-highspeed","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"name":"MiniMax AI","audio_content":"","content":"","tool_calls":[{"id":"call_function_a6dzjuv09o80_1","function":{"arguments":"{\"command\": \"ls -l\", \"description\": ","name":"Bash"},"type":"function","index":0}]}}]}
{"id":"0632b3a45cc84223d5387e5feb1f2628","created":1776517285,"model":"minimax-m2.7-highspeed","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"name":"MiniMax AI","audio_content":"","content":"","role":"assistant","tool_calls":[{"function":{"arguments":"null, \"timeout\": null}"},"type":"function","index":0}]}}]}
{"id":"0632b3a45cc84223d5387e5feb1f2628","created":1776517285,"model":"minimax-m2.7-highspeed","object":"chat.completion.chunk","choices":[{"finish_reason":"tool_calls","index":0,"delta":{}}]}
{"id":"0632b3a45cc84223d5387e5feb1f2628","created":1776517285,"model":"minimax-m2.7-highspeed","object":"chat.completion.chunk","choices":[{"index":0,"delta":{}}],"usage":{"completion_tokens":75,"prompt_tokens":14059,"total_tokens":14134,"completion_tokens_details":{"reasoning_tokens":25}}}
```

# 处理后的报文
```json
{
  "id": "0632b3a45cc84223d5387e5feb1f2628",
  "object": "chat.completion",
  "created": 1776517285,
  "model": "minimax-m2.7-highspeed",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "<think>\nThe user wants me to execute a bash command `ls -l`. This is a simple file listing command.\n</think>\n\n\n",
        "tool_calls": [
          {
            "id": "call_function_a6dzjuv09o80_1",
            "type": "function",
            "function": {
              "name": "Bash",
              "arguments": "{\"command\": \"ls -l\", \"description\": null, \"timeout\": null}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ],
  "usage": {
    "prompt_tokens": 14059,
    "completion_tokens": 75,
    "total_tokens": 14134,
    "completion_tokens_details": {
      "reasoning_tokens": 25
    }
  }
}
```