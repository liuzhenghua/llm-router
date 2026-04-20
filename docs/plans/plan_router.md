# 路由策略实现计划

## 设计原则
**代码始终实现组合逻辑，用户通过配置达到不同效果：**
- 优先级 + 权重 + 后备 始终生效
- 不需要 `routing_strategy` 字段

---

## 路由选择算法

### 核心概念
| 概念 | 说明 |
|-----|------|
| 优先级 (priority) | 按 priority 分组，数值小的先调用 |
| 权重 (weight) | 同组内按权重加权随机分配流量，weight=0 表示不参与路由 |
| 后备 (is_fallback) | is_fallback=true 的路由只在主路由全部失败时调用 |

### 路由流程
```
1. 获取所有 active 且 weight > 0 的路由
2. 按 is_fallback 分离：Main 组 vs Fallback 组
3. 组按 priority 排序：[[P=10的路由], [P=20的路由], ...]
4. 遍历各组：
   a. 组内按权重加权随机选择 1 个 provider
   b. 调用 provider
   c. 成功 → 返回
   d. 5xx 错误 → 组内最多重试 3 次
   e. 组内全部失败 → 切换下一组
5. 所有组失败 → 返回 503
```

### 加权随机选择算法
```
Input: providers = [A, B, C], weights = [3, 2, 5]
Step 1: 总权重 = 10
Step 2: 随机数 r = random(0, 10)
Step 3: 遍历累加：r < 累计权重时选择
        - r < 3  → A (0-3)
        - r < 5  → B (3-5)
        - r < 10 → C (5-10)
```

### 配置示例
| 场景 | 配置 | 效果 |
|-----|------|------|
| 加权轮询 | P1+W3, P1+W7 | 30%/70% 流量分配 |
| 优先级调用 | P1+W1, P2+W1 | 先 P1，失败才 P2 |
| 主备 | P1+W1+fallback=false, P2+W1+fallback=true | P1 为主，P2 为后备 |
| 优先级+加权 | P1[W3], P1[W7], P2[W5] | P1 组内 30%/70%，P1 失败才 P2 |

---

## 降级机制

### 触发条件与降级类型
| 条件 | 处理 | 降级类型 |
|------|------|---------|
| 429 或 403 | **立即标记** degraded | `quota_exhausted` |
| 连续 N 次 5xx/超时 | 累计失败计数，超过阈值后标记 | `unavailable` |
| 连接超时 N 次 | 同上 | `unavailable` |

### 降级流程
```
1. Provider 返回错误
   - 429/403 → 立即标记 degraded_type=quota_exhausted，跳到步骤 4
   - 5xx/超时 → 累计失败计数
2. 失败计数超过阈值（5 次）？
   - 是 → 标记为 degraded_type=unavailable
3. 更新状态：
   → 写入 DualCache（持久化，包含降级类型）
   → 内存缓存同步失效
4. 下一轮路由选择时，自动跳过 degraded 的 route
5. 按 priority 顺序尝试下一组，直到 fallback
```

### 降级状态数据结构
```python
# 使用现有的 DualCache 机制存储
# 存储 Key: route:degraded:{route_id}
# Value: {"degraded_type": "quota_exhausted|unavailable", "fail_count": 0, "last_fail_time": timestamp}

# 部署模式
- local 模式：仅内存缓存
- distributed 模式：Redis + 内存缓存（自动同步）
```

**状态流转**：
```
active → degraded (失败次数超限)
degraded → active (定时探测恢复 或 手动恢复)
```

### 降级状态数据结构
```python
# 使用现有的 DualCache 机制存储
# 存储 Key: route:degraded:{route_id}
# Value: {"status": "active|degraded", "fail_count": 0, "last_fail_time": timestamp}

# 部署模式
- local 模式：仅内存缓存
- distributed 模式：Redis + 内存缓存（自动同步）
```

---

## 实现步骤

### Step 1: 新增降级状态管理
**文件**: `src/llm_router/services/cache/degraded_cache.py` (新增)

