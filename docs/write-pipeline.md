# 写入管道

写入管道是 OpenClaw Memory 的核心差异化设计之一。V1 版本完全用规则替代 LLM，实现零 LLM 依赖的智能写入。

## 管道总览

```
输入内容 (content + type?)
  │
  ▼
┌─────────────┐
│ Quality Gate │ ── 拒绝噪声、填充词、纯代码、推测性内容
└──────┬──────┘
       │ 通过
       ▼
┌─────────────┐
│Privacy Filter│ ── 拒绝含 API 密钥、密码、内网 IP 等的内容
└──────┬──────┘
       │ 通过
       ▼
┌─────────────┐
│ Smart Router │ ── 按关键词模式分类，确定目标文件和 scope
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Embedding   │ ── 生成内容嵌入向量
└──────┬──────┘
       │
       ▼
┌──────────────────┐
│ Dedup / Conflict │ ── 查找相似记忆，三种结果分支
└──────┬───────────┘
       │
       ├── score >= 0.92  ──→  强化（reinforcement++）
       ├── 0.85 ~ 0.92   ──→  冲突替换
       └── score < 0.85   ──→  追加新条目
```

## 1. Quality Gate（质量门控）

> 文件：`writer.py::quality_gate()`

质量门控的目标是过滤噪声，确保只有有价值的信息被写入记忆。

### 检查规则

| 检查项 | 规则 | 示例（被拒绝） |
|--------|------|----------------|
| 最小长度 | CJK 内容 >= 10 字符，ASCII 内容 >= 20 字符 | `"OK"`, `"好的"` |
| 填充词 | 匹配 `_FILLER_PATTERNS` 正则列表 | `"我来帮你看看"`, `"Sure, let me"` |
| 纯代码/路径 | 匹配 `_CODE_PATTERNS` 正则列表 | `"/usr/bin/python"`, `"import os"` |
| 推测性内容 | 以 `_SPECULATIVE_PREFIXES` 开头 | `"可能是..."`, `"Maybe..."` |
| 敏感信息 | `PrivacyFilter.contains_sensitive()` | 含 `sk-xxx` 或 `password = xxx` |

### 填充词模式 (`_FILLER_PATTERNS`)

```python
_FILLER_PATTERNS = [
    r"^(好的|OK|sure|got it|understood|I see|let me|我来|让我)",
    r"^(I'll |I will |我会|我将)",
    r"^(Here'?s? |This is |这是)",
    # ...
]
```

### 代码/路径模式 (`_CODE_PATTERNS`)

```python
_CODE_PATTERNS = [
    r"^[/\\.][\w/\\.-]+$",         # 纯路径
    r"^(import |from .+ import )",  # Python 导入
    r"^[\[({]",                     # 括号开头（JSON/数组等）
    # ...
]
```

### 推测性前缀 (`_SPECULATIVE_PREFIXES`)

```python
_SPECULATIVE_PREFIXES = [
    "可能", "也许", "或许", "大概",
    "maybe", "perhaps", "possibly", "probably",
    "I think", "I guess", "not sure",
]
```

### 返回值

```python
@dataclass
class GateResult:
    passed: bool          # 是否通过
    reason: str           # 拒绝原因（如果被拒）
    content: str          # 处理后的内容
```

## 2. Privacy Filter（隐私过滤）

> 详见 [privacy.md](privacy.md)

质量门控阶段同时调用隐私过滤器，拒绝包含敏感信息的内容。

## 3. Smart Router（智能路由）

> 文件：`writer.py::route_content()`

路由器根据内容中的关键词匹配，自动将内容分类到正确的目标文件。

### 路由规则（按优先级排列）

