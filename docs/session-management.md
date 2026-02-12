# 会话管理

会话管理模块负责 Primer 构建（冷启动上下文）、日志写入（会话摘要）和任务追踪。

## 概览

```
┌─────────────────────────────────────────┐
│           Session Lifecycle              │
│                                         │
│  会话开始 ──→ memory_primer()            │
│              返回结构化上下文             │
│                │                         │
│                ▼                         │
│  会话进行 ──→ memory_log()               │
│              memory_search()             │
│              memory_update_tasks()       │
│                │                         │
│                ▼                         │
│  会话结束 ──→ memory_session_end()       │
│              写入日志 + 更新 Primer       │
└─────────────────────────────────────────┘
```

## 1. Primer（会话引导）

> 文件：`primer.py::build_primer()`  
> 工具：`memory_primer()`

Primer 在每个会话开始时提供约 500-1000 tokens 的结构化上下文，帮助 Agent 快速了解用户和项目状态。

### 模板结构

```markdown
## 用户身份
{entities}

## 项目概况
{project_name} — {project_description}

## 关键偏好
{preferences}

## 近期上下文（最近 3 天）
{recent_context}

## 进行中任务
{tasks}
```

### 数据来源

| 区块 | 数据来源 | 提取方式 |
|------|----------|---------|
| 用户身份 | `~/.openclaw_memory/user/entities.md` | 提取全部列表项 |
| 项目概况 | `.openclaw_memory.toml` 的 `[project]` | 直接读取配置 |
| 关键偏好 | `~/.openclaw_memory/user/preferences.md` | 提取最近 5 个列表项 |
| 近期上下文 | `journal/YYYY-MM-DD.md` (最近 3 天) | 提取 "完成了什么" section |
| 进行中任务 | `.openclaw_memory/TASKS.md` | 直接读取 |

### 提取逻辑

#### `_extract_items(content, max_items)`

从 Markdown 内容中提取列表项（`- ` 开头的行），返回最后 `max_items` 条。

#### `_extract_recent_completed(journal_dir, days)`

1. 扫描 `journal/` 目录
2. 按文件名（`YYYY-MM-DD.md`）筛选最近 N 天
3. 从每个文件中提取 `### 完成了什么` section 下的列表项
4. 合并返回

### 自动写入

`write_primer()` 在以下时机自动更新 `PRIMER.md`：

- `memory_session_end()` 调用后
- 任务更新后

## 2. Session Journal（会话日志）

> 文件：`primer.py::write_session_to_journal()`  
> 工具：`memory_session_end(summary)`

### 日志文件

每天一个文件：`journal/YYYY-MM-DD.md`

### 文件结构

```markdown
---
date: 2026-02-12
sessions: 2
updated: 2026-02-12
---

## Session 14:30

### 请求
用户要求实现用户认证功能

### 学到了什么
- JWT + refresh token 模式：15min access / 7day refresh
- Token 黑名单存储在 Redis

### 完成了什么
- 实现了登录端点
- 添加了 token 刷新逻辑

### 下一步
- 实现注销功能
- 添加角色权限

---

## Session 16:45

### 请求
...
```

### 写入流程

1. 解析 `summary` JSON：

```python
{
    "request": "用户要求实现认证功能",
    "learned": ["JWT 需要 refresh token", "Redis 用于黑名单"],
    "completed": ["实现登录端点", "添加 token 刷新"],
    "next_steps": ["实现注销功能"]
}
```

2. 读取当天日志文件（不存在则创建）
3. 更新 frontmatter：`sessions++`，`updated=today`
4. 追加 `## Session HH:MM` 区块
5. 调用 `write_primer()` 更新 Primer

### Frontmatter 管理

使用 `python-frontmatter` 库解析和写入 YAML frontmatter：

```python
import frontmatter

post = frontmatter.load(journal_path)
post.metadata["sessions"] = post.metadata.get("sessions", 0) + 1
post.metadata["updated"] = today
post.content += session_block
frontmatter.dump(post, journal_path)
```

## 3. Task Management（任务管理）

> 文件：`primer.py::write_tasks()`  
> 工具：`memory_update_tasks(tasks_json)`

### TASKS.md 格式

```markdown
---
updated: 2026-02-12
---

- [ ] 实现用户注册
  - 进展：数据库模型已设计
  - 下一步：实现 API 端点
  - 相关文件：src/models/user.py
- [x] 实现用户登录
- [ ] 添加角色权限
```

### 输入格式

```json
[
  {
    "task": "实现用户注册",
    "status": "in_progress",
    "progress": "数据库模型已设计",
    "next_step": "实现 API 端点",
    "related_files": ["src/models/user.py"]
  },
  {
    "task": "实现用户登录",
    "status": "done"
  }
]
```

### 写入逻辑

1. 解析 `tasks_json` 为任务列表
2. 每个任务转换为 Markdown checkbox 格式：
   - `status == "done"` → `- [x] task`
   - 其他 → `- [ ] task` + 子项
3. 写入 `TASKS.md`（整体覆盖）
4. 调用 `write_primer()` 更新 Primer

## 文件变更与索引

### Watcher 排除

`PRIMER.md` 和 `TASKS.md` 被 watcher 排除，不触发自动索引：

- 这些文件是自动生成的
- 频繁更新会导致不必要的重索引
- Primer 和 Tasks 通过专用工具直接读取

### Journal 索引

`journal/*.md` 被 watcher 监控并自动索引：

- 会话日志是长期记忆的重要来源
- 支持语义搜索历史会话

## 设计决策

### 为什么需要 Primer？

- **冷启动问题**：新会话中 Agent 对用户一无所知
- **500-1000 tokens**：足够建立上下文，但不浪费 context window
- **结构化**：比 "搜索所有记忆" 更高效、更有针对性

### 为什么 Journal 按天分文件？

- 文件大小可控
- 按日期浏览直观
- Git diff 友好
- 自然的时间线组织

### 为什么 Session Summary 用 JSON 而非自由文本？

- 结构化输入确保信息完整
- 便于程序解析和路由
- Agent 容易生成正确格式
- 可以分别索引 "学到了什么" 和 "完成了什么"
