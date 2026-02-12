# 存储层设计

存储层基于 SQLite + sqlite-vec + FTS5 构建，实现向量检索、全文搜索和结构化元数据管理。

## 架构

```
┌────────────────────────────────────────┐
│            VectorStore (store.py)       │
│                                        │
│  upsert / vector_search / fts_search   │
│  find_similar / delete_by_uri          │
│  increment_reinforcement               │
│  increment_access_count                │
├────────────────────────────────────────┤
│          SQLite Database               │
│  ┌──────────┬───────────┬───────────┐  │
│  │  chunks  │chunks_vec │chunks_fts │  │
│  │ (元数据) │ (向量索引)│ (全文索引)│  │
│  └──────────┴───────────┴───────────┘  │
└────────────────────────────────────────┘
```

## 表结构

### chunks（主表）

存储 chunk 的元数据和内容。

```sql
CREATE TABLE IF NOT EXISTS chunks (
    id            TEXT PRIMARY KEY,          -- chunk ID (SHA256[:16])
    uri           TEXT NOT NULL,             -- 源文件相对路径
    content       TEXT NOT NULL,             -- chunk 内容
    content_hash  TEXT NOT NULL,             -- 内容的 SHA-256 哈希
    parent_dir    TEXT NOT NULL,             -- 父目录（user/journal/agent）
    type          TEXT DEFAULT '',           -- 记忆类型
    section       TEXT DEFAULT '',           -- 所属 section
    importance    INTEGER DEFAULT 1,         -- 重要性 1-5
    reinforcement INTEGER DEFAULT 0,         -- 强化计数
    access_count  INTEGER DEFAULT 0,         -- 访问计数
    token_count   INTEGER DEFAULT 0,         -- token 数量
    created_at    TEXT NOT NULL,             -- ISO 时间戳
    updated_at    TEXT NOT NULL              -- ISO 时间戳
);
```

### chunks_vec（向量表）

使用 sqlite-vec 扩展存储嵌入向量。

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
    id TEXT PRIMARY KEY,
    embedding float[{dimension}]            -- 维度取决于 Provider
);
```

维度对照：
| Provider | 维度 |
|----------|------|
| OpenAI `text-embedding-3-small` | 1536 |
| Ollama `nomic-embed-text` | 768 |
| Local `all-MiniLM-L6-v2` | 384 |

### chunks_fts（全文搜索表）

使用 SQLite FTS5 提供全文搜索。

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content, uri, section,
    content=chunks,
    content_rowid=rowid
);
```

- `content=chunks`：与 chunks 表关联（外部内容表模式）
- FTS 通过触发器与主表保持同步

### 索引

```sql
CREATE INDEX idx_chunks_uri          ON chunks(uri);
CREATE INDEX idx_chunks_parent_dir   ON chunks(parent_dir);
CREATE INDEX idx_chunks_content_hash ON chunks(content_hash);
CREATE INDEX idx_chunks_type         ON chunks(type);
```

## Chunk ID 生成

```python
chunk_id = SHA256(f"{source}:{start_line}:{end_line}:{content_hash}")[:16]
```

- `source`：文件相对路径
- `start_line` / `end_line`：chunk 在文件中的行范围
- `content_hash`：内容的 SHA-256
- 截取前 16 字符作为 ID

## 核心操作

### upsert（写入/更新）

```python
async def upsert(self, record: dict, embedding: list[float]) -> None
```

1. 通过 `content_hash` 查找已有 chunk
2. 已存在 → UPDATE（更新内容、元数据、向量、时间戳）
3. 不存在 → INSERT（新建 chunk + 向量 + FTS）

更新向量的策略：先删除旧向量，再插入新向量（sqlite-vec 不支持 UPDATE）。

### vector_search（向量搜索）

```python
async def vector_search(
    self,
    embedding: list[float],
    top_k: int = 10,
    parent_dir: str | None = None,
) -> list[ChunkRecord]
```

执行 KNN 搜索：

```sql
SELECT cv.id, cv.distance
FROM chunks_vec cv
WHERE cv.embedding MATCH ?
  AND k = ?
ORDER BY cv.distance
```

- `distance` 为余弦距离
- `similarity = 1 - distance` 转换为相似度分数
- 可按 `parent_dir` 过滤 scope

### fts_search（全文搜索）

```python
async def fts_search(
    self,
    query: str,
    top_k: int = 10,
    parent_dir: str | None = None,
) -> list[ChunkRecord]
```

执行 FTS5 查询：

```sql
SELECT c.*, rank
FROM chunks_fts fts
JOIN chunks c ON c.rowid = fts.rowid
WHERE chunks_fts MATCH ?
ORDER BY rank
LIMIT ?
```

### find_similar（查找相似）

```python
async def find_similar(
    self,
    embedding: list[float],
    threshold: float = 0.85,
    parent_dir: str | None = None,
) -> list[tuple[ChunkRecord, float]]
```

- 调用 `vector_search` 获取候选
- 过滤 `similarity >= threshold` 的结果
- 返回 `(record, similarity_score)` 列表

用于写入管道的去重/冲突检测。

### 计数器操作

```python
async def increment_reinforcement(self, chunk_id: str) -> None
async def increment_access_count(self, chunk_id: str) -> None
```

- 原子 SQL UPDATE 操作
- 同时更新 `updated_at` 时间戳

### 批量删除

```python
async def delete_by_uri(self, uri: str) -> int
```

删除某个文件的所有 chunks（文件被删除或重新索引时使用）。

## FTS 同步机制

```python
def _sync_fts_for(self, chunk_id: str) -> None
```

由于 FTS5 外部内容表模式不自动同步，每次 upsert 后手动同步：

1. 删除 FTS 中旧记录
2. 从 chunks 表读取最新内容
3. 插入到 FTS

## 统计信息

```python
async def get_stats(self) -> dict
```

返回：

```python
{
    "total_chunks": int,         # 总 chunk 数
    "total_tokens": int,         # 总 token 数
    "by_type": {                 # 按类型统计
        "journal": {"chunks": 10, "tokens": 5000},
        "preference": {"chunks": 3, "tokens": 800},
        # ...
    }
}
```

## 双 Store 设计

系统维护两个独立的 VectorStore 实例：

| Store | 数据库路径 | 内容 |
|-------|-----------|------|
| `store_global` | `~/.openclaw_memory/index.db` | 用户偏好、指令、实体 |
| `store_project` | `<project>/.openclaw_memory/index.db` | 日志、决策、模式 |

检索时根据 `scope` 参数决定搜索哪个 store，或两者都搜。

## 设计决策

### 为什么用 SQLite？

- 零部署：嵌入式数据库，无需外部服务
- 事务安全：ACID 保证数据一致性
- 单文件存储：易于备份和迁移
- FTS5 内置：无需额外全文搜索引擎

### 为什么用 sqlite-vec？

- 原生 SQLite 扩展：与 FTS5 同库
- KNN 搜索性能优秀：适合万级 chunk
- 支持多种距离度量：余弦、L2、内积

### 为什么 Markdown 是 source of truth 而非 SQLite？

- 人类可读、可编辑
- Git 友好，支持协作
- SQLite 索引可随时从 Markdown 重建
- 避免数据锁定在二进制格式中
