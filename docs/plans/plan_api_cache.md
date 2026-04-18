# API Key 缓存功能改造计划

## 需求概述
参考 litellm 的 dual cache 实现三层缓存架构：
- **InMemoryCache**：进程内 LRU 缓存
- **RedisCache**：跨机共享缓存（仅 server 模式）
- **PostgreSQL/MySQL**：最终数据源

**缓存内容**：ApiKey 基本信息 + LogicalModelRoute 路由表

**增量队列**：Token 费用使用增量队列 + 30s 定期刷新到 DB，使用 `spend += delta` 原子操作

**分布式锁**：Server 模式使用 Redis 分布式锁确保同一时刻只有一个 Pod 执行批量 DB 写入

---

## 实现步骤

### Phase 1: 基础缓存层
1. **新增 `services/cache/` 目录**，包含：
   - `in_memory_cache.py` - 内存 LRU 缓存（OrderedDict + asyncio.Lock）
   - `redis_cache.py` - Redis 缓存层
   - `serializer.py` - JSON 序列化（Decimal/date 处理）
   - `dual_cache.py` - 双层缓存核心类（读：内存→Redis→DB，写：内存+Redis）

2. **新增 `domain/schemas.py` 缓存数据结构**：
   - `CachedApiKey` - ApiKey 缓存数据
   - `CachedRoute` - 路由缓存数据
   - `CachedProvider` - Provider 缓存数据

3. **修改 `core/config.py`**：
   - 新增 Redis 配置项（`redis_host`, `redis_port`, `redis_password`, `cache_ttl`）

### Phase 2: 集成 Router
4. **修改 `services/router.py`**：
   - `resolve_request_context`：先查缓存，miss 时查 DB 并回填
   - `resolve_provider_candidates`：先查缓存，miss 时查 DB 并回填
   - 添加缓存失效逻辑

### Phase 3: 增量队列 + DB 刷新
5. **新增 `services/cache/spend_queue.py`**：
   - `SpendDeltaQueue`：增量队列（local 模式用 asyncio.Queue，server 模式用 Redis ZSET）

6. **新增 `services/cache/redis_lock.py`**：
   - `RedisLockManager`：Redis 分布式锁（SET NX EX + Lua 释放）

7. **新增 `services/cache/db_writer.py`**：
   - `DbSpendWriter`：30s 定期刷新，使用 `UPDATE ... SET balance = balance - delta`

### Phase 4: 集成与配置
8. **修改 `main.py`**：
   - lifespan 中初始化/清理 `DualCache` 和 `DbSpendWriter`

9. **修改 `services/billing.py`**：
   - 使用增量队列而非直接 DB 写入

10. **修改 `api/admin.py`**：
    - ApiKey/Model 变更时失效缓存

11. **修改 `docker/server/docker-compose.yaml`**：
    - 新增 Redis 服务依赖

12. **修改 `pyproject.toml`**：
    - 新增 `redis[hiredis]` 依赖

---

## 关键文件修改清单

| 文件路径 | 操作 |
|---------|------|
| `services/cache/__init__.py` | 新增 |
| `services/cache/in_memory_cache.py` | 新增 |
| `services/cache/redis_cache.py` | 新增 |
| `services/cache/dual_cache.py` | 新增 |
| `services/cache/serializer.py` | 新增 |
| `services/cache/spend_queue.py` | 新增 |
| `services/cache/db_writer.py` | 新增 |
| `services/cache/redis_lock.py` | 新增 |
| `src/llm_router/core/config.py` | 修改 |
| `src/llm_router/core/database.py` | 修改 |
| `src/llm_router/domain/schemas.py` | 修改 |
| `src/llm_router/services/router.py` | 修改 |
| `src/llm_router/services/billing.py` | 修改 |
| `src/llm_router/main.py` | 修改 |
| `src/llm_router/api/admin.py` | 修改 |
| `docker/server/docker-compose.yaml` | 修改 |
| `pyproject.toml` | 修改 |

---

## Redis Key 命名规范

```
llm_router:cache:apikey:hash:{key_hash}      # ApiKey by hash
llm_router:cache:apikey:id:{id}              # ApiKey by id
llm_router:cache:route:logical:{model_id}    # 路由列表
llm_router:cache:provider:id:{id}            # Provider
llm_router:queue:spend_delta                 # 增量队列 (ZSET)
llm_router:lock:db_writer                    # 分布式锁
```

---

## 配置项

```python
# core/config.py 新增
redis_host: str = "localhost"
redis_port: int = 6379
redis_db: int = 0
redis_password: str | None = None
cache_ttl: int = 60
```
