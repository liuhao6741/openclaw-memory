"""MCP Server: FastMCP with 6 memory tools."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import OpenClawConfig, ensure_directories, load_config
from .cursor_context import update_cursor_context
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
_context_refreshed: bool = False  # tracks first-call context refresh


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
            w_semantic=cfg.search.w_semantic,
            w_reinforcement=cfg.search.w_reinforcement,
            w_recency=cfg.search.w_recency,
            w_access=cfg.search.w_access,
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


def _extract_preview(content: str, max_chars: int = 60) -> str:
    """Extract a short preview from memory content for compact display."""
    for line in content.split("\n"):
        line = line.strip()
        # Skip frontmatter markers, headers, and empty lines
        if not line or line.startswith("---") or line.startswith("#"):
            continue
        # Strip list marker
        if line.startswith("- "):
            line = line[2:]
        # Truncate
        if len(line) > max_chars:
            return line[:max_chars - 3] + "..."
        return line
    return "(empty)"


def _refresh_cursor_context() -> None:
    """Update .cursor/rules/openclaw-memory-context.mdc with latest primer content.

    Called on first tool invocation and after state-changing operations
    (memory_log, memory_session_end, memory_update_tasks).
    Non-blocking: errors are logged but never propagated.
    """
    try:
        cfg = _get_config()
        update_cursor_context(
            project_root=cfg.project_root,
            global_root=cfg.global_root,
            project_name=cfg.project.name,
            project_description=cfg.project.description,
            ctx_cfg=cfg.context,
        )
    except Exception as e:
        logger.warning(f"Cursor context refresh failed (non-critical): {e}")


def _ensure_first_call_refresh() -> None:
    """Ensure cursor context file is refreshed on the very first tool call.

    This guarantees the context file is up-to-date even if the agent
    doesn't call memory_primer() first (e.g., starts with memory_search).
    """
    global _context_refreshed
    if not _context_refreshed:
        _refresh_cursor_context()
        _context_refreshed = True


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="claw-memory",
    instructions=(
        "OpenClaw Memory is a persistent memory system for AI agents. "
        "Key context is auto-injected via Cursor rules — you already have it. "
        "Use memory_primer() at session start for full context. "
        "Use memory_log() to record user preferences, technical decisions, patterns, and facts. "
        "Use memory_observe() after significant coding actions to build a structured timeline. "
        "Use memory_search(query) to recall specific past info (compact index by default, detail=true for full). "
        "Use memory_session_end() when the user ends the session."
    ),
)


@mcp.tool()
async def memory_primer() -> str:
    """Load full session context: user identity, project info, preferences, recent activity, active tasks.

    Call this tool ONCE at the start of every new session to load your complete working context.
    Note: A lightweight version of this context is already auto-injected via Cursor rules.
    This tool provides the full, detailed version (~500-1000 tokens). Zero search overhead.

    Returns structured markdown with sections: Instructions, Entities, Preferences,
    Recent Context (last 3 days), and Active Tasks.
    """
    cfg = _get_config()
    _ensure_first_call_refresh()

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
    detail: bool = False,
) -> str:
    """Search memories with hybrid retrieval (semantic + full-text) and salience scoring.

    Call when you need to recall specific information from past sessions, e.g.:
    - User says "remember", "before", "last time", "we discussed"
    - You need to check a past architectural decision
    - You want to confirm a user preference before suggesting something

    Search tips:
    - Use specific keywords: "JWT authentication decision" not "what did we decide"
    - Include "recent" or "最近" to trigger the fast timeline path (reads last 7 days)
    - Queries about "preference" or "偏好" trigger the fast keyword path

    By default returns a **compact index** (~50-100 tokens/result) with salience
    scores and first-line previews. Set detail=true to get full content (costs
    more tokens). Use `memory_read(path)` to read a specific file in full.

    Results are ranked by salience (configurable weights, default:
    50% semantic + 20% reinforcement + 20% recency + 10% access frequency).

    Args:
        query: What you want to find (natural language, be keyword-rich).
        scope: Filter scope - "user" (preferences/instructions/entities),
               "journal" (session logs), "agent" (decisions/patterns),
               "global" (cross-project), or "" for all scopes.
        max_tokens: Maximum tokens in results (0 = use default 1500).
        detail: If true, return full content for each result (default: compact index).
    """
    _ensure_first_call_refresh()
    retriever = _get_retriever()
    response = await retriever.search(
        query,
        scope=scope,
        max_tokens=max_tokens if max_tokens > 0 else None,
    )

    if not response.results:
        return "No matching memories found."

    if detail or response.fast_path_used:
        # Full content mode (or fast-path which always returns full)
        parts: list[str] = []
        for r in response.results:
            header = f"[salience: {r.salience:.2f} | reinforcement: {r.reinforcement} | {r.uri}]"
            parts.append(f"{header}\n{r.content}")

        footer = f"\n[total tokens: {response.total_tokens} | budget remaining: {response.budget_remaining}]"
        if response.fast_path_used:
            footer += " (fast path)"
        return "\n\n---\n\n".join(parts) + footer
    else:
        # Compact index mode (~50-100 tokens/result)
        lines: list[str] = []
        lines.append("| # | Salience | Source | Preview | Tokens |")
        lines.append("|---|---------|--------|---------|--------|")
        for i, r in enumerate(response.results, 1):
            # Extract first meaningful line as preview
            preview = _extract_preview(r.content, max_chars=60)
            lines.append(
                f"| {i} | {r.salience:.2f} | `{r.uri}` | {preview} | ~{r.token_count} |"
            )

        footer_parts = [
            f"\n**{len(response.results)} results** "
            f"(total ~{response.total_tokens} tokens, budget remaining: {response.budget_remaining})",
            "",
            "_Tip: Set detail=true to see full content, "
            "or use `memory_read(path)` to read a specific file._",
        ]
        return "\n".join(lines + footer_parts)


@mcp.tool()
async def memory_log(
    content: str,
    type: str = "",
) -> str:
    """Record a new memory. Auto-classifies, deduplicates, detects conflicts, and routes to the correct file.

    The system automatically:
    - Routes content to the right file based on keywords (preferences.md, decisions.md, etc.)
    - Detects near-duplicates (similarity >= 0.92) and reinforces instead of duplicating
    - Detects conflicts (similarity 0.85-0.92) and replaces the outdated entry
    - Filters out low-quality content (filler phrases, code snippets, speculation)

    Call when you discover information worth persisting across sessions:
    1. User states a preference: "I prefer tabs over spaces" → routes to preferences.md
    2. User sets a rule: "Always run tests before committing" → routes to instructions.md
    3. A technical decision: "Chose PostgreSQL over MySQL because..." → routes to decisions.md
    4. A reusable pattern: "Root cause was N+1 queries, fix: use joinedload()" → routes to patterns.md
    5. A fact about someone/something: "Alice leads the backend team" → routes to entities.md

    Do NOT record: debug output, code snippets, file paths, uncertain speculation ("maybe...", "probably...").

    Args:
        content: The memory to store (plain text, one fact per call, be concise).
        type: Optional explicit type to override auto-routing: preference, instruction, entity, decision, pattern, event.
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
        reinforce_threshold=cfg.writer.reinforce_threshold,
        conflict_threshold=cfg.writer.conflict_threshold,
    )

    if result.action == "rejected":
        return f"Memory not stored: {result.reason}"

    # Refresh cursor context after successful write
    _refresh_cursor_context()

    if result.action == "reinforced":
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
    """Write structured session summary and update project state. Call when the user ends the session.

    This performs three updates atomically:
    1. Appends a timestamped session block to today's journal (journal/YYYY-MM-DD.md)
    2. Updates TASKS.md with next_steps as new pending tasks
    3. Refreshes PRIMER.md and the auto-injected Cursor context file

    Call when the user says goodbye, ends the conversation, or explicitly asks to wrap up.

    Args:
        request: One-line summary of what the user originally asked for.
        learned: Key knowledge discovered in this session (comma-separated or single item).
        completed: What was accomplished (comma-separated or single item).
        next_steps: What should be done next — becomes pending tasks (comma-separated or single item).
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

    # Refresh cursor context after session end
    _refresh_cursor_context()

    return f"Session summary written to {journal_path.name}. PRIMER.md and TASKS.md updated."


@mcp.tool()
async def memory_update_tasks(tasks_json: str) -> str:
    """Update the project task list (TASKS.md). Call when task status changes during a session.

    Use this to track work across sessions — tasks persist and appear in every
    future session's primer context. Mark tasks as "done" when completed,
    add new tasks as "pending".

    Args:
        tasks_json: JSON array of task objects. Required keys: title, status (pending/done).
            Optional keys: progress (string), next_step (string), related_files (list of strings).
            Example: [{"title": "Implement auth", "status": "done"}, {"title": "Add tests", "status": "pending", "next_step": "Write unit tests for auth module"}]
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

    # Refresh cursor context after task update
    _refresh_cursor_context()

    return f"TASKS.md updated with {len(tasks)} tasks. PRIMER.md refreshed."


