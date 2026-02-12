# 配置系统

配置系统采用多层合并策略，从默认值到环境变量逐层覆盖，支持全局配置和项目级配置并存。

## 合并优先级

```
低 ───────────────────────────────────────────── 高

内置默认值 → 全局 TOML → 项目 TOML → 环境变量
(config.py)  (~/.openclaw_    (.openclaw_      (OPENCLAW_*)
              memory/          memory.toml)
              config.toml)
```

高优先级的值会覆盖低优先级。

## 配置数据结构

> 文件：`config.py`

```python
@dataclass
class OpenClawConfig:
    """合并后的完整配置。"""
    project: ProjectMeta       # 项目元信息
    embedding: EmbeddingConfig # 嵌入模型配置
    privacy: PrivacyConfig     # 隐私过滤配置
    search: SearchConfig       # 搜索参数配置
    global_root: Path          # 全局记忆目录
    project_root: Path | None  # 项目记忆目录
```

### ProjectMeta

```python
@dataclass
class ProjectMeta:
    name: str = ""             # 项目名称
    description: str = ""      # 项目描述
```

### EmbeddingConfig

```python
@dataclass
class EmbeddingConfig:
    provider: str = "local"    # openai | ollama | local
    model: str = ""            # 模型名（空则用 Provider 默认）
    dimension: int = 0         # 向量维度（0 则用 Provider 默认）
    api_key: str = ""          # API Key（OpenAI 用）
    base_url: str = ""         # 自定义 API 地址
```

### PrivacyConfig

```python
@dataclass
class PrivacyConfig:
    enabled: bool = True       # 是否启用隐私过滤
    patterns: list[str] = [    # 正则模式列表
        r"sk-[a-zA-Z0-9]{20,}",
        r"ghp_[a-zA-Z0-9]{36}",
        r"password\s*[:=]\s*\S+",
        r"secret\s*[:=]\s*\S+",
        r"192\.168\.\d+\.\d+",
        r"10\.\d+\.\d+\.\d+",
        r"localhost:\d+",
    ]
```

### SearchConfig

```python
@dataclass
class SearchConfig:
    default_max_tokens: int = 1500     # 默认 Token 预算
    recency_half_life_days: float = 30 # 时间衰减半衰期（天）
    default_top_k: int = 10            # 默认最大返回条数
```

## 配置文件示例

### 全局配置 (`~/.openclaw_memory/config.toml`)

```toml
[embedding]
provider = "openai"
model = "text-embedding-3-small"

[privacy]
enabled = true

[search]
default_max_tokens = 2000
```

### 项目配置 (`.openclaw_memory.toml`)

```toml
[project]
name = "my-project"
description = "E-commerce platform"

[embedding]
provider = "openai"
model = "text-embedding-3-small"

[privacy]
enabled = true
patterns = [
    'sk-[a-zA-Z0-9]{20,}',
    'ghp_[a-zA-Z0-9]{36}',
    'password\s*[:=]\s*\S+',
    'CUSTOM_SECRET_[A-Z0-9]+',
]

[search]
default_max_tokens = 1500
recency_half_life_days = 30
```

## 环境变量

| 变量 | 对应配置项 | 说明 |
|------|-----------|------|
| `OPENCLAW_EMBEDDING_PROVIDER` | `embedding.provider` | 嵌入 Provider |
| `OPENCLAW_EMBEDDING_MODEL` | `embedding.model` | 模型名称 |
| `OPENCLAW_MEMORY_ROOT` | `global_root` | 覆盖全局记忆目录路径 |
| `OPENAI_API_KEY` | `embedding.api_key` | OpenAI API Key |

### 环境变量命名规则

```
OPENCLAW_{SECTION}_{FIELD}
```

例如 `OPENCLAW_SEARCH_DEFAULT_MAX_TOKENS` → `search.default_max_tokens`

## 加载流程

> 文件：`config.py::load_config()`

```
1. 初始化默认值（dataclass 默认参数）
         │
         ▼
2. 检测全局目录
   ├── OPENCLAW_MEMORY_ROOT 环境变量
   └── 或默认 ~/.openclaw_memory/
         │
         ▼
3. 加载全局 config.toml（如果存在）
   └── _deep_merge() 覆盖默认值
         │
         ▼
4. 检测项目根目录
   └── _detect_project_root()
       ├── 向上查找 .openclaw_memory.toml
       └── 或 git 仓库根目录
         │
         ▼
5. 加载项目 .openclaw_memory.toml（如果存在）
   └── _deep_merge() 覆盖全局配置
         │
         ▼
6. 应用环境变量
   └── _apply_env() 覆盖文件配置
         │
         ▼
7. 转换为类型化配置对象
   └── _dict_to_dataclass()
         │
         ▼
8. 返回 OpenClawConfig
```

## 项目根目录检测

> 文件：`config.py::_detect_project_root()`

从当前工作目录开始向上查找：

1. 查找 `.openclaw_memory.toml` 文件
2. 如未找到，查找 `.git` 目录（Git 仓库根）
3. 如都未找到，使用当前工作目录

## 深度合并

> 文件：`config.py::_deep_merge()`

递归合并两个字典，嵌套字典逐层合并而非整体替换：

```python
# 全局配置
{"embedding": {"provider": "local"}, "search": {"default_max_tokens": 2000}}

# 项目配置
{"embedding": {"provider": "openai", "model": "text-embedding-3-small"}}

# 合并结果
{
    "embedding": {
        "provider": "openai",                # 项目覆盖
        "model": "text-embedding-3-small",   # 项目新增
    },
    "search": {
        "default_max_tokens": 2000,          # 保留全局
    }
}
```

## 设计决策

### 为什么用 TOML？

- Python 生态标准（pyproject.toml）
- 比 YAML 更不易出错（无缩进陷阱）
- 比 JSON 支持注释
- 标准库 `tomllib` 支持（Python 3.11+）

### 为什么分全局和项目两层？

- 全局配置：用户偏好（如嵌入 Provider）跨项目通用
- 项目配置：项目特定的名称、描述、搜索参数
- 避免每个项目都重复配置通用选项

### 为什么环境变量优先级最高？

- 适合 CI/CD 环境
- 适合容器部署
- 适合临时覆盖（不修改文件）
- MCP 服务配置中可直接设置
