# OpenClaw Memory

[![PyPI version](https://img.shields.io/pypi/v/claw-memory.svg)](https://pypi.org/project/claw-memory/)

一个为 AI Agent 设计的轻量级 MCP 记忆服务。以 Markdown 文件作为唯一数据源，零外部依赖。

## 特性

- **Markdown 优先** — 所有记忆以人类可读的 Markdown 文件存储，对 git 友好
- **零外部依赖** — 纯 Python + SQLite，不需要外部服务
- **智能写入** — 质量门控、自动路由、冲突检测、强化计数
- **基于显著性的检索** — 多维评分：语义相似度 + 强化次数 + 时间衰减 + 访问频率
- **Token 预算感知** — 永远不超出上下文窗口预算
- **会话引导** — 用约 500 token 的结构化上下文冷启动
- **项目隔离** — 全局用户记忆 + 每个项目独立的工作记忆
- **隐私保护** — 基于正则的敏感信息过滤
- **V1 零 LLM 依赖** — 仅需要 embedding 模型（支持本地离线选项）

## 快速开始

### 1. 安装 + 初始化（两条命令搞定）

```bash
# 第一步：从 PyPI 安装（推荐）
pip install claw-memory[local]          # 本地 embedding（离线可用）
# 或 pip install claw-memory[openai]   # OpenAI embedding（更准确）
# 或 pip install claw-memory[ollama]   # Ollama embedding

# 第二步：在你的任意项目中初始化，然后重启 Cursor
cd /path/to/your/project
claw-memory init
```

**或从源码安装**（开发时使用）：

```bash
cd /path/to/claw-memory
pip install -e ".[local]"          # 本地 embedding
# 或 pip install -e ".[openai]"   # OpenAI embedding
```

> **macOS 注意**：如果遇到 `'sqlite3.Connection' object has no attribute 'enable_load_extension'` 错误，说明你的 Python 编译时未启用 SQLite 扩展加载。pyenv 用户可通过以下命令修复：
> ```bash
> LDFLAGS="-L$(brew --prefix sqlite3)/lib" CPPFLAGS="-I$(brew --prefix sqlite3)/include" \
> PYTHON_CONFIGURE_OPTS="--enable-loadable-sqlite-extensions" pyenv install <version> --force
> ```
> 详见 [故障排查](docs/cursor-usage-guide.md#sqlite-扩展加载问题)。

**`init` 命令会自动完成所有配置：**

- 创建 `~/.openclaw_memory/user/` 全局记忆目录和模板文件
- 创建 `.openclaw_memory/` 项目记忆目录（journal、agent 等）
- 创建 `.openclaw_memory.toml` 项目配置（自动检测 embedding provider）
- 创建 `.cursor/mcp.json` MCP 服务配置
- 创建 `.cursor/rules/memory.mdc` Agent 使用指南
- 创建 `.openclaw_memory/.gitignore`（索引文件不入库）

可选参数：

```bash
# 指定 embedding provider
claw-memory init --provider openai

# 指定项目名称
claw-memory init --name "my-awesome-project"

# 仅初始化全局记忆（不创建项目级文件）
claw-memory init --global-only
```

**初始化完成后，重启 Cursor 即可使用。** Agent 会自动调用记忆工具。

### 其他命令

```bash
# 启动 MCP 服务（通常由 Cursor 自动调用，无需手动运行）
claw-memory serve

# SSE 模式（用于 Web 客户端）
claw-memory serve --transport sse --port 8765

# 一次性索引已有记忆文件
claw-memory index
```

### 手动配置（可选）

如果你不想用 `init` 命令，也可以手动创建 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "claw-memory": {
      "command": "python",
      "args": ["-m", "openclaw_memory"],
      "env": {
        "OPENCLAW_EMBEDDING_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

## MCP 工具

| 工具 | 使用时机 | 说明 |
|------|----------|------|
| `memory_primer()` | 每次会话开始时 | 返回结构化上下文：用户身份、项目信息、偏好、近期活动、活跃任务 |
| `memory_search(query, scope?, max_tokens?)` | 需要回忆特定信息时 | 基于显著性评分的语义搜索，支持 token 预算控制 |
| `memory_log(content, type?)` | 发现值得记住的信息时 | 自动分类、去重、冲突检测，路由到正确的文件 |
| `memory_session_end(summary)` | 会话结束时 | 写入结构化会话摘要，更新任务和引导文件 |
| `memory_update_tasks(tasks_json)` | 任务状态变更时 | 更新 TASKS.md 和引导文件 |
| `memory_read(path)` | 需要查看完整文件内容时 | 读取并返回完整的 Markdown 文件 |

> **详细使用用例**：请参阅 [Cursor 使用用例指南](docs/cursor-usage-guide.md)，通过一个完整的多会话场景演示 Agent 如何在实践中使用每个工具。

## 工作原理

### 记忆目录结构

```
~/.openclaw_memory/              # 全局（跨项目）
├── config.toml                  # 全局配置
├── user/
│   ├── preferences.md           # 你的偏好
│   ├── instructions.md          # 你对 Agent 的规则
│   └── entities.md              # 人物、工具、项目
└── index.db                     # 全局向量索引

<项目>/.openclaw_memory/         # 项目级
├── .openclaw_memory.toml        # 项目配置
├── PRIMER.md                    # 自动维护的会话引导
├── TASKS.md                     # 活跃任务追踪
├── journal/YYYY-MM-DD.md        # 结构化每日会话日志
├── agent/
│   ├── patterns.md              # 可复用的解决方案模式
│   └── decisions.md             # 架构决策记录 (ADR)
└── index.db                     # 项目向量索引
```

### 智能写入流水线

```
输入 --> 质量门控 --> 隐私过滤 --> 智能路由 --> 强化/冲突检测 --> 写入
```

1. **质量门控**：过滤噪音（太短、客套话、纯代码、推测性内容）
2. **隐私过滤**：拦截 API Key、密码、内网 IP（可配置正则）
3. **智能路由**：根据关键词模式自动分类内容到正确的文件
4. **强化计数**：如果已存在高度相似的记忆（>0.92），增加强化计数而非重复存储
5. **冲突检测**：如果存在相似记忆（0.85-0.92）但信息更新了，替换旧条目

### 基于显著性的检索

```
显著性 = 0.50 * 语义相似度
       + 0.20 * 强化得分
       + 0.20 * 时间衰减
       + 0.10 * 访问频率
```

被频繁提到（高强化）、最近更新、经常被检索的记忆自然排名更高 —— 无需手动调整重要性。

### Token 预算

```python
# 返回在 1500 token 内尽可能多的相关记忆
results = memory_search("webhook handling", max_tokens=1500)
```

## 配置

### 项目配置 (`.openclaw_memory.toml`)

```toml
[project]
name = "my-project"
description = "电商平台"

[embedding]
provider = "openai"              # openai | ollama | local
model = "text-embedding-3-small" # 可选，使用 provider 默认值

[privacy]
enabled = true
patterns = [
    'sk-[a-zA-Z0-9]{20,}',
    'ghp_[a-zA-Z0-9]{36}',
    'password\s*[:=]\s*\S+',
]

[search]
default_max_tokens = 1500
recency_half_life_days = 30
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENCLAW_EMBEDDING_PROVIDER` | Embedding provider | `local` |
| `OPENCLAW_EMBEDDING_MODEL` | 模型名称 | Provider 默认值 |
| `OPENCLAW_MEMORY_ROOT` | 覆盖记忆根路径 | 自动检测 |
| `OPENAI_API_KEY` | OpenAI API 密钥 | — |

## Embedding 提供商

| 提供商 | 维度 | 依赖 | 适用场景 |
|--------|------|------|----------|
| OpenAI `text-embedding-3-small` | 1536 | API 密钥 | 最佳准确度 |
| Ollama `nomic-embed-text` | 768 | 本地 Ollama | 离线 / 隐私优先 |
| sentence-transformers `all-MiniLM-L6-v2` | 384 | 纯本地 | 零依赖 |

## 设计决策

本项目通过分析四个已有的记忆系统设计而成：

- **memsearch**：Markdown 作为数据源、内容哈希去重、混合搜索
- **OpenViking**：基于目录的组织结构、L0/L1/L2 渐进加载
- **memU**：强化计数衡量重要性、显著性评分公式
- **claude-mem**：结构化会话摘要、项目隔离、隐私标签

与以上四个项目的关键区别：

- V1 **零 LLM 依赖**（仅需 embedding 模型）
- **零外部服务依赖**（纯 Python + SQLite）
- **智能写入流水线**用规则路由替代 LLM 提取
- **强化 + 规则**混合重要性评估（数据驱动 + 启发式）
- **Token 预算感知**检索（四个项目均无此特性）

## 许可证

Apache-2.0。详见 [LICENSE](LICENSE)。
