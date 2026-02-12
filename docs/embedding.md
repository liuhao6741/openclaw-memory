# 嵌入抽象层

嵌入抽象层通过 Python Protocol 定义统一接口，支持三种嵌入 Provider，便于扩展。

## Protocol 定义

> 文件：`embeddings/__init__.py`

```python
class EmbeddingProvider(Protocol):
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入多个文本。"""
        ...

    async def embed_single(self, text: str) -> list[float]:
        """嵌入单个文本。"""
        ...
```

### 接口说明

| 方法 | 用途 | 调用场景 |
|------|------|---------|
| `embed(texts)` | 批量嵌入 | 索引文件（分块后批量嵌入） |
| `embed_single(text)` | 单条嵌入 | 搜索查询、写入去重 |
| `dimension` | 向量维度 | 创建 sqlite-vec 表时需要 |

## 工厂函数

```python
def get_provider(config: EmbeddingConfig) -> EmbeddingProvider
```

根据 `config.provider` 返回对应的 Provider 实例：

```python
_PROVIDER_DEFAULTS = {
    "openai": {"model": "text-embedding-3-small", "dimension": 1536},
    "ollama": {"model": "nomic-embed-text", "dimension": 768},
    "local":  {"model": "all-MiniLM-L6-v2", "dimension": 384},
}
```

## 三种 Provider

### 1. OpenAI (`embeddings/openai.py`)

```python
@dataclass
class OpenAIEmbedding:
    model: str = "text-embedding-3-small"
    dimension: int = 1536
    api_key: str = ""
    base_url: str = ""
```

- 使用 `openai.AsyncOpenAI` 客户端
- 支持自定义 `base_url`（兼容 OpenAI 兼容 API）
- 延迟初始化：首次调用时创建客户端
- API Key 来源优先级：`config.api_key` → `OPENAI_API_KEY` 环境变量

**依赖**：`pip install claw-memory[openai]`

### 2. Ollama (`embeddings/ollama.py`)

```python
@dataclass
class OllamaEmbedding:
    model: str = "nomic-embed-text"
    dimension: int = 768
    host: str = ""
```

- 使用 `ollama.AsyncClient`
- 默认连接 `localhost:11434`
- 支持自定义 `host`

**依赖**：`pip install claw-memory[ollama]`

### 3. Local (`embeddings/local.py`)

```python
@dataclass
class LocalEmbedding:
    model: str = "all-MiniLM-L6-v2"
    dimension: int = 384
```

- 使用 `sentence-transformers` 库
- 纯本地运行，零网络依赖
- 使用 `asyncio.to_thread()` 包装同步调用为异步
- 首次加载模型约 2-3 秒，后续复用

**依赖**：`pip install claw-memory[local]`

## Provider 对比

| 特性 | OpenAI | Ollama | Local |
|------|--------|--------|-------|
| 维度 | 1536 | 768 | 384 |
| 精度 | 最高 | 良好 | 基础 |
| 速度 | 依赖网络 | 本地快速 | 本地快速 |
| 离线 | 否 | 是 | 是 |
| 费用 | 按量付费 | 免费 | 免费 |
| 依赖 | API Key | Ollama 服务 | 无 |
| 首次加载 | 即时 | 即时 | 2-3 秒 |

## 扩展指南

### 添加新 Provider

1. 在 `embeddings/` 下创建新文件（如 `cohere.py`）：

```python
from dataclasses import dataclass


@dataclass
class CohereEmbedding:
    model: str = "embed-english-v3.0"
    dimension: int = 1024
    api_key: str = ""

    _client = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            import cohere
            self._client = cohere.AsyncClient(api_key=self.api_key)
        response = await self._client.embed(
            texts=texts,
            model=self.model,
            input_type="search_document",
        )
        return [list(e) for e in response.embeddings]

    async def embed_single(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result[0]
```

2. 在 `embeddings/__init__.py` 中注册：

```python
_PROVIDER_DEFAULTS = {
    # ... 已有 ...
    "cohere": {"model": "embed-english-v3.0", "dimension": 1024},
}

def get_provider(config: EmbeddingConfig) -> EmbeddingProvider:
    # ... 已有 ...
    elif config.provider == "cohere":
        from .cohere import CohereEmbedding
        return CohereEmbedding(
            model=config.model or defaults["model"],
            dimension=config.dimension or defaults["dimension"],
            api_key=config.api_key,
        )
```

3. 在 `pyproject.toml` 中添加可选依赖：

```toml
[project.optional-dependencies]
cohere = ["cohere>=5.0.0"]
```

### 设计要点

- **延迟导入**：第三方库在首次使用时才导入，避免未安装时启动报错
- **延迟初始化**：客户端在首次调用时创建，减少启动时间
- **异步接口**：所有方法都是 `async`，适配 MCP 服务器的异步模型
- **缺失依赖提示**：`ImportError` 时给出明确的安装命令
