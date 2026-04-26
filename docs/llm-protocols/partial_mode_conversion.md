# Partial Mode 协议转换规则

## 概述

Partial Mode（部分消息模式）是 OpenAI/Kimi 等大模型提供的一项功能，允许通过预填（Prefill）部分模型回复来引导模型的输出。在协议转换过程中，需要特殊处理最后一条 `assistant` 类型的消息。

## 核心规则

### Anthropic → OpenAI 转换规则

当 Anthropic Messages API 请求转换为 OpenAI Chat Completions 格式时：

**如果消息列表的最后一条是 `assistant` 角色，则需要添加 `"partial": true` 标记。**

```python
# Anthropic 原始请求
{
  "messages": [
    {"role": "user", "content": "请用JSON格式输出用户信息"},
    {"role": "assistant", "content": "{\n  \"name\": \""}  # 预填开头
  ]
}

# 转换后的 OpenAI 请求
{
  "messages": [
    {"role": "user", "content": "请用JSON格式输出用户信息"},
    {"role": "assistant", "content": "{\n  \"name\": \"", "partial": true}  # 添加 partial 标记
  ]
}
```

### 转换条件

| 条件 | 是否添加 `partial` |
|------|-------------------|
| 最后一条消息 role = `assistant` | ✅ 添加 `"partial": true` |
| 最后一条消息 role = `user` | ❌ 不添加 |
| 最后一条消息 role = `tool` | ❌ 不添加 |
| 最后一条 assistant 消息包含 `tool_calls` | ❌ 不添加（工具调用消息不启用 partial） |
| 非最后一条的 assistant 消息 | ❌ 不添加 |

### OpenAI → Anthropic 转换规则

当 OpenAI Chat Completions 请求转换回 Anthropic Messages API 格式时：

**`partial` 字段是 OpenAI 特有的扩展，转换回 Anthropic 时直接丢弃，无需保留。**

Anthropic 协议本身没有 `partial` 标记的概念，最后一条 `assistant` 消息自然就是预填内容。

```python
# OpenAI 请求（带 partial）
{
  "messages": [
    {"role": "user", "content": "讲个故事"},
    {"role": "assistant", "content": "从前有座山，", "partial": true}
  ]
}

# 转换后的 Anthropic 请求（partial 被移除）
{
  "messages": [
    {"role": "user", "content": "讲个故事"},
    {"role": "assistant", "content": "从前有座山，"}
  ]
}
```

## 使用场景

### 1. 控制输出格式

通过预填 JSON/XML 的开头，强制模型输出特定格式：

```json
{
  "messages": [
    {"role": "user", "content": "请输出用户数据"},
    {"role": "assistant", "content": "{\n  \"users\": [", "partial": true}
  ]
}
```

### 2. 引导输出内容

通过预填开头语句，引导模型按照特定风格或方向继续：

```json
{
  "messages": [
    {"role": "user", "content": "分析这首诗的意境"},
    {"role": "assistant", "content": "这首诗通过", "partial": true}
  ]
}
```

### 3. 角色扮演一致性

在角色扮演场景中，预填角色的说话风格开头：

```json
{
  "messages": [
    {"role": "system", "content": "你是一位专业的客服代表，说话礼貌且简洁。"},
    {"role": "user", "content": "我的订单怎么还没到？"},
    {"role": "assistant", "content": "非常抱歉给您带来不便，", "partial": true}
  ]
}
```

## ⚠️ 重要警告

**请勿混用 partial mode 和 `response_format=json_object`，否则可能会获得预期外的模型回复。**

```python
# ❌ 错误示例：混用 partial 和 json_object
def validate_request(messages, response_format):
    has_partial = any(
        msg.get("role") == "assistant" and msg.get("partial")
        for msg in messages
    )
    is_json_mode = response_format and response_format.get("type") == "json_object"

    if has_partial and is_json_mode:
        raise ValueError(
            "请勿混用 partial mode 和 response_format=json_object，"
            "否则可能会获得预期外的模型回复"
        )
```

## 代码实现位置

协议转换的核心逻辑位于：`src/llm_router/services/protocol_converter.py`

### anthropic_to_openai_request 函数

```python
# 关键逻辑片段
for i, msg in enumerate(raw_messages):
    role = msg["role"]
    # ... 消息转换逻辑 ...

    is_last = (i == len(raw_messages) - 1)
    # Partial Mode: when the last message is from assistant, mark it as partial
    if is_last and role == "assistant" and not msg.get("tool_calls"):
        openai_msg["partial"] = True
```

### openai_to_anthropic_request 函数

`partial` 字段在转换回 Anthropic 格式时自然丢弃，无需特殊处理。

## 相关文档

- [Anthropic 协议文档](./anthropic_protocol.md)
- [OpenAI 协议文档](./openai_protocol.md)
