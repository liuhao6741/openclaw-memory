"""OpenAI embedding provider."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class OpenAIEmbedding:
    """Embedding provider using OpenAI API."""

    model: str = "text-embedding-3-small"
    dimension: int = 1536
    api_key: str = ""
    base_url: str | None = None
    _client: object = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError(
                    "openai package is required for OpenAI embeddings. "
                    "Install with: pip install openclaw-memory[openai]"
                )
            api_key = self.api_key or os.environ.get("OPENAI_API_KEY", "")
            self._client = AsyncOpenAI(api_key=api_key, base_url=self.base_url)
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts."""
        client = self._get_client()
        response = await client.embeddings.create(input=texts, model=self.model)
        return [item.embedding for item in response.data]

    async def embed_single(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        results = await self.embed([text])
        return results[0]
