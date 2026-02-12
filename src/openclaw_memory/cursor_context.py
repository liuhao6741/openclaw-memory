"""Auto-update Cursor rules file with memory context.

Inspired by claude-mem's context injection mechanism: automatically write a
`.cursor/rules/openclaw-memory-context.mdc` file with `alwaysApply: true` so
that Cursor agents have memory context **without** needing to call any tool.

This file is updated:
1. On MCP server startup (first tool call)
2. After memory_session_end()
3. After a successful memory_log()
4. After memory_update_tasks()

Key improvement over v0.3.0: respects `context.max_tokens` budget and
truncates content by priority (instructions > preferences > tasks > recent).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ContextConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

_CONTEXT_TEMPLATE = """\
---
description: "OpenClaw Memory — cross-session persistent context (auto-updated, do NOT edit manually)"
globs:
alwaysApply: true
---

# Memory Context (auto-injected)

The following context is from OpenClaw Memory, a persistent memory system
that tracks your coding sessions. This file is auto-updated — do not edit
it manually.

{content}

---
*Last updated: {updated_at}*
*For more detailed queries, use the `memory_search()` tool.*
"""


# ---------------------------------------------------------------------------
# Budget-aware content builder
# ---------------------------------------------------------------------------


def _count_tokens_approx(text: str) -> int:
    """Rough token count: CJK chars ≈ 1 token each, otherwise ~4 chars/token."""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    ascii_chars = len(text) - cjk
    return cjk + ascii_chars // 4


def _build_budgeted_content(
    global_root: Path,
    project_root: Path,
    project_name: str = "",
    project_description: str = "",
    ctx_cfg: "ContextConfig | None" = None,
) -> str:
    """Build context content that fits within the token budget.

    Priority order (higher = included first when budget is tight):
    1. Global instructions
    2. Key preferences
    3. Active tasks
    4. Recent activity
    5. Entity info
    """
    from .config import ContextConfig

    cfg = ctx_cfg or ContextConfig()
    budget = cfg.max_tokens
    sections: list[str] = []
    used = 0

    def _try_add(section_text: str) -> bool:
        nonlocal used
        tokens = _count_tokens_approx(section_text)
        if used + tokens <= budget:
            sections.append(section_text)
            used += tokens
            return True
        return False

    # --- 1. Instructions (highest priority) ---
    if cfg.include_instructions:
        instructions_path = global_root / "user" / "instructions.md"
        if instructions_path.is_file():
            try:
                import frontmatter
                text = instructions_path.read_text(encoding="utf-8")
                post = frontmatter.loads(text)
                body = post.content.strip()
                if body:
                    _try_add(f"## Instructions\n\n{body}")
            except Exception:
                pass

    # --- 2. Project info ---
    if project_name:
        info = f"## Project\n\n- {project_name}"
        if project_description:
            info += f" | {project_description}"
        _try_add(info)

    # --- 3. Key preferences ---
    from .primer import _extract_items
    pref_items = _extract_items(
        global_root / "user" / "preferences.md",
        max_items=cfg.max_preferences,
    )
    if pref_items:
        pref_text = "## Key Preferences\n\n" + "\n".join(f"- {i}" for i in pref_items)
        _try_add(pref_text)

    # --- 4. Active tasks ---
    from .primer import _read_tasks
    if project_root:
        tasks_content = _read_tasks(project_root / ".openclaw_memory" / "TASKS.md")
        if tasks_content and tasks_content != "（暂无）":
            # Truncate to max_tasks lines
            task_lines = [l for l in tasks_content.split("\n") if l.strip()]
            if len(task_lines) > cfg.max_tasks:
                task_lines = task_lines[:cfg.max_tasks]
                task_lines.append(f"  _(+{len(task_lines) - cfg.max_tasks} more)_")
            tasks_text = "## Active Tasks\n\n" + "\n".join(task_lines)
            _try_add(tasks_text)

    # --- 5. Recent activity ---
    from .primer import _extract_recent_completed
    if project_root:
        journal_dir = project_root / ".openclaw_memory" / "journal"
        recent = _extract_recent_completed(journal_dir, days=3)
        if recent:
            recent = recent[:cfg.max_recent_entries]
            recent_text = "## Recent Activity (3 days)\n\n" + "\n".join(f"- {i}" for i in recent)
            _try_add(recent_text)

    # --- 6. Entity info (lowest priority) ---
    entity_items = _extract_items(global_root / "user" / "entities.md", max_items=3)
    if entity_items:
        entity_text = "## Entities\n\n" + "\n".join(f"- {i}" for i in entity_items)
        _try_add(entity_text)

    return "\n\n".join(sections) if sections else "*(No memories yet — start recording!)*"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def update_cursor_context(
    project_root: Path | None,
    global_root: Path,
    project_name: str = "",
    project_description: str = "",
    ctx_cfg: "ContextConfig | None" = None,
) -> Path | None:
    """Build budget-aware content and write to .cursor/rules/openclaw-memory-context.mdc.

    Uses atomic write (temp file + rename) to avoid corruption.
    Returns the path to the written file, or None if no project root.
    """
    if not project_root:
        logger.debug("No project root — skipping cursor context update")
        return None

    try:
        content = _build_budgeted_content(
            global_root=global_root,
            project_root=project_root,
            project_name=project_name,
            project_description=project_description,
            ctx_cfg=ctx_cfg,
        )
    except Exception as e:
        logger.warning(f"Failed to build cursor context: {e}")
        return None

    # Render template
    rendered = _CONTEXT_TEMPLATE.format(
        content=content,
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    # Write to .cursor/rules/ (atomic)
    rules_dir = project_root / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    context_file = rules_dir / "openclaw-memory-context.mdc"
    tmp_file = context_file.with_suffix(".mdc.tmp")

    try:
        tmp_file.write_text(rendered, encoding="utf-8")
        tmp_file.rename(context_file)
        logger.info(f"Updated cursor context file: {context_file}")
        return context_file
    except Exception as e:
        logger.warning(f"Failed to write cursor context file: {e}")
        try:
            tmp_file.unlink(missing_ok=True)
        except Exception:
            pass
        return None
