# OpenClaw Memory

**AI 对话结束就消失了。OpenClaw Memory 让它们永远留下来。**

每次和 AI 编程助手对话时，你们共同做出的决策、找到的解决方案、调试的过程——全部在会话结束时消失。下一次对话从零开始，你不得不重复解释上下文。

OpenClaw Memory 自动将每一轮对话完整记录到本地 Markdown 文件中，让你的 AI 对话历史可搜索、可浏览。没有云端，没有数据库——只是你项目里的纯文本文件。

## 工作原理

```
你和 AI 对话  →  每轮自动保存到 .openclaw_memory/journal/2026-02-24.md
              →  通过 MCP 工具或 Web 浏览器搜索历史对话
```

每条记录包含完整的对话内容：时间戳、使用的模型、你的输入、AI 的完整回复、以及所做的代码变更。

## 快速开始

**1. 安装**

```bash
pip install claw-memory
```

**2. 在你的项目中初始化**

```bash
cd your-project
claw-memory init
```

会自动创建：
- `.openclaw_memory/journal/` — 对话记录存储目录
- `.cursor/mcp.json` — 将 MCP 服务连接到 Cursor
- `.cursor/rules/memory.mdc` — 指导 AI 自动记录对话

**3. 重启 Cursor** — 搞定。从现在起，每一轮对话都会被自动记录。

## 搜索历史对话

AI 会自动搜索你的对话历史。只需自然地提问：

> "我们之前讨论过这个问题，当时的解决方案是什么？"
>
> "上次我们修过类似的 bug，是怎么处理的？"

Agent 会在后台调用 `memory_search()` 找到匹配的对话内容。

### 通过 Web 浏览器查看

```bash
claw-memory web
```

在浏览器中打开查看器：
- 按日期浏览对话记录
- 全文搜索所有对话
- 暗色/亮色模式切换

## 记录的内容

每一轮对话保存为 Markdown 格式：

```markdown
## 14:32 | claude-4-opus

### User

如何修复用户列表接口的 N+1 查询问题？

### Agent

问题出在 `api/users.py`，每个用户都会触发一次单独的角色查询...

### Code Changes

- `api/users.py` (modified)
- `tests/test_users.py` (modified)
```

## MCP 工具

| 工具 | 用途 |
|---|---|
| `memory_log_conversation` | 记录一轮完整的对话 |
| `memory_log_conversation_append` | 追加到最后一轮（用于长回复） |
| `memory_search` | 按关键词搜索对话历史 |

## 数据存储

所有数据以纯 Markdown 文件形式存储在 `.openclaw_memory/journal/` 中——每天一个文件。没有数据库，没有云同步。数据完全属于你。

`.openclaw_memory/` 目录会自动加入 `.gitignore`，防止对话记录被意外提交。

## 项目隔离

每个项目拥有独立的 `.openclaw_memory/` 目录。在项目 A 中搜索，不会返回项目 B 的结果。

## 许可证

Apache 2.0