新增 `DegradedRouteCache` 类，**使用现有的 DualCache 机制**：
```python
class DegradedRouteCache:
    # 依赖注入 DualCache，无需关心部署模式
    def __init__(self, cache: DualCache):
        self._cache = cache

    ROUTE_DEGRADED_KEY = "route:degraded:{route_id}"

    async def mark_degraded(self, route_id: int, fail_count: int) -> None
    async def recover(self, route_id: int) -> None
    async def get_status(self, route_id: int) -> RouteStatus | None
    async def increment_fail_count(self, route_id: int) -> int
    async def reset_fail_count(self, route_id: int) -> None
```

### Step 2: 修改路由选择逻辑
**文件**: `src/llm_router/services/router.py`

修改 `resolve_provider_candidates()` 函数：
1. 过滤 `status=active` 且 `weight > 0` 的路由
2. **新增**：过滤掉 degraded 状态的路由（通过 DegradedRouteCache 检查）
3. 按 `is_fallback` 和 `priority` 分组
4. 返回分组后的路由候选列表

新增 `weighted_random_select()` 函数：
- 输入：`List[RoutedProvider]` + 权重列表
- 使用加权随机算法选择

### Step 3: 修改故障切换逻辑
**文件**: `src/llm_router/services/gateway.py`

修改 `handle_proxy_request()`：
1. 按组顺序遍历（先 Main 组，再 Fallback 组）
2. 每组内加权随机选择 provider
3. 调用失败时：
   - 调用 `degraded_cache.increment_fail_count()`
   - 如果超过阈值，标记为 degraded
4. 5xx 错误：组内最多重试 3 次
5. 组内全部失败：切换下一组

### Step 4: 添加字段注释
**文件**: `src/llm_router/domain/models.py`

添加注释说明 `is_fallback`、`priority`、`weight` 的用途。

### Step 5: 新增降级恢复机制
**文件**: `src/llm_router/services/scheduler.py` (新增)

#### 5.1 定时探测恢复
```python
class DegradedRouteRecovery:
    """定时扫描 degraded 路由，尝试恢复"""

    async def scan_and_recover(self) -> None:
        """
        执行流程：
        1. 扫描所有 degraded 状态的路由
        2. 对每个 degraded 路由，根据类型选择探测方式：
           - quota_exhausted 类型：
             → 发送推理请求（max_tokens=1）验证配额是否恢复
           - unavailable 类型：
             → 发送轻量请求（如模型列表）验证连通性
        3. 成功 → 记录一次成功
        4. 失败 → 重置成功计数
        5. 连续成功 N 次（如 3 次）→ 恢复为 active
        """
```

**探测方式对照表**：
| 降级类型 | 探测方式 | 说明 |
|---------|---------|------|
| `quota_exhausted` | 推理接口（max_tokens=1） | 验证配额是否恢复 |
| `unavailable` | 轻量接口（如模型列表） | 验证连通性 |

#### 5.2 手动恢复 API
**文件**: `src/llm_router/api/admin.py`

新增恢复路由接口：
```python
@router.post("/logical-models/{logical_model_id}/routes/{route_id}/recover")
async def recover_route(logical_model_id: int, route_id: int) -> JSONResponse:
    """
    手动恢复 degraded 路由为 active
    1. 调用 DegradedRouteCache.recover(route_id)
    2. 失效内存缓存
    3. 返回成功
    """
```

#### 5.3 管理后台页面
**文件**: `src/llm_router/templates/logical_models.html`

在路由列表页面：
- 显示每条路由的 degraded 状态
- 添加"恢复"按钮（仅 degraded 状态的路由显示）

---

## 关键文件修改清单

| 文件 | 修改内容 |
|------|---------|
| `src/llm_router/services/cache/degraded_cache.py` | **新增** 降级状态管理 |
| `src/llm_router/services/router.py` | 修改路由分组逻辑，添加加权随机选择算法 |
| `src/llm_router/services/gateway.py` | 修改故障切换逻辑，支持降级标记 |
| `src/llm_router/services/scheduler.py` | **新增** 降级恢复定时任务 |
| `src/llm_router/domain/models.py` | 添加字段注释 |
| `src/llm_router/api/admin.py` | **新增** 手动恢复路由 API |
| `src/llm_router/templates/logical_models.html` | **修改** 显示 degraded 状态和恢复按钮 |
