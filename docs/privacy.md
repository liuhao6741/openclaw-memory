# 隐私保护

隐私保护模块通过正则模式匹配，在写入管道中自动检测和过滤敏感信息。

## 架构

```
用户输入内容
  │
  ▼
┌───────────────────────┐
│    PrivacyFilter       │
│                       │
│  contains_sensitive() │ ── 检测：是否包含敏感信息
│  redact()             │ ── 脱敏：替换为占位符
│                       │
│  patterns: list[str]  │ ── 正则模式列表
└───────────────────────┘
```

## PrivacyFilter 类

> 文件：`privacy.py`

```python
class PrivacyFilter:
    def __init__(self, patterns: list[str]) -> None:
        """编译正则模式列表。"""

    def contains_sensitive(self, text: str) -> bool:
        """检测文本是否包含敏感信息。"""

    def redact(self, text: str) -> str:
        """将敏感信息替换为 [REDACTED]。"""
```

## 默认模式

| 模式 | 说明 | 示例匹配 |
|------|------|---------|
| `sk-[a-zA-Z0-9]{20,}` | OpenAI API Key | `sk-abc123def456ghi789jkl012mno` |
| `ghp_[a-zA-Z0-9]{36}` | GitHub Personal Token | `ghp_abcdefghijklmnopqrstuvwxyz1234567890` |
| `password\s*[:=]\s*\S+` | 密码赋值 | `password = my_secret_123` |
| `secret\s*[:=]\s*\S+` | 密钥赋值 | `secret: my_api_secret` |
| `192\.168\.\d+\.\d+` | 内网 IP (C 类) | `192.168.1.100` |
| `10\.\d+\.\d+\.\d+` | 内网 IP (A 类) | `10.0.0.50` |
| `localhost:\d+` | 本地服务端口 | `localhost:8080` |

## 在写入管道中的位置

```
quality_gate()
  │
  ├── 长度检查
  ├── 填充词检查
  ├── 代码/路径检查
  ├── 推测性检查
  └── privacy_filter.contains_sensitive()  ← 这里
      │
      ├── True  → 拒绝写入，返回原因 "contains sensitive information"
      └── False → 继续后续管道
```

**注意**：隐私检查位于质量门控内部，被拒绝的内容不会进入路由或存储。

## 自定义规则

### 在项目配置中添加

```toml
# .openclaw_memory.toml

[privacy]
enabled = true
patterns = [
    'sk-[a-zA-Z0-9]{20,}',           # OpenAI API Key（保留默认）
    'ghp_[a-zA-Z0-9]{36}',           # GitHub Token（保留默认）
    'password\s*[:=]\s*\S+',          # 密码（保留默认）
    'CUSTOM_TOKEN_[A-Z0-9]{32}',      # 自定义 Token 格式
    'mongodb\+srv://[^\s]+',          # MongoDB 连接字符串
    'Bearer\s+[a-zA-Z0-9._-]+',      # Bearer Token
]
```

**注意**：配置文件中的 `patterns` 会完全替换默认模式，如需保留默认模式，需要一并列出。

### 禁用隐私过滤

```toml
[privacy]
enabled = false
```

## 使用方式

### 检测

```python
from openclaw_memory.privacy import PrivacyFilter

pf = PrivacyFilter(patterns=[
    r"sk-[a-zA-Z0-9]{20,}",
    r"password\s*[:=]\s*\S+",
])

pf.contains_sensitive("my key is sk-abcdefghij1234567890abcdef")
# → True

pf.contains_sensitive("user prefers dark mode")
# → False
```

### 脱敏

```python
pf.redact("key: sk-abcdefghij1234567890abcdef here")
# → "key: [REDACTED] here"

pf.redact("password = hunter2 in config")
# → "[REDACTED] in config"
```

## 常见场景

### 场景 1：Agent 尝试记录含 API Key 的内容

```
Agent: memory_log("使用 OpenAI API，key 是 sk-abc123def456...")
→ quality_gate 拒绝
→ 返回：Rejected: contains sensitive information
```

### 场景 2：Agent 记录技术决策（含密码相关但非敏感的内容）

```
Agent: memory_log("密码存储采用 bcrypt，cost factor = 12")
→ 不匹配 "password\s*[:=]\s*\S+" 模式（"密码" 是中文，且后面不是赋值）
→ 通过隐私检查
→ 正常写入 agent/decisions.md
```

### 场景 3：公网 IP 不被过滤

```
Agent: memory_log("API 部署在 8.8.8.8")
→ 不匹配 192.168.x.x 或 10.x.x.x 模式
→ 通过隐私检查
→ 正常写入
```

## 设计决策

### 为什么用正则而非 LLM？

- **确定性**：相同输入总是相同结果
- **性能**：微秒级检测，无 API 调用
- **可审计**：模式列表明确、可评审
- **零依赖**：不需要 LLM API

### 为什么默认拒绝而非脱敏？

写入管道中选择拒绝（`contains_sensitive`）而非脱敏（`redact`）：

- 脱敏后的内容（`[REDACTED]`）对记忆没有价值
- 拒绝可以提醒 Agent 不要发送敏感信息
- 避免存储 "[REDACTED]" 垃圾记忆

`redact()` 方法作为工具保留，供其他场景使用（如日志输出）。

### 为什么内网 IP 被过滤？

- 内网 IP 暴露网络拓扑
- 不同环境（开发/生产）IP 不同，记录无意义
- 公网 IP（如 CDN、DNS）不被过滤，因为通常是公开信息

### 项目配置覆盖默认模式的原因

- 允许完全自定义隐私规则
- 某些项目可能不需要默认规则
- 避免默认规则误伤合法内容
- 需要保留默认规则时手动列出，确保用户明确知道启用了哪些规则
