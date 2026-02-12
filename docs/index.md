# OpenClaw Memory 设计文档

> 本目录包含 OpenClaw Memory 项目的完整技术设计方案。

## 文档目录

| 文档 | 内容 |
|------|------|
| [architecture.md](architecture.md) | 整体架构、模块关系、核心数据流 |
| [write-pipeline.md](write-pipeline.md) | 写入管道：质量门控 → 隐私过滤 → 智能路由 → 去重/冲突 |
| [retrieval-pipeline.md](retrieval-pipeline.md) | 检索管道：混合搜索、显著性评分、Token 预算、快速路径 |
| [storage.md](storage.md) | SQLite 存储层：表结构、向量索引、全文搜索、去重机制 |
| [embedding.md](embedding.md) | 嵌入抽象层：Protocol 设计、三种 Provider、扩展指南 |
| [configuration.md](configuration.md) | 配置系统：多层合并、环境变量、配置项参考 |
| [session-management.md](session-management.md) | 会话管理：Primer 构建、日志写入、任务追踪 |
| [privacy.md](privacy.md) | 隐私保护：正则模式、过滤流程、自定义规则 |
| [cursor-usage-guide.md](cursor-usage-guide.md) | **Cursor 使用用例**：完整场景演示、工具速查表、搜索技巧 |

## 架构概览

```
┌───────────────────────────────────────────────────────┐
│                    MCP Server (server.py)              │
│  memory_primer / memory_search / memory_log / ...      │
├─────────┬─────────┬──────────┬────────────────────────┤
│ Writer  │Retriever│  Primer  │       Indexer           │
│         │         │          │         ↑               │
│ quality │ hybrid  │ build    │       Watcher           │
│ gate    │ search  │ primer   │   (file changes)        │
│ privacy │salience │ session  │                         │
│ router  │ budget  │ tasks    │                         │
├─────────┴────┬────┴──────────┴────────────────────────┤
│              │                                         │
│          VectorStore (store.py)                         │
│    SQLite + sqlite-vec + FTS5                          │
├──────────────┴─────────────────────────────────────────┤
│           Embedding Provider (embeddings/)              │
│      OpenAI  |  Ollama  |  Local (sentence-transformers)│
├────────────────────────────────────────────────────────┤
│                Markdown Files (source of truth)         │
│   ~/.openclaw_memory/user/    <project>/.openclaw_memory/│
└────────────────────────────────────────────────────────┘
```

## 核心设计原则

1. **Markdown 为唯一数据源** — 所有记忆以人类可读的 Markdown 文件存储，SQLite 仅为索引/缓存
2. **零外部服务依赖** — 纯 Python + SQLite，嵌入模型支持纯本地运行
3. **V1 零 LLM 依赖** — 仅需嵌入模型，写入管道用规则替代 LLM
4. **项目隔离** — 全局用户记忆 + 项目工作记忆，互不干扰
5. **数据驱动重要性** — 强化计数 + 访问频率 + 时间衰减，自动排序
