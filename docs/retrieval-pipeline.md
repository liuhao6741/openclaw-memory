# 检索管道

检索管道实现了多维度的显著性评分和 Token 预算控制，确保返回最相关的记忆且不超出上下文窗口限制。

## 检索总览

```
query + scope? + max_tokens?
  │
  ▼
┌──────────────┐
│  Fast Path   │ ── 关键词匹配 → 直接读文件（命中则返回）
└──────┬───────┘
       │ 未命中
       ▼
┌──────────────┐
│Timeline Path │ ── "最近/today" → 读近 7 天日志（命中则返回）
└──────┬───────┘
       │ 未命中
       ▼
┌──────────────┐
│ Hybrid Search│ ── 向量搜索 + 全文搜索 + RRF 融合
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   Salience   │ ── 多维度评分（语义 + 强化 + 时间 + 访问）
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Token Budget │ ── 累加截断，不超出预算
└──────┬───────┘
       │
       ▼
返回结果 + 元信息
```

## 1. Fast Path（快速路径）

> 文件：`retriever.py::_try_fast_path()`

对于明确指向特定记忆文件的查询，跳过搜索直接读取文件，零搜索开销。

### 路由规则

| 查询模式 | 目标文件 | scope |
|----------|----------|-------|
| `偏好\|preference` | `user/preferences.md` | global |
| `指令\|instruction\|规则\|rule` | `user/instructions.md` | global |
| `任务\|task` | `TASKS.md` | project |
| `实体\|entity\|人物\|people` | `user/entities.md` | global |
| `决策\|decision` | `agent/decisions.md` | project |
| `模式\|pattern` | `agent/patterns.md` | project |

### 返回结构

Fast path 命中时，返回单个 `SearchResult`，包含完整文件内容和 token 计数。

## 2. Timeline Path（时间线路径）

> 文件：`retriever.py::_try_timeline_path()`

对于时间相关的查询，直接读取最近的日志文件。

### 触发条件

查询包含：`最近|recent|today|昨天|yesterday|past \d+ days|这几天`

### 行为

1. 扫描 `journal/` 目录，按日期倒序排列
2. 读取最近 7 天的日志文件
3. 在 Token 预算内累加返回
4. 不触发向量搜索

## 3. Hybrid Search（混合搜索）

> 文件：`retriever.py::_hybrid_search()`

当快速路径和时间线路径都未命中时，执行完整的混合搜索。

### 3.1 向量搜索

```python
vector_results = store.vector_search(
    embedding=query_embedding,
    top_k=top_k * 2,        # 取 2 倍候选
    scope=scope              # global / project / None(两者都搜)
)
```

- 使用 sqlite-vec 执行 KNN 搜索
- 返回余弦相似度分数

### 3.2 全文搜索 (FTS)

```python
fts_results = store.fts_search(
    query=query,
    top_k=top_k * 2,
    scope=scope
)
```

- 使用 SQLite FTS5 执行全文匹配
- 返回 BM25 相关性分数

### 3.3 RRF 融合

> 文件：`retriever.py::_rrf_merge()`

使用 Reciprocal Rank Fusion (RRF) 合并两组结果：

```
rrf_score(doc) = Σ 1 / (k + rank_i + 1)
```

- `k = 60`（标准 RRF 参数）
- `rank_i` 是文档在第 i 个排序列表中的排名（0-indexed）
- 文档在多个列表中出现则累加分数

**示例**：

```
向量搜索：[A(rank=0), B(rank=1), C(rank=2)]
FTS搜索：  [B(rank=0), D(rank=1), A(rank=2)]

A 的 RRF = 1/(60+0+1) + 1/(60+2+1) = 0.0164 + 0.0159 = 0.0323
B 的 RRF = 1/(60+1+1) + 1/(60+0+1) = 0.0161 + 0.0164 = 0.0325
C 的 RRF = 1/(60+2+1) = 0.0159
D 的 RRF = 1/(60+1+1) = 0.0161

融合排序：B > A > D > C
```

