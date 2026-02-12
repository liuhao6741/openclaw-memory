"""Local embedding provider using sentence-transformers."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class LocalEmbedding:
    """Embedding provider using sentence-transformers (fully offline)."""

    model: str = "all-MiniLM-L6-v2"
    dimension: int = 384
    _st_model: object = None

    def _get_model(self):
        if self._st_model is None:
            # Force offline mode to skip slow HuggingFace Hub connectivity check.
            # The model is expected to be cached locally already.
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for local embeddings. "
                    "Install with: pip install openclaw-memory[local]"
                )

            logger.info(f"Loading local embedding model: {self.model}")
            self._st_model = SentenceTransformer(self.model)
            logger.info("Local embedding model loaded successfully")
        return self._st_model

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        """Synchronous encoding (runs in thread pool)."""
        model = self._get_model()
        embeddings = model.encode(texts, normalize_embeddings=True)
        return [emb.tolist() for emb in embeddings]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts."""
        # Run in thread pool to avoid blocking the async event loop
        return await asyncio.to_thread(self._encode_sync, texts)

    async def embed_single(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        results = await self.embed([text])
        return results[0]
