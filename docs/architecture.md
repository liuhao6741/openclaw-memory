# 整体架构

## 模块总览

```
src/openclaw_memory/
├── __init__.py          # 版本号
├── __main__.py          # CLI 入口（serve / init / index）
├── server.py            # MCP Server，6 个工具定义
├── config.py            # 多层配置加载与合并
├── writer.py            # 写入管道（质量门 → 隐私 → 路由 → 去重）
├── retriever.py         # 检索管道（混合搜索 + 显著性评分 + Token 预算）
├── store.py             # 向量存储（SQLite + sqlite-vec + FTS5）
├── chunker.py           # Markdown 分块 + Token 计数
├── primer.py            # 会话引导（Primer / Journal / Tasks）
├── privacy.py           # 隐私过滤（正则模式匹配）
├── indexer.py           # 文件索引（分块 → 嵌入 → 入库）
├── watcher.py           # 文件监控（watchfiles 防抖触发重索引）
└── embeddings/
    ├── __init__.py      # EmbeddingProvider Protocol + 工厂函数
    ├── openai.py        # OpenAI text-embedding-3-small (1536 维)
    ├── ollama.py        # Ollama nomic-embed-text (768 维)
    └── local.py         # sentence-transformers all-MiniLM-L6-v2 (384 维)
```

## 模块依赖关系

```
__main__.py (CLI)
  │
  ├── serve ──→ server.py (MCP Server)
  │                 ├──→ config.py         加载配置
  │                 ├──→ embeddings/        获取嵌入 Provider
  │                 ├──→ store.py           向量存储
  │                 ├──→ retriever.py       检索器
  │                 ├──→ writer.py          写入管道
  │                 ├──→ primer.py          会话管理
  │                 ├──→ privacy.py         隐私过滤
  │                 ├──→ indexer.py         初始索引
  │                 └──→ watcher.py         文件监控
  │
  ├── init  ──→ 初始化目录结构 + 配置文件 + Cursor MCP 配置
  │
  └── index ──→ indexer.py (一次性索引)

writer.py
  ├──→ privacy.py          隐私检查
  ├──→ embeddings/         生成嵌入
  ├──→ store.py            查找相似项
  └──→ chunker.py          内容哈希

retriever.py
  ├──→ store.py            向量搜索 + 全文搜索
  ├──→ embeddings/         查询嵌入
  └──→ chunker.py          Token 计数

indexer.py
  ├──→ chunker.py          Markdown 分块
  ├──→ embeddings/         批量嵌入
  └──→ store.py            upsert 入库

watcher.py
  └──→ indexer.py          触发重索引
```

## MCP 工具接口

server.py 通过 `FastMCP` 暴露 6 个工具：

| 工具 | 函数签名 | 用途 |
|------|----------|------|
| `memory_primer` | `() -> str` | 会话冷启动，返回约 500-1000 tokens 的结构化上下文 |
| `memory_search` | `(query: str, scope?: str, max_tokens?: int) -> str` | 语义搜索 + 显著性评分 + Token 预算控制 |
| `memory_log` | `(content: str, type?: str) -> str` | 智能写入：自动分类、去重、冲突检测 |
| `memory_session_end` | `(summary: str) -> str` | 会话结束：写入日志、更新 Primer |
| `memory_update_tasks` | `(tasks_json: str) -> str` | 更新任务状态 |
| `memory_read` | `(path: str) -> str` | 直接读取 Markdown 文件 |

## 核心数据流

### 写入路径

```
Agent 调用 memory_log(content, type?)
  │
  ▼
quality_gate(content)          ── 拒绝噪声（太短/填充词/纯代码/推测性）
  │
  ▼
privacy_filter.contains_sensitive()  ── 拒绝含敏感信息的内容
  │
  ▼
route_content(content, type?)  ── 关键词匹配确定目标文件 + scope(global/project)
  │
  ▼
embed_single(content)          ── 生成嵌入向量
  │
  ▼
store.find_similar(embedding)  ── 查找相似记忆
  │
  ├── score >= 0.92 ──→ 强化（reinforcement++，不重复写入）
  ├── 0.85 <= score < 0.92 ──→ 冲突替换（用新内容覆盖旧条目）
  └── score < 0.85 ──→ 追加新条目到目标 Markdown 文件
  │
  ▼
index_file()                   ── 对修改的文件重新索引
```

### 检索路径

```
Agent 调用 memory_search(query, scope?, max_tokens?)
  │
  ▼
_try_fast_path(query)          ── 关键词匹配直接读文件（偏好/指令/任务等）
  │ (命中则直接返回)
  ▼
_try_timeline_path(query)      ── "最近/today" 等查询读近 7 天日志
  │ (命中则直接返回)
  ▼
_hybrid_search(query)
  ├── embed_single(query)      ── 查询向量化
  ├── vector_search(top_k*2)   ── 向量搜索
  ├── fts_search(top_k*2)      ── 全文搜索
  ├── _rrf_merge()             ── RRF 融合排序
  ├── compute_salience()       ── 计算显著性（语义 + 强化 + 时间 + 访问）
  ├── 按 salience 降序排列
  ├── Token 预算截断           ── 累加直到超出预算
  └── increment_access_count() ── 更新访问计数
  │
  ▼
格式化返回结果
```

### 索引路径

```
文件变更（创建/修改/删除）
  │
  ▼
watcher 检测到变更（防抖 1.5s）
  │
  ├── 删除 ──→ store.delete_by_uri()
  │
  └── 创建/修改 ──→ index_file()
       ├── chunk_markdown()     ── 按标题分块
       ├── embedder.embed()     ── 批量嵌入
       └── store.upsert()       ── 写入/更新 chunk
```

## 记忆目录结构

```
~/.openclaw_memory/                   # 全局（跨项目）
├── config.toml                       # 全局配置
├── user/
│   ├── preferences.md                # 用户偏好
│   ├── instructions.md               # 对 Agent 的指令
│   └── entities.md                   # 人物、工具、项目
└── index.db                          # 全局向量索引

<project>/.openclaw_memory/           # 项目级
├── .openclaw_memory.toml             # 项目配置
├── PRIMER.md                         # 自动维护的会话引导
├── TASKS.md                          # 任务追踪
├── journal/YYYY-MM-DD.md             # 每日会话日志
├── agent/
│   ├── patterns.md                   # 可复用的解决方案模式
│   └── decisions.md                  # 架构决策记录 (ADR)
└── index.db                          # 项目向量索引
```

## 延迟初始化

server.py 使用全局状态 + 延迟初始化策略：

```python
_cfg: OpenClawConfig | None = None
_store_global: VectorStore | None = None
_store_project: VectorStore | None = None
_retriever: Retriever | None = None
_embedder: EmbeddingProvider | None = None
_privacy: PrivacyFilter | None = None
```

首次调用任意工具时初始化所有组件，后续调用复用实例。同时启动 watcher 后台任务监控文件变更。