@mcp.tool()
async def memory_observe(
    action: str,
    result: str = "",
    files: str = "",
    insight: str = "",
) -> str:
    """Record a structured observation about a coding action you just performed.

    Unlike memory_log() which records standalone facts, this captures **what you
    just did** with structured metadata — closer to how claude-mem auto-records
    tool executions. Use this after significant actions to build a richer timeline.

    The observation is written to today's journal with structured formatting.
    If `insight` contains a reusable pattern or decision, it is also routed
    to the appropriate memory file (patterns.md / decisions.md).

    Call after:
    - Completing a significant code change or refactor
    - Debugging and finding a root cause
    - Running tests and discovering failures/fixes
    - Making an architectural or dependency decision

    Args:
        action: What you did (e.g., "Fixed N+1 query in user_list endpoint").
        result: Outcome or key finding (e.g., "Response time dropped from 2s to 50ms").
        files: Comma-separated list of files touched (e.g., "api/users.py, tests/test_users.py").
        insight: Optional reusable insight — if provided, also saved as a pattern/decision memory.
    """
    cfg = _get_config()
    if not cfg.project_root:
        return "No project detected. Observation not recorded."

    from datetime import date as date_cls
    from datetime import datetime as dt

    # Build structured observation block for journal
    time_str = dt.now().strftime("%H:%M")
    lines: list[str] = [f"### [{time_str}] {action}"]
    if result:
        lines.append(f"- **Result:** {result}")
    if files:
        lines.append(f"- **Files:** {files}")
    if insight:
        lines.append(f"- **Insight:** {insight}")
    lines.append("")
    obs_block = "\n".join(lines)

    # Append to today's journal
    today = date_cls.today().isoformat()
    journal_dir = cfg.project_root / ".openclaw_memory" / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_path = journal_dir / f"{today}.md"

    import frontmatter

    if journal_path.is_file():
        existing = journal_path.read_text(encoding="utf-8")
        post = frontmatter.loads(existing)
        post.content = post.content.rstrip() + "\n\n" + obs_block
        journal_path.write_text(frontmatter.dumps(post), encoding="utf-8")
    else:
        post = frontmatter.Post(
            content=obs_block.strip(),
            type="event",
            created=today,
            updated=today,
            sessions=0,
        )
        journal_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    response_parts = [f"Observation recorded in journal/{today}.md"]

    # If there's a reusable insight, also save it as a memory
    if insight and len(insight) >= 15:
        store = _get_store()
        embedder = _get_embedder()
        privacy = _get_privacy_filter()

        write_result = await smart_write(
            content=insight,
            global_root=cfg.global_root,
            project_root=cfg.project_root,
            store=store,
            embedder=embedder,
            privacy_filter=privacy,
            reinforce_threshold=cfg.writer.reinforce_threshold,
            conflict_threshold=cfg.writer.conflict_threshold,
        )
        if write_result.action != "rejected":
            response_parts.append(
                f"Insight also saved to {write_result.target_file} ({write_result.action})"
            )

    # Refresh cursor context
    _refresh_cursor_context()

    return ". ".join(response_parts) + "."


