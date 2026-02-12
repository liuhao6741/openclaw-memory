"""File watcher: monitor memory directories for changes and trigger re-indexing."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .embeddings import EmbeddingProvider
    from .store import VectorStore

logger = logging.getLogger(__name__)

# Debounce interval in seconds
_DEBOUNCE_SECONDS = 1.5


async def watch_and_index(
    roots: list[Path],
    store: "VectorStore",
    embedder: "EmbeddingProvider",
    *,
    on_indexed: Callable[[str, int], None] | None = None,
) -> None:
    """Watch memory directories and re-index on changes.

    This is a long-running coroutine. Cancel it to stop watching.

    Args:
        roots: List of directories to watch.
        store: Vector store to update.
        embedder: Embedding provider.
        on_indexed: Optional callback(uri, chunk_count) after indexing a file.
    """
    try:
        from watchfiles import awatch, Change
    except ImportError:
        logger.error(
            "watchfiles is required for file watching. "
            "Install with: pip install watchfiles"
        )
        return

    from .indexer import index_file

    watch_paths = [str(r) for r in roots if r.is_dir()]
    if not watch_paths:
        logger.warning("No valid directories to watch")
        return

    logger.info(f"Watching directories: {watch_paths}")

    pending_files: set[Path] = set()
    debounce_task: asyncio.Task | None = None

    async def _process_pending() -> None:
        """Process all pending file changes after debounce period."""
        await asyncio.sleep(_DEBOUNCE_SECONDS)

        files = list(pending_files)
        pending_files.clear()

        for file_path in files:
            if not file_path.exists():
                # File was deleted
                for root in roots:
                    try:
                        uri = str(file_path.relative_to(root))
                        deleted = store.delete_by_uri(uri)
                        logger.info(f"Removed {deleted} chunks for deleted: {uri}")
                        if on_indexed:
                            on_indexed(uri, 0)
                        break
                    except ValueError:
                        continue
            elif file_path.suffix == ".md":
                # File was created or modified
                for root in roots:
                    try:
                        file_path.relative_to(root)
                        count = await index_file(file_path, root, store, embedder)
                        logger.info(f"Re-indexed {count} chunks: {file_path.name}")
                        if on_indexed:
                            on_indexed(str(file_path.relative_to(root)), count)
                        break
                    except ValueError:
                        continue

    async for changes in awatch(*watch_paths):
        for change_type, changed_path_str in changes:
            changed_path = Path(changed_path_str)

            # Skip hidden files and index.db
            if any(p.startswith(".") for p in changed_path.parts[len(roots[0].parts):]):
                if ".openclaw_memory" not in str(changed_path):
                    continue
            if changed_path.name == "index.db" or changed_path.suffix == ".db":
                continue
            # Skip auto-generated files
            if changed_path.name in ("PRIMER.md", "TASKS.md"):
                continue

            pending_files.add(changed_path)

            # Cancel existing debounce and restart
            if debounce_task and not debounce_task.done():
                debounce_task.cancel()
            debounce_task = asyncio.create_task(_process_pending())
