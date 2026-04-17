# llm-router

一个基于 Python + `uv` 的 LLM 转发路由网关，支持：

- `local` 模式：SQLite，单机低成本启动
- `server` 模式：MySQL，面向多实例部署
- OpenAI `POST /v1/chat/completions`
- Anthropic `POST /v1/messages`
- 逻辑模型到下游 provider 路由
- API Key 余额、按天限额、QPS 控制
- token 用量与费用记录
- Provider 定价按“每百万 token 单价”配置
- 请求/响应内容可选记录，默认关闭
- 管理后台登录与基础配置页面

## 目录

- 架构设计：[llm-router-architecture.md](docs/architecture/llm-router-architecture.md)
- Compose:
  - [docker/local/docker-compose.yaml](docker/local/docker-compose.yaml)
  - [docker/server/docker-compose.yaml](docker/server/docker-compose.yaml)

## 项目结构

- `src/llm_router`：应用代码，包含 API、核心配置、领域模型、服务和后台模板
- `docs/architecture`：架构与设计说明
- `docker/local`：SQLite 本地部署 Compose 和本地数据目录
- `docker/server`：MySQL 部署 Compose、初始化 SQL 和运行数据目录
- `tests`：基础测试

## 本地启动

1. 安装依赖

```bash
uv sync
```

2. 复制环境变量

```bash
cp .env.example .env
```

3. 初始化管理员

```bash
uv run llm-router init-admin --username admin --password your-password
```

4. 启动服务

```bash
uv run uvicorn llm_router.main:app --reload
```

也可以直接运行模块，适合本地打断点调试：

```bash
uv run python -m llm_router.main
```

5. 打开后台

- [http://127.0.0.1:8000/admin/login](http://127.0.0.1:8000/admin/login)

## Docker

本地 SQLite 模式：

```bash
cd docker/local
docker compose up --build
```

MySQL server 模式：

```bash
cd docker/server
docker compose up --build
```

## 当前实现说明

- 流式响应第一版已支持 OpenAI SSE 与 Anthropic streaming 透传
- OpenAI 流式请求会自动附加 `stream_options.include_usage=true` 以便获取 usage
- Anthropic 流式 usage 依赖上游事件中返回的 usage 字段
- 当前路由选择按优先级进行，并过滤协议类型
- Provider 单价字段使用“每百万 token 单价”，费用按 `tokens / 1_000_000 * price` 计算
- 后台已支持手动充值、Provider 单价查看、请求日志筛选和最近账本/日报汇总查看

## Debug

- 命令行调试：`uv run python -m llm_router.main`
- 断点调试时，入口函数是 [main.py](/Users/liuzhenghua/Projects/llm-router/src/llm_router/main.py:1) 里的 `main()`
- 如果你想要自动重载，继续用 `uv run uvicorn llm_router.main:app --reload`