@mcp.tool()
async def memory_read(path: str) -> str:
    """Read a memory file's complete content (untruncated).

    Call when you need to see the full content of a specific memory file.
    Unlike memory_search() which returns ranked excerpts, this returns the
    entire file content including frontmatter metadata.

    Common paths:
    - "user/preferences.md" — user coding preferences
    - "user/instructions.md" — standing instructions for the agent
    - "user/entities.md" — people, projects, tools
    - "agent/decisions.md" — architectural decisions (project-level)
    - "agent/patterns.md" — reusable solution patterns (project-level)
    - "journal/2026-02-12.md" — session log for a specific date
    - "TASKS.md" — current task list (project-level)
    - "PRIMER.md" — auto-generated session primer (project-level)

    Args:
        path: Relative path within the memory directory. Tries project memory first, then global.
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


# ---------------------------------------------------------------------------
# MCP Prompts — available in Cursor's prompt selector
# ---------------------------------------------------------------------------


@mcp.prompt()
def session_start() -> str:
    """Load memory context for a new session.

    Returns the full primer context including user identity, preferences,
    recent activity, and active tasks. Use this at the start of any session.
    """
    cfg = _get_config()
    _ensure_first_call_refresh()

    primer_content = build_primer(
        global_root=cfg.global_root,
        project_root=cfg.project_root,
        project_name=cfg.project.name,
        project_description=cfg.project.description,
    )

    instructions_path = cfg.global_root / "user" / "instructions.md"
    instructions = ""
    if instructions_path.is_file():
        instructions = instructions_path.read_text(encoding="utf-8").strip()

    parts = []
    if instructions:
        parts.append(f"# Instructions\n\n{instructions}")
    parts.append(f"# Context\n\n{primer_content}")
    parts.append(
        "\n---\n"
        "Memory system is active. Use `memory_log()` to record important findings, "
        "`memory_search()` for recall, and `memory_session_end()` when done."
    )

    return "\n\n".join(parts)


@mcp.prompt()
def session_end_template() -> str:
    """Template for ending a session with a structured summary.

    Copy and fill in this template, then call memory_session_end() with the values.
    """
    return (
        "Please call `memory_session_end()` with the following information:\n\n"
        "- **request**: [One-line summary of what was asked for]\n"
        "- **learned**: [Key knowledge discovered, comma-separated]\n"
        "- **completed**: [What was accomplished, comma-separated]\n"
        "- **next_steps**: [What should be done next, comma-separated]\n"
    )
