"""MCP Server: FastMCP with 6 memory tools."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import OpenClawConfig, ensure_directories, load_config
from .embeddings import get_provider
from .indexer import index_directory
from .primer import build_primer, write_primer, write_session_to_journal, write_tasks
from .privacy import PrivacyFilter
from .retriever import Retriever
from .store import VectorStore
from .writer import smart_write

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server state (initialized lazily)
# ---------------------------------------------------------------------------

_config: OpenClawConfig | None = None
_store: VectorStore | None = None
_global_store: VectorStore | None = None
_embedder: Any = None
_retriever: Retriever | None = None
_privacy_filter: PrivacyFilter | None = None


def _get_config() -> OpenClawConfig:
    global _config
    if _config is None:
        _config = load_config()
        ensure_directories(_config)
    return _config


def _get_embedder():
    global _embedder
    if _embedder is None:
        cfg = _get_config()
        _embedder = get_provider(cfg.embedding)
    return _embedder


def _get_store() -> VectorStore:
    """Get the project-level vector store (or global if no project)."""
    global _store
    if _store is None:
        cfg = _get_config()
        emb = _get_embedder()
        db_path = cfg.project_index_db or cfg.global_index_db
        _store = VectorStore(db_path, dimension=emb.dimension)
    return _store


def _get_global_store() -> VectorStore:
    """Get the global vector store."""
    global _global_store
    if _global_store is None:
        cfg = _get_config()
        emb = _get_embedder()
        _global_store = VectorStore(cfg.global_index_db, dimension=emb.dimension)
    return _global_store


def _get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        cfg = _get_config()
        store = _get_store()
        emb = _get_embedder()
        roots: list[Path] = [cfg.global_root]
        if cfg.project_memory_dir:
            roots.append(cfg.project_memory_dir)
        _retriever = Retriever(
            store=store,
            embedder=emb,
            memory_roots=roots,
            default_max_tokens=cfg.search.default_max_tokens,
            half_life_days=cfg.search.recency_half_life_days,
        )
    return _retriever


def _get_privacy_filter() -> PrivacyFilter:
    global _privacy_filter
    if _privacy_filter is None:
        cfg = _get_config()
        _privacy_filter = PrivacyFilter(
            patterns=cfg.privacy.patterns,
            enabled=cfg.privacy.enabled,
        )
    return _privacy_filter


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="claw-memory",
    instructions=(
        "OpenClaw Memory is a persistent memory system for AI agents. "
        "Use memory_primer() at the start of every session to load context. "
        "Use memory_log() when you discover important information. "
        "Use memory_search() when you need to recall specific memories. "
        "Use memory_session_end() at the end of each session."
    ),
)


@mcp.tool()
async def memory_primer() -> str:
    """Load session context: user identity, project info, preferences, recent activity, active tasks.

    Call this tool ONCE at the start of every new session to load your working context.
    Returns structured context in ~500-1000 tokens. Zero search overhead.
    """
    cfg = _get_config()

    # Build primer content
    primer_content = build_primer(
        global_root=cfg.global_root,
        project_root=cfg.project_root,
        project_name=cfg.project.name,
        project_description=cfg.project.description,
    )

    # Also include instructions (always injected)
    instructions_path = cfg.global_root / "user" / "instructions.md"
    instructions = ""
    if instructions_path.is_file():
        instructions = instructions_path.read_text(encoding="utf-8").strip()

    parts = []
    if instructions:
        parts.append(f"# Instructions\n\n{instructions}")
    parts.append(f"# Context\n\n{primer_content}")

    return "\n\n".join(parts)


@mcp.tool()
async def memory_search(
    query: str,
    scope: str = "",
    max_tokens: int = 0,
) -> str:
    """Search memories with salience scoring and token budget control.

    Call when you need to recall specific information from past sessions.

    Args:
        query: What you want to find (natural language).
        scope: Filter scope - "user", "journal", "agent", "global", or empty for all.
        max_tokens: Maximum tokens in results (0 = use default 1500).
    """
    retriever = _get_retriever()
    response = await retriever.search(
        query,
        scope=scope,
        max_tokens=max_tokens if max_tokens > 0 else None,
    )

    if not response.results:
        return "No matching memories found."

    parts: list[str] = []
    for r in response.results:
        header = f"[salience: {r.salience:.2f} | reinforcement: {r.reinforcement} | {r.uri}]"
        parts.append(f"{header}\n{r.content}")

    footer = f"\n[total tokens: {response.total_tokens} | budget remaining: {response.budget_remaining}]"
    if response.fast_path_used:
        footer += " (fast path)"

    return "\n\n---\n\n".join(parts) + footer


@mcp.tool()
async def memory_log(
    content: str,
    type: str = "",
) -> str:
    """Record a new memory. Auto-classifies, deduplicates, detects conflicts, and routes to the right file.

    Call when you discover information worth remembering:
    1. User expressed a preference or requirement ("I prefer...", "Please always...")
    2. A technical decision was made ("Decided to use...", "Chose...")
    3. A reusable pattern was found ("The solution is...", "Root cause was...")
    4. A new fact about people/projects/tools was learned

    Do NOT record: temporary debug steps, code snippets, file paths, uncertain speculation.

    Args:
        content: The memory to store (plain text, one fact per call).
        type: Optional explicit type: preference, instruction, entity, decision, pattern, event.
    """
    cfg = _get_config()
    store = _get_store()
    embedder = _get_embedder()
    privacy = _get_privacy_filter()

    result = await smart_write(
        content=content,
        global_root=cfg.global_root,
        project_root=cfg.project_root,
        store=store,
        embedder=embedder,
        privacy_filter=privacy,
        memory_type=type or None,
    )

    if result.action == "rejected":
        return f"Memory not stored: {result.reason}"
    elif result.action == "reinforced":
        return f"Existing memory reinforced ({result.reason}) in {result.target_file}"
    elif result.action == "replaced":
        return f"Conflicting memory updated ({result.reason}) in {result.target_file}"
    else:
        return f"Memory saved to {result.target_file} (type: {result.memory_type})"


@mcp.tool()
async def memory_session_end(
    request: str = "",
    learned: str = "",
    completed: str = "",
    next_steps: str = "",
) -> str:
    """Write structured session summary. Call at the end of each session.

    This updates the daily journal, TASKS.md, and PRIMER.md.

    Args:
        request: What the user asked for in this session.
        learned: What was learned (comma-separated or single item).
        completed: What was accomplished (comma-separated or single item).
        next_steps: What should be done next (comma-separated or single item).
    """
    cfg = _get_config()
    if not cfg.project_root:
        return "No project detected. Session summary not written."

    # Parse comma-separated values into lists
    def to_list(s: str) -> list[str]:
        if not s:
            return []
        return [item.strip() for item in s.split(",") if item.strip()]

    summary = {
        "request": request,
        "learned": to_list(learned) or (learned if learned else []),
        "completed": to_list(completed) or (completed if completed else []),
        "next_steps": to_list(next_steps) or (next_steps if next_steps else []),
    }

    # Write to journal
    journal_path = write_session_to_journal(cfg.project_root, summary)

    # Update TASKS.md from next_steps
    if summary["next_steps"]:
        next_items = summary["next_steps"] if isinstance(summary["next_steps"], list) else [summary["next_steps"]]
        tasks = [{"title": item, "status": "pending"} for item in next_items]
        write_tasks(cfg.project_root, tasks)

    # Update PRIMER.md
    write_primer(
        cfg.global_root,
        cfg.project_root,
        cfg.project.name,
        cfg.project.description,
    )

    return f"Session summary written to {journal_path.name}. PRIMER.md and TASKS.md updated."


@mcp.tool()
async def memory_update_tasks(tasks_json: str) -> str:
    """Update task tracking. Call when task status changes during a session.

    Args:
        tasks_json: JSON array of task objects with keys: title, status (pending/done), progress, next_step, related_files.
            Example: [{"title": "Implement auth", "status": "done"}, {"title": "Add tests", "status": "pending", "next_step": "Write unit tests"}]
    """
    cfg = _get_config()
    if not cfg.project_root:
        return "No project detected. Tasks not updated."

    try:
        tasks = json.loads(tasks_json)
    except json.JSONDecodeError:
        return "Invalid JSON. Please provide a valid JSON array of task objects."

    if not isinstance(tasks, list):
        return "Expected a JSON array of task objects."

    tasks_path = write_tasks(cfg.project_root, tasks)

    # Update PRIMER
    write_primer(
        cfg.global_root,
        cfg.project_root,
        cfg.project.name,
        cfg.project.description,
    )

    return f"TASKS.md updated with {len(tasks)} tasks. PRIMER.md refreshed."


@mcp.tool()
async def memory_read(path: str) -> str:
    """Read a memory file's complete content.

    Call when you need to see the full content of a specific memory file.

    Args:
        path: Relative path to the memory file (e.g., "user/preferences.md", "agent/decisions.md", "journal/2026-02-12.md").
    """
    cfg = _get_config()

    # Try project memory first, then global
    candidates: list[Path] = []
    if cfg.project_memory_dir:
        candidates.append(cfg.project_memory_dir / path)
    candidates.append(cfg.global_root / path)

    for file_path in candidates:
        if file_path.is_file():
            return file_path.read_text(encoding="utf-8")

    return f"File not found: {path}"
