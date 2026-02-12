"""Embedding provider factory and protocol."""

from __future__ import annotations

from typing import Protocol

from ..config import EmbeddingConfig


class EmbeddingProvider(Protocol):
    """Protocol for embedding providers."""

    @property
    def dimension(self) -> int:
        """Return the embedding dimension."""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts."""
        ...

    async def embed_single(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        ...


# ---------------------------------------------------------------------------
# Provider defaults
# ---------------------------------------------------------------------------

_PROVIDER_DEFAULTS: dict[str, tuple[str, int]] = {
    "openai": ("text-embedding-3-small", 1536),
    "ollama": ("nomic-embed-text", 768),
    "local": ("all-MiniLM-L6-v2", 384),
}


def get_provider(config: EmbeddingConfig) -> EmbeddingProvider:
    """Create an embedding provider from configuration."""
    provider_name = config.provider.lower()
    default_model, default_dim = _PROVIDER_DEFAULTS.get(provider_name, ("", 384))
    model = config.model or default_model
    dimension = config.dimension or default_dim

    if provider_name == "openai":
        from .openai import OpenAIEmbedding
        return OpenAIEmbedding(
            model=model,
            dimension=dimension,
            api_key=config.api_key,
            base_url=config.base_url or None,
        )
    elif provider_name == "ollama":
        from .ollama import OllamaEmbedding
        return OllamaEmbedding(
            model=model,
            dimension=dimension,
            base_url=config.base_url or "http://localhost:11434",
        )
    elif provider_name == "local":
        from .local import LocalEmbedding
        return LocalEmbedding(model=model, dimension=dimension)
    else:
        raise ValueError(f"Unknown embedding provider: {provider_name!r}. "
                         f"Supported: openai, ollama, local")
