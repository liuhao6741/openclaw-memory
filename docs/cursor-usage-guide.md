# Cursor 使用用例指南

本文档通过一个完整的真实场景，展示 Cursor Agent 如何使用 OpenClaw Memory 的 6 个 MCP 工具来实现跨会话记忆。

---

## 前置准备

安装并初始化：

```bash
pip install claw-memory[local]
cd /path/to/your/project
claw-memory init
# 重启 Cursor
```

初始化后，Cursor 的 `.cursor/rules/memory.mdc` 会自动注入 Agent 行为规则，Agent 在每次会话中都会遵循记忆工具的使用指南。

---

## 检查 MCP 服务是否正常启动

### 方法 1：通过 Cursor 设置面板查看

1. 打开 Cursor
2. 进入 **Settings** → **MCP**（或使用快捷键 `Cmd+Shift+P` 搜索 `MCP`）
3. 在 MCP 服务列表中找到 **claw-memory**
4. 状态显示为绿色/已连接 表示正常

如果状态为红色/错误，点击展开查看错误日志。

### 方法 2：通过终端手动启动测试

```bash
# 在项目目录下手动运行 MCP 服务器
claw-memory serve --log-level DEBUG
```

如果启动正常，会看到服务器就绪的日志输出。按 `Ctrl+C` 退出后重启 Cursor 使用自动模式即可。

### 方法 3：在 Agent 对话中验证

在 Cursor Agent 模式中直接对话：

> 请调用 memory_primer 加载上下文。

如果 Agent 成功调用并返回了结构化上下文，说明 MCP 服务正常运行。如果 Agent 提示找不到该工具，需要检查配置。

