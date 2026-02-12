"""File indexer: scan, chunk, embed, upsert to store."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .chunker import Chunk, chunk_markdown

if TYPE_CHECKING:
    from .embeddings import EmbeddingProvider
    from .store import VectorStore

logger = logging.getLogger(__name__)


def _relative_uri(file_path: Path, root: Path) -> str:
    """Compute relative URI from root."""
    try:
        return str(file_path.relative_to(root))
    except ValueError:
        return str(file_path)


def _parent_dir(uri: str) -> str:
    """Extract parent directory from URI."""
    parts = uri.split("/")
    return parts[0] if len(parts) > 1 else ""


def _type_from_uri(uri: str) -> str:
    """Infer memory type from file URI."""
    if "preferences" in uri:
        return "preference"
    elif "instructions" in uri:
        return "instruction"
    elif "entities" in uri:
        return "entity"
    elif "decisions" in uri:
        return "decision"
    elif "patterns" in uri:
        return "pattern"
    elif "journal" in uri:
        return "event"
    return ""


def scan_markdown_files(root: Path) -> list[Path]:
    """Recursively find all .md files under root, excluding hidden dirs and special files."""
    files: list[Path] = []
    if not root.is_dir():
        return files

    for path in root.rglob("*.md"):
        # Skip hidden directories (except .openclaw itself which we're inside)
        parts = path.relative_to(root).parts
        if any(p.startswith(".") and p != ".openclaw_memory" for p in parts):
            continue
        # Skip PRIMER.md and TASKS.md (auto-generated)
        if path.name in ("PRIMER.md", "TASKS.md"):
            continue
        files.append(path)

    return sorted(files)


async def index_file(
    file_path: Path,
    root: Path,
    store: "VectorStore",
    embedder: "EmbeddingProvider",
    *,
    max_chunk_tokens: int = 500,
) -> int:
    """Index a single markdown file. Returns number of chunks upserted."""
    uri = _relative_uri(file_path, root)
    parent = _parent_dir(uri)
    inferred_type = _type_from_uri(uri)

    text = file_path.read_text(encoding="utf-8")
    if not text.strip():
        return 0

    chunks = chunk_markdown(text, source=uri, max_chunk_tokens=max_chunk_tokens)
    if not chunks:
        return 0

    # Batch embed all chunks
    texts = [c.content for c in chunks]
    embeddings = await embedder.embed(texts)

    count = 0
    for chunk, embedding in zip(chunks, embeddings):
        # Merge type: frontmatter type > inferred type
        chunk_type = chunk.metadata.get("type", "") or inferred_type
        importance = chunk.metadata.get("importance", 1)

        record = {
            "uri": uri,
            "content": chunk.content,
            "content_hash": chunk.content_hash,
            "parent_dir": parent,
            "type": chunk_type,
            "section": chunk.section,
            "importance": importance,
            "token_count": chunk.token_count,
        }
        store.upsert(chunk.chunk_id, record, embedding)
        count += 1

    logger.info(f"Indexed {count} chunks from {uri}")
    return count


async def index_directory(
    root: Path,
    store: "VectorStore",
    embedder: "EmbeddingProvider",
    *,
    max_chunk_tokens: int = 500,
) -> dict[str, int]:
    """Index all markdown files under root.

    Returns dict mapping URI → chunk count.
    """
    files = scan_markdown_files(root)
    existing_uris = store.get_all_uris()
    current_uris: set[str] = set()
    results: dict[str, int] = {}

    for file_path in files:
        uri = _relative_uri(file_path, root)
        current_uris.add(uri)

        # Check if file content changed by comparing with stored hashes
        count = await index_file(
            file_path, root, store, embedder,
            max_chunk_tokens=max_chunk_tokens,
        )
        results[uri] = count

    # Remove chunks for deleted files
    deleted_uris = existing_uris - current_uris
    for uri in deleted_uris:
        removed = store.delete_by_uri(uri)
        logger.info(f"Removed {removed} chunks for deleted file: {uri}")
        results[uri] = 0

    return results
