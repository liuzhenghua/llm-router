# llm-router Architecture

## Overview

`llm-router` 是一个基于 Python 的 LLM 转发网关，面向两类场景：

- `local`：单机部署，使用 SQLite，强调低启动成本
- `server`：多实例部署，使用 MySQL，强调共享状态与集中管理

系统对外提供兼容：

- OpenAI `POST /v1/chat/completions`
- Anthropic `POST /v1/messages`

系统对内提供：

- 逻辑模型到下游模型的路由
- API Key 级别的余额、日限额、QPS 与模型授权控制
- 请求级 token 用量与费用记录
- 请求/响应内容的可选审计记录
- 管理后台、账本和请求日志查询

## Core Concepts

### Logical Model

逻辑模型是网关对外暴露的模型名称。调用方只感知逻辑模型，不直接感知具体下游厂商或部署。

示例：

- `gpt-4o`
- `claude-sonnet`
- `deep-reasoner`

### Provider Model

下游模型配置描述一个可实际请求的上游模型实例，包含：

- provider 类型：`openai` 或 `anthropic`
- 协议类型：`openai` 或 `anthropic`
- 上游 endpoint
- 上游 model 名称
- 加密保存的上游 API Key
- 每百万 token 单价
- prompt cache 支持能力
- 超时、启停状态

### Route

路由将一个逻辑模型映射到一个或多个 provider model。当前路由依据优先级顺序选择下游，并在失败时按顺序尝试后备路由。

## Protocol Boundary

系统当前采用同协议转发模型：

- OpenAI 入口路由到 OpenAI 协议的 provider
- Anthropic 入口路由到 Anthropic 协议的 provider

当前并不做跨协议语义转换。也就是说，OpenAI 请求不会自动转换成 Anthropic 请求格式，反之亦然。

## Runtime Modes

### Local Mode

`local` 模式面向开发、自托管和低成本单机部署。

特点：

- SQLite 作为默认存储
- 数据文件落在本地目录
- 无外部基础设施要求
- 启动路径简单

### Server Mode

`server` 模式面向多实例部署。

特点：

- MySQL 作为共享数据库
- 适合多个应用实例共享 API Key、路由、账本与日志
- 便于集中管理和运维

## Request Lifecycle

一次请求在系统中的标准处理过程如下：

1. 接收 OpenAI 或 Anthropic 协议请求
2. 从请求头中解析 API Key
3. 校验 API Key 状态、余额、日限额、QPS 和模型访问权限
4. 依据逻辑模型查询可用路由
5. 选出符合协议要求的 provider model
6. 将请求转发到上游 endpoint
7. 解析上游 usage 信息
8. 按价格快照计算费用
9. 写入请求日志、用量记录、账本和日报汇总
10. 将响应按原协议返回给调用方

在上游失败时，系统会按路由优先级尝试后续 provider；若全部失败，则记录失败请求。

## Streaming

系统支持 OpenAI SSE 流式响应和 Anthropic streaming。

流式处理遵循以下原则：

- 保持上游事件格式尽量透明
- OpenAI 流式请求自动附加 `stream_options.include_usage=true`，以便在流结束时获取 usage
- Anthropic 流式 usage 依赖上游事件中的 usage 字段
- 流结束后统一落库账单与日志
- 流中断时记录失败请求，并保留已知错误上下文

## Billing Model

系统采用按请求计费模型，定价口径为每百万 token 单价。

计算公式：

- `input_cost = (prompt_tokens - cache_read_tokens - cache_write_tokens) / 1_000_000 * input_token_price`
- `output_cost = completion_tokens / 1_000_000 * output_token_price`
- `cache_read_cost = cache_read_tokens / 1_000_000 * cache_read_token_price`
- `cache_write_cost = cache_write_tokens / 1_000_000 * cache_write_token_price`

说明：
- `prompt_tokens` 包含 `cache_read_tokens` 和 `cache_write_tokens`，因此需减去后避免重复计费
- `reasoning_tokens`（思考 tokens）包含在 `completion_tokens` 中，用于单独统计，不单独计费
- 费用在请求发生时按价格快照记录，因此后续改价不会影响历史账单

## Quota and Access Control

每个 API Key 可以独立配置：

- 当前余额
- 每日费用上限
- QPS 限制
- 可访问逻辑模型列表
- 请求内容日志开关
- 响应内容日志开关
- 启用或禁用状态

其中：

- 余额不足会直接拒绝请求
- 当日累计费用超限会拒绝请求
- QPS 超限会返回限流错误

## Persistence Model

系统当前持久化以下核心实体：

### `api_keys`

保存 API Key 的哈希、额度配置、限流配置和日志策略。

### `logical_models`

保存对外暴露的逻辑模型名称与状态。

### `provider_models`

保存下游 provider 的 endpoint、协议、上游模型名、价格和加密密钥。

### `logical_model_routes`

保存逻辑模型与 provider model 的映射、优先级、权重和后备标记。

### `request_logs`

保存每次请求的协议、状态码、延迟、错误信息及可选的请求/响应内容。

### `usage_records`

保存每次请求的 token 用量、价格快照和费用拆分。

### `balance_ledgers`

保存充值、扣费、调整和退款等余额流水。

### `daily_usage_summaries`

保存按 API Key 聚合的日请求量、token 用量和费用。

## Security Model

系统中的密钥与认证边界如下：

- 调用方 API Key 只保存哈希，不保存明文
- 上游 provider API Key 使用应用级加密密文保存
- 管理后台采用独立管理员用户文件
- 管理员密码仅保存哈希
- 后台登录态通过 session cookie 维护

加密与会话安全依赖以下配置：

- `APP_ENCRYPTION_KEY`
- `SESSION_SECRET`

## Admin Surface

管理后台提供：

- 管理员登录
- API Key 创建、编辑、充值与禁用
- 逻辑模型创建与编辑
- Provider 创建、编辑与禁用
- Route 创建、编辑与删除
- 请求日志列表与详情
- 账本、用量记录与日报查看

默认情况下，请求内容和响应内容不落库，仅记录元数据；可按 API Key 单独开启。

## Deployment Layout

项目当前采用 `src` 布局，核心目录如下：

```text
llm-router/
  src/llm_router/
    api/
    core/
    domain/
    services/
    templates/
  docs/architecture/
    llm-router-architecture.md
  docker/
    local/
      docker-compose.yaml
      data/
    server/
      docker-compose.yaml
      init.sql
      data/
  tests/
  Dockerfile
```

Docker 部署结构分为两套：

- `docker/local`：SQLite 本地部署
- `docker/server`：MySQL 部署，包含数据库初始化脚本

## Current Boundaries

当前系统边界如下：

- 支持 OpenAI 与 Anthropic 两种协议入口
- 支持非流式和流式转发
- 支持同协议路由，不支持跨协议转换
- 支持基于优先级的路由与故障切换
- 支持后台管理与基础运维能力

尚未覆盖的能力包括：

- Redis 分布式限流
- 跨协议请求转换
- 更复杂的智能路由策略
- 多租户与细粒度权限系统
- 复杂报表与离线分析链路