### 常见启动问题排查

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| Agent 找不到 memory 工具 | MCP 配置未生效 | 重启 Cursor，或检查 `.cursor/mcp.json` |
| MCP 状态显示红色 | Python 路径错误 | 检查 `mcp.json` 中的 `command` 路径 |
| 启动后报 `ModuleNotFoundError` | 包未安装到正确的 Python 环境 | 确认 `mcp.json` 中的 Python 与 `pip install` 使用的是同一个 |
| 报 `sqlite-vec` 错误 | 依赖缺失 | 运行 `pip install sqlite-vec` |
| OpenAI 报错 | API Key 未设置 | 在 `mcp.json` 的 `env` 中设置 `OPENAI_API_KEY` |
| `'sqlite3.Connection' object has no attribute 'enable_load_extension'` | Python 编译时未启用 SQLite 扩展加载 | 见下方 [SQLite 扩展加载问题](#sqlite-扩展加载问题) |

### SQLite 扩展加载问题

这是 macOS 上最常见的问题。`claw-memory` 依赖 `sqlite-vec` 向量扩展，该扩展需要通过 Python 的 `sqlite3.enable_load_extension()` 加载。如果你的 Python 编译时未启用该特性，就会报错：

```
Error executing tool memory_log: 'sqlite3.Connection' object has no attribute 'enable_load_extension'
```

**受影响的环境：**
- macOS 系统自带 Python（`/usr/bin/python3`）
- pyenv / asdf 编译安装但未加 SQLite 扩展标志的 Python

**检测方法：**

```bash
python3 -c "import sqlite3; c = sqlite3.connect(':memory:'); print(hasattr(c, 'enable_load_extension'))"
```

输出 `True` 表示正常，`False` 表示需要修复。

**修复方法（pyenv 用户）：**

1. 确保已安装 Homebrew 版 SQLite：

```bash
brew install sqlite3
```

2. 使用正确参数重新编译 Python（注意关键参数是 `--enable-loadable-sqlite-extensions`）：

```bash
LDFLAGS="-L$(brew --prefix sqlite3)/lib" \
CPPFLAGS="-I$(brew --prefix sqlite3)/include" \
PYTHON_CONFIGURE_OPTS="--enable-loadable-sqlite-extensions" \
pyenv install 3.11.9 --force
```

> 将 `3.11.9` 替换为你实际使用的 Python 版本。

3. 重新安装 claw-memory：

```bash
pip install claw-memory[local]
```

4. 重启 Cursor。

**修复方法（非 pyenv 用户）：**

如果你使用系统 Python 或其他方式安装的 Python，建议通过 pyenv 安装一个支持扩展加载的 Python 版本，然后在 `.cursor/mcp.json` 中指定该 Python 路径：

```json
{
  "mcpServers": {
    "claw-memory": {
      "command": "/Users/you/.pyenv/versions/3.11.9/bin/python3.11",
      "args": ["-m", "openclaw_memory"],
      "env": {
        "OPENCLAW_EMBEDDING_PROVIDER": "local"
      }
    }
  }
}
```

---

## Cursor 配置详解

`claw-memory init` 会自动生成两个配置文件，下面说明如何手动检查和修改它们。

### 1. MCP 服务配置：`.cursor/mcp.json`

这是 Cursor 发现和启动 MCP 服务的核心配置。

**文件位置**：`<你的项目>/.cursor/mcp.json`

```json
{
  "mcpServers": {
    "claw-memory": {
      "command": "python3",
      "args": ["-m", "openclaw_memory"],
      "env": {
        "OPENCLAW_EMBEDDING_PROVIDER": "local"
      }
    }
  }
}
```

#### 常用修改场景

**场景 A：切换嵌入 Provider**

```json
{
  "mcpServers": {
    "claw-memory": {
      "command": "python3",
      "args": ["-m", "openclaw_memory"],
      "env": {
        "OPENCLAW_EMBEDDING_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-your-key-here"
      }
    }
  }
}
```

**场景 B：使用虚拟环境中的 Python**

如果你使用 pyenv / venv / conda，需要指定完整的 Python 路径：

```json
{
  "mcpServers": {
    "claw-memory": {
      "command": "/Users/you/.pyenv/versions/3.12.0/bin/python",
      "args": ["-m", "openclaw_memory"],
      "env": {
        "OPENCLAW_EMBEDDING_PROVIDER": "local"
      }
    }
  }
}
```

查找当前 Python 路径：

```bash
which python3
# 或
python3 -c "import sys; print(sys.executable)"
```

**场景 C：使用 Ollama 本地嵌入**

```json
{
  "mcpServers": {
    "claw-memory": {
      "command": "python3",
      "args": ["-m", "openclaw_memory"],
      "env": {
        "OPENCLAW_EMBEDDING_PROVIDER": "ollama"
      }
    }
  }
}
```

> 确保 Ollama 正在运行（`ollama serve`），并已拉取模型（`ollama pull nomic-embed-text`）。

**场景 D：使用 SSE 传输模式**

适用于需要通过 HTTP 连接 MCP 的场景（如 Web 客户端）：

```json
{
  "mcpServers": {
    "claw-memory": {
      "command": "python3",
      "args": ["-m", "openclaw_memory", "serve", "--transport", "sse", "--port", "8765"],
      "env": {
        "OPENCLAW_EMBEDDING_PROVIDER": "local"
      }
    }
  }
}
```

### 2. Agent 行为规则：`.cursor/rules/memory.mdc`

这个文件告诉 Cursor Agent 如何使用记忆工具。`init` 命令会自动生成，内容如下：

```markdown
---
description: OpenClaw Memory usage guide for agent
globs:
alwaysApply: true
---

## Memory System

You have access to a persistent memory system via MCP tools. Follow these rules:

1. **Session start**: Always call `memory_primer()` first to load context.
2. **During work**: Call `memory_log(content)` when you discover:
   - User preferences ("I prefer...", "Please always...")
   - Technical decisions ("Decided to use...", "Chose...")
   - Reusable patterns ("The solution is...", "Root cause was...")
   - Facts about people/projects/tools
3. **Need to recall**: Call `memory_search(query)` when you need past context.
4. **Session end**: When the user says goodbye or ends the session,
   call `memory_session_end()` with a structured summary.

Do NOT log: debug steps, code snippets, file paths, uncertain guesses.
```

#### 自定义 Agent 行为

你可以编辑这个文件来调整 Agent 的记忆行为：

**示例：让 Agent 更积极地记录**

```markdown
2. **During work**: Call `memory_log(content)` for ANY of:
   - User preferences, requirements, constraints
   - Technical decisions and their reasoning
   - Reusable patterns, root causes, solutions
   - Facts about people, projects, tools, APIs
   - Important configuration or environment details
```

**示例：会话结束时自动提醒**

```markdown
4. **Session end**: ALWAYS call `memory_session_end()` before ending,
   even if the user doesn't explicitly say goodbye.
   Summarize what was discussed, decided, and what should happen next.
```

**示例：限制搜索范围**

```markdown
3. **Need to recall**: Call `memory_search(query, scope="agent")`
   for project-specific context, or `memory_search(query, scope="user")`
   for personal preferences. Use empty scope only when unsure.
```

### 3. 项目记忆配置：`.openclaw_memory.toml`

这是 OpenClaw Memory 自身的配置文件，控制嵌入模型、隐私过滤、搜索参数等。

**文件位置**：`<你的项目>/.openclaw_memory.toml`

```toml
[project]
name = "my-project"
description = "项目描述"

[embedding]
provider = "local"                # openai | ollama | local

[privacy]
enabled = true                    # 是否启用隐私过滤

[search]
default_max_tokens = 1500         # 默认搜索 Token 预算
recency_half_life_days = 30       # 时间衰减半衰期
```

> 完整配置说明请参阅 [configuration.md](configuration.md)。

### 修改配置后的生效方式

| 配置文件 | 修改后如何生效 |
|----------|---------------|
| `.cursor/mcp.json` | **重启 Cursor** |
| `.cursor/rules/memory.mdc` | **新开会话**即生效（无需重启） |
| `.openclaw_memory.toml` | **新开会话**即生效（服务器每次启动重新加载） |

---

## 场景：开发一个电商平台

以下按时间线模拟多个会话，展示完整的记忆生命周期。

---

## 会话 1：项目启动 & 技术选型

### Step 1 — 会话开始，加载上下文

Agent 在收到第一条消息时，自动调用 `memory_primer()`。

**Agent 调用：**

```
memory_primer()
```

**返回：**

```markdown
# Instructions

（首次使用，暂无内容）

# Context

## 用户身份
（暂无记录）

## 项目概况
my-ecommerce — 电商平台项目

## 关键偏好
（暂无记录）

## 近期上下文（最近 3 天）
（暂无记录）

## 进行中任务
（暂无记录）
```

> Agent 了解到这是一个新项目，尚无历史上下文。

---

### Step 2 — 用户表达偏好，Agent 记录记忆

**用户：**
> 我偏好使用 FastAPI 而不是 Flask，前端用 Vue 3。代码风格请始终使用 type hints。

**Agent 识别到用户偏好和指令，分别调用 `memory_log()`：**

```
memory_log("用户偏好使用 FastAPI 而不是 Flask 作为后端框架")
```

**返回：**
```
Memory saved to user/preferences.md (type: preference)
```

```
memory_log("用户偏好前端使用 Vue 3")
```

**返回：**
```
Memory saved to user/preferences.md (type: preference)
```

```
memory_log("必须始终使用 Python type hints")
```

**返回：**
```
Memory saved to user/instructions.md (type: instruction)
```

> 关键词 "偏好" 触发路由到 `user/preferences.md`（全局），关键词 "必须" 触发路由到 `user/instructions.md`（全局）。

---

### Step 3 — 技术决策，Agent 记录

**用户：**
> 数据库用 PostgreSQL，ORM 用 SQLAlchemy 2.0，认证方案采用 JWT。

**Agent 记录决策：**

```
memory_log("决定使用 PostgreSQL 作为数据库，SQLAlchemy 2.0 作为 ORM")
```

**返回：**
```
Memory saved to agent/decisions.md (type: decision)
```

```
memory_log("决定采用 JWT 认证方案：15min access token + 7day refresh token")
```

**返回：**
```
Memory saved to agent/decisions.md (type: decision)
```

> 关键词 "决定" 和 "采用" 触发路由到 `agent/decisions.md`（项目级）。

---

### Step 4 — 会话结束

**用户：**
> 今天就到这里吧。

**Agent 调用 `memory_session_end()`：**

```
memory_session_end(
    request="项目初始化和技术选型",
    learned="FastAPI + Vue 3 技术栈, PostgreSQL + SQLAlchemy 2.0, JWT 认证方案",
    completed="完成技术选型, 确定项目架构",
    next_steps="搭建项目脚手架, 实现用户认证模块"
)
```

**返回：**
```
Session summary written to 2026-02-12.md. PRIMER.md and TASKS.md updated.
```

**此时 `journal/2026-02-12.md` 内容：**

```markdown
---
date: 2026-02-12
sessions: 1
updated: 2026-02-12
---

## Session 14:30

### 请求
项目初始化和技术选型

### 学到了什么
- FastAPI + Vue 3 技术栈
- PostgreSQL + SQLAlchemy 2.0
- JWT 认证方案

### 完成了什么
- 完成技术选型
- 确定项目架构

### 下一步
- 搭建项目脚手架
- 实现用户认证模块
```

**`TASKS.md` 自动更新为：**

```markdown
- [ ] 搭建项目脚手架
- [ ] 实现用户认证模块
```

---

## 会话 2：第二天继续开发

### Step 1 — 加载上下文（冷启动）

**Agent 调用：**

```
memory_primer()
```

**返回：**

```markdown
# Instructions

必须始终使用 Python type hints

# Context

## 用户身份
（暂无记录）

## 项目概况
my-ecommerce — 电商平台项目

## 关键偏好
- 用户偏好使用 FastAPI 而不是 Flask 作为后端框架
- 用户偏好前端使用 Vue 3

## 近期上下文（最近 3 天）
- 完成技术选型
- 确定项目架构

## 进行中任务
- [ ] 搭建项目脚手架
- [ ] 实现用户认证模块
```

> Agent 瞬间恢复了所有上下文：知道要用 FastAPI + type hints，知道还有两个待办任务。整个过程约 500 tokens，无搜索开销。

---

### Step 2 — 用户提出新需求，Agent 搜索记忆

**用户：**
> 帮我实现用户认证模块。

**Agent 搜索相关决策：**

```
memory_search("认证方案 JWT", scope="agent")
```

**返回：**

```
[salience: 0.92 | reinforcement: 0 | agent/decisions.md]
决定采用 JWT 认证方案：15min access token + 7day refresh token

[total tokens: 45 | budget remaining: 1455]
```

> Agent 回忆起之前的 JWT 决策细节，基于此开始编码。因为搜索内容匹配 "decisions"，走了快速路径（fast path），直接读文件。

---

### Step 3 — 开发中发现模式，Agent 记录

**Agent 在实现过程中发现了一个可复用的模式：**

```
memory_log("发现 FastAPI 依赖注入模式：使用 Depends() 注入当前用户，结合 JWT decode 实现认证中间件")
```

**返回：**
```
Memory saved to agent/patterns.md (type: pattern)
```

> 关键词 "发现" 和 "模式" 触发路由到 `agent/patterns.md`。

---

### Step 4 — 用户再次表达偏好（去重检测）

**用户：**
> 对了，我更喜欢 FastAPI，不要用 Flask。

**Agent 调用：**

```
memory_log("用户偏好 FastAPI 而非 Flask")
```

**返回：**
```
Existing memory reinforced (score=0.95) in user/preferences.md
```

> 与已有记忆 "用户偏好使用 FastAPI 而不是 Flask 作为后端框架" 相似度 0.95 >= 0.92，触发强化而非重复写入。该条目的 reinforcement 计数 +1。

---

### Step 5 — 更新任务状态

**Agent 完成认证模块后，更新任务：**

```
memory_update_tasks('[
    {"title": "搭建项目脚手架", "status": "done"},
    {"title": "实现用户认证模块", "status": "done"},
    {"title": "编写认证模块单元测试", "status": "pending", "next_step": "pytest + httpx"},
    {"title": "实现商品目录 API", "status": "pending"}
]')
```

**返回：**
```
TASKS.md updated with 4 tasks. PRIMER.md refreshed.
```

**`TASKS.md` 更新为：**

```markdown
- [x] 搭建项目脚手架
- [x] 实现用户认证模块
- [ ] 编写认证模块单元测试
  - 下一步：pytest + httpx
- [ ] 实现商品目录 API
```

---

### Step 6 — 会话结束

```
memory_session_end(
    request="实现用户认证模块",
    learned="FastAPI Depends() 依赖注入模式用于认证中间件",
    completed="用户认证模块（登录/注册/刷新 token）",
    next_steps="编写认证模块单元测试, 实现商品目录 API"
)
```

---

## 会话 3：一周后回来

### 已过去 7 天，记忆系统帮助快速恢复

**Agent 调用：**

```
memory_primer()
```

**返回：**

```markdown
# Instructions

必须始终使用 Python type hints

# Context

## 用户身份
（暂无记录）

## 项目概况
my-ecommerce — 电商平台项目

## 关键偏好
- 用户偏好使用 FastAPI 而不是 Flask 作为后端框架 (reinforcement: 2)
- 用户偏好前端使用 Vue 3

## 近期上下文（最近 3 天）
（超过 3 天，无近期活动）

## 进行中任务
- [ ] 编写认证模块单元测试
  - 下一步：pytest + httpx
- [ ] 实现商品目录 API
```

> 虽然过了 7 天，Agent 仍然知道：用户偏好、当前待办任务、要用 type hints。近期上下文为空是正常的——可以通过 `memory_search` 回忆更早的信息。

---

### 用户想回忆之前做了什么

**用户：**
> 最近这个项目做了哪些工作？

**Agent 搜索时间线：**

```
memory_search("最近完成了什么工作", scope="journal")
```

**返回：**

```
[salience: 0.78 | reinforcement: 0 | journal/2026-02-13.md]
## Session 10:00

### 完成了什么
- 用户认证模块（登录/注册/刷新 token）

### 学到了什么
- FastAPI Depends() 依赖注入模式用于认证中间件

---

[salience: 0.65 | reinforcement: 0 | journal/2026-02-12.md]
## Session 14:30

### 完成了什么
- 完成技术选型
- 确定项目架构

[total tokens: 198 | budget remaining: 1302]
```

> 查询包含 "最近"，触发时间线路径，按日期倒序返回日志摘要。

---

### 用户提出冲突变更

**用户：**
> 我改主意了，ORM 不用 SQLAlchemy 了，改用 Tortoise ORM。

**Agent 记录：**

```
memory_log("决定将 ORM 从 SQLAlchemy 2.0 更换为 Tortoise ORM")
```

**返回：**
```
Conflicting memory updated (score=0.88) in agent/decisions.md
```

> 与已有记忆 "决定使用 PostgreSQL 作为数据库，SQLAlchemy 2.0 作为 ORM" 相似度 0.88（0.85~0.92 区间），触发冲突替换。旧决策被新决策覆盖。

---

## 会话 4：读取完整文件

### 用户想查看所有偏好

**用户：**
> 帮我看看你记住了我哪些偏好。

**Agent 调用：**

```
memory_read("user/preferences.md")
```

**返回：**

```markdown
---
updated: 2026-02-13
reinforcement: 2
---

## Preferences

- 用户偏好使用 FastAPI 而不是 Flask 作为后端框架
- 用户偏好前端使用 Vue 3
```

---

### 用户想查看所有技术决策

```
memory_read("agent/decisions.md")
```

**返回：**

```markdown
---
updated: 2026-02-19
---

## Decisions

- 决定将 ORM 从 SQLAlchemy 2.0 更换为 Tortoise ORM
- 决定采用 JWT 认证方案：15min access token + 7day refresh token
```

> 注意 SQLAlchemy 的决策已被 Tortoise ORM 替换（冲突更新）。

---

## 工具使用速查表

### 什么时候调用哪个工具？

| 时机 | 工具 | 说明 |
|------|------|------|
| 会话开始 | `memory_primer()` | **每次必调**，加载上下文 |
| 用户说 "我偏好/喜欢/讨厌..." | `memory_log(content)` | 记录偏好 |
| 用户说 "请始终/不要/必须..." | `memory_log(content)` | 记录指令 |
| 做了技术决策 | `memory_log(content)` | 记录决策 |
| 发现可复用的解决方案 | `memory_log(content)` | 记录模式 |
| 了解到人物/项目/工具信息 | `memory_log(content)` | 记录实体 |
| 需要回忆某件事 | `memory_search(query)` | 语义搜索 |
| 需要完整文件内容 | `memory_read(path)` | 直接读取 |
| 完成了任务/有新任务 | `memory_update_tasks(json)` | 更新任务 |
| 用户说再见/会话结束 | `memory_session_end(...)` | 写入日志 |

### 不应该记录什么？

| 内容类型 | 示例 | 原因 |
|----------|------|------|
| 临时调试步骤 | "试一下加个 print" | 噪声，无长期价值 |
| 代码片段 | `import os; os.path.join(...)` | 质量门控拒绝纯代码 |
| 文件路径 | `/usr/local/bin/python` | 质量门控拒绝纯路径 |
| 不确定的猜测 | "可能是这个 bug..." | 推测性前缀检测拒绝 |
| 含敏感信息 | "API key 是 sk-xxx..." | 隐私过滤器拒绝 |
| 简短回应 | "好的"、"OK" | 填充词检测拒绝 |

---

## 记忆如何跨项目复用

全局记忆（`~/.openclaw_memory/user/`）跨所有项目共享：

```
项目 A 中：memory_log("用户偏好 dark mode")
  → 写入 ~/.openclaw_memory/user/preferences.md（全局）

项目 B 中：memory_primer()
  → 自动加载 "用户偏好 dark mode"（来自全局）
```

| 范围 | 存储位置 | 跨项目 | 记忆类型 |
|------|----------|--------|---------|
| global | `~/.openclaw_memory/user/` | 是 | 偏好、指令、实体 |
| project | `<project>/.openclaw_memory/` | 否 | 日志、决策、模式、任务 |

---

## 搜索技巧

### scope 参数

```
memory_search("FastAPI 认证")           # 搜索所有记忆
memory_search("FastAPI 认证", scope="agent")    # 只搜项目级记忆
memory_search("用户偏好", scope="user")          # 只搜全局用户记忆
memory_search("最近做了什么", scope="journal")   # 只搜日志
```

### max_tokens 参数

```
memory_search("项目架构", max_tokens=500)     # 简短回答
memory_search("项目架构", max_tokens=3000)    # 详细回答
```

---

## 显著性排序示例

假设搜索 "FastAPI"，返回 3 条结果：

| 记忆 | 语义 (0.50) | 强化 (0.20) | 时间 (0.20) | 访问 (0.10) | **Salience** |
|------|------------|------------|------------|------------|-------------|
| 偏好 FastAPI (reinforcement=2, 7天前, 访问3次) | 0.45 | 0.18 | 0.17 | 0.08 | **0.88** |
| JWT 认证方案 (reinforcement=0, 7天前, 访问1次) | 0.35 | 0.00 | 0.17 | 0.04 | **0.56** |
| Depends 注入模式 (reinforcement=0, 6天前, 访问0次) | 0.30 | 0.00 | 0.17 | 0.00 | **0.47** |

> "偏好 FastAPI" 因为被多次提及（reinforcement=2）和频繁检索（access=3），排名最高。
