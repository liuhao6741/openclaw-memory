"""Ollama embedding provider."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OllamaEmbedding:
    """Embedding provider using local Ollama server."""

    model: str = "nomic-embed-text"
    dimension: int = 768
    base_url: str = "http://localhost:11434"
    _client: object = None

    def _get_client(self):
        if self._client is None:
            try:
                from ollama import AsyncClient
            except ImportError:
                raise ImportError(
                    "ollama package is required for Ollama embeddings. "
                    "Install with: pip install claw-memory[ollama]"
                )
            self._client = AsyncClient(host=self.base_url)
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts."""
        client = self._get_client()
        results: list[list[float]] = []
        for text in texts:
            response = await client.embeddings(model=self.model, prompt=text)
            results.append(response["embedding"])
        return results

    async def embed_single(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        results = await self.embed([text])
        return results[0]