## 4. Salience Scoring（显著性评分）

> 文件：`retriever.py::compute_salience()`

### 公式

```
salience = 0.50 × semantic_similarity
         + 0.20 × reinforcement_score
         + 0.20 × recency_decay
         + 0.10 × access_frequency
```

### 各维度计算

#### 语义相似度 (0.50)

直接使用向量搜索/RRF 返回的分数（已归一化到 0-1）。

#### 强化得分 (0.20)

```python
reinforcement_score = log(reinforcement + 1) / log(max_reinforcement + 2)
```

- `reinforcement`：该条目被多次提及（去重）后的累计次数
- `max_reinforcement`：当前结果集中的最大强化次数
- 对数归一化避免极端值主导

**意义**：被频繁提到的记忆更重要。

#### 时间衰减 (0.20)

```python
days_old = (now - updated_at).total_seconds() / 86400
decay_lambda = log(2) / half_life_days    # 默认 half_life = 30 天
recency_decay = exp(-decay_lambda × days_old)
```

| 天数 | 衰减值 |
|------|--------|
| 0 天 | 1.000 |
| 7 天 | 0.851 |
| 15 天 | 0.707 |
| 30 天 | 0.500 |
| 60 天 | 0.250 |
| 90 天 | 0.125 |

**意义**：近期更新的记忆更相关。

#### 访问频率 (0.10)

```python
access_score = log(access_count + 1) / log(max_access + 2)
```

- `access_count`：该条目被检索命中的累计次数
- 每次搜索结果返回时自动 +1

**意义**：被频繁检索的记忆可能持续重要。

### 权重选择理由

| 维度 | 权重 | 理由 |
|------|------|------|
| 语义相似度 | 0.50 | 核心指标，确保结果语义相关 |
| 强化次数 | 0.20 | 反映社区/用户认可的重要性 |
| 时间衰减 | 0.20 | 保证近期记忆优先 |
| 访问频率 | 0.10 | 辅助信号，避免过度依赖 |

## 5. Token Budget（Token 预算）

### 工作方式

```python
budget = max_tokens or self.default_max_tokens    # 默认 1500

total_tokens = 0
results = []
for result in sorted_by_salience:
    if total_tokens + result.token_count > budget:
        break
    results.append(result)
    total_tokens += result.token_count
```

### 返回结构

```python
@dataclass
class SearchResponse:
    results: list[SearchResult]     # 命中的记忆条目
    total_tokens: int               # 已用 tokens
    budget_remaining: int           # 剩余预算
```

### 设计考虑

- 默认 1500 tokens，足以提供丰富上下文但不过度消耗上下文窗口
- Agent 可按需调整（小问题用 500，复杂推理用 3000）
- 显著性排序确保在有限预算下优先返回最重要的记忆

## 6. 搜索结果格式

```python
@dataclass
class SearchResult:
    chunk_id: str           # chunk 唯一标识
    uri: str                # 来源文件路径
    content: str            # chunk 内容
    score: float            # 最终 salience 分数
    memory_type: str        # 记忆类型
    section: str            # 所属 section
    reinforcement: int      # 强化次数
    token_count: int        # token 数量
```

## 设计决策

### 为什么用混合搜索（向量 + FTS）？

- **向量搜索**擅长语义匹配（"偏好 dark mode" 能匹配 "喜欢深色主题"）
- **FTS** 擅长精确关键词匹配（人名、技术术语）
- **RRF 融合**综合两者优势，无需手动调权

### 为什么用快速路径？

- 常见查询（如 "我的偏好是什么"）不需要向量搜索
- 直接读文件比搜索索引快一个数量级
- 返回完整文件而非 chunk，信息更完整

### 为什么 Token 预算默认 1500？

- 覆盖 3-5 条记忆的典型场景
- 在常见模型 context window (8K-128K) 中占比合理
- 留足空间给用户对话和系统提示