| 优先级 | 类型 | 关键词模式 | 目标文件 | scope | 重要性 |
|--------|------|-----------|----------|-------|--------|
| 1 | instruction | `必须\|不要\|always\|never\|rule\|规则` | `user/instructions.md` | global | 5 |
| 2 | decision | `决定\|采用\|decided\|chose\|选择.*方案` | `agent/decisions.md` | project | 5 |
| 3 | pattern | `发现\|模式\|pattern\|solution\|解决` | `agent/patterns.md` | project | 3 |
| 4 | preference | `偏好\|prefer\|like\|喜欢` | `user/preferences.md` | global | 4 |
| 5 | entity | `[\u4e00-\u9fff]{2,4}(是\|担任)` 或 `[A-Z][a-z]+ (is\|role)` | `user/entities.md` | global | 3 |
| 6 | default | 无匹配 | `journal/YYYY-MM-DD.md` | project | 1 |

### 路由结果

```python
@dataclass
class RouteResult:
    target_file: str      # 目标文件相对路径
    scope: str            # "global" 或 "project"
    memory_type: str      # instruction/decision/pattern/preference/entity/journal
    importance: int       # 1-5
    section: str          # Markdown 标题（如 "## Instructions"）
```

### 类型覆盖

如果调用时指定了 `type` 参数，会优先使用指定类型（`_route_by_type()`），跳过关键词匹配。

## 4. 去重与冲突检测

> 文件：`writer.py::smart_write()`

### 流程

1. **生成嵌入**：对内容调用 `embed_single()` 获取向量
2. **查找相似**：在对应 scope 的 store 中调用 `find_similar(embedding, threshold=0.85)`
3. **三种分支**：

#### 分支 A：强化 (score >= 0.92)

```
已有：用户偏好使用 dark mode
新增：用户喜欢 dark mode
→ 相似度 0.95 → 对已有条目执行 reinforcement++
```

操作：
- `store.increment_reinforcement(chunk_id)` — 数据库强化计数 +1
- `_increment_reinforcement_in_file()` — 更新文件中对应条目的 frontmatter `reinforcement` 值

#### 分支 B：冲突替换 (0.85 <= score < 0.92)

```
已有：偏好使用 Vim 编辑器
新增：偏好使用 Neovim 编辑器
→ 相似度 0.88 → 用新内容替换旧条目
```

操作：
- `_replace_in_file()` — 在 Markdown 文件中定位旧的列表项并替换

#### 分支 C：追加 (score < 0.85)

```
已有：偏好使用 dark mode
新增：项目采用 monorepo 结构
→ 相似度 0.42 → 追加为新条目
```

操作：
- `_append_to_file()` — 追加到目标文件的合适位置

### 文件操作细节

#### `_append_to_file()`

1. 读取现有文件（或创建新文件）
2. 解析 frontmatter
3. 在正确的 section 下追加 `- {content}` 列表项
4. 更新 frontmatter 的 `updated` 时间戳
5. 写回文件

#### `_replace_in_file()`

1. 读取文件内容
2. 查找与旧内容最匹配的列表项（`- ` 开头的行）
3. 替换为新内容
4. 写回文件

#### `_increment_reinforcement_in_file()`

1. 读取文件 frontmatter
2. `reinforcement` 值 +1
3. 写回文件

## 5. 写入后索引

写入成功后，调用 `index_file()` 对修改的文件重新索引，确保向量库与 Markdown 文件保持同步。

## 设计决策

### 为什么用规则而非 LLM？

1. **零 LLM 依赖**：V1 的核心目标，降低使用门槛
2. **确定性**：规则行为可预测、可测试
3. **性能**：无 API 调用延迟
4. **成本**：零额外费用

### 为什么用 0.92/0.85 阈值？

- **0.92**：经验值，高于此阈值的通常是语义相同的内容（同义表述）
- **0.85**：相关但不完全相同，可能是更新/修正
- **< 0.85**：不同的记忆条目

### 为什么写入 Markdown 列表项？

- 人类可读、可编辑
- Git 友好（每条记忆一行，diff 清晰）
- 结构化足够，便于程序解析
