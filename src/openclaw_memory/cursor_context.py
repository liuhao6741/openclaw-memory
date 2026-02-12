"""Auto-update Cursor rules file with memory context.

Inspired by claude-mem's context injection mechanism: automatically write a
`.cursor/rules/openclaw-memory-context.mdc` file with `alwaysApply: true` so
that Cursor agents have memory context **without** needing to call any tool.

This file is updated:
1. On MCP server startup (first tool call)
2. After memory_session_end()
3. After a successful memory_log()
4. After memory_update_tasks()
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

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
# Public API
# ---------------------------------------------------------------------------


def update_cursor_context(
    project_root: Path | None,
    global_root: Path,
    project_name: str = "",
    project_description: str = "",
) -> Path | None:
    """Build primer content and write it to .cursor/rules/openclaw-memory-context.mdc.

    Uses atomic write (temp file + rename) to avoid corruption.
    Returns the path to the written file, or None if no project root.
    """
    if not project_root:
        logger.debug("No project root — skipping cursor context update")
        return None

    # Lazy import to avoid circular dependency
    from .primer import build_primer

    try:
        primer_content = build_primer(
            global_root=global_root,
            project_root=project_root,
            project_name=project_name,
            project_description=project_description,
        )
    except Exception as e:
        logger.warning(f"Failed to build primer for cursor context: {e}")
        return None

    # Also include global instructions if available
    instructions_path = global_root / "user" / "instructions.md"
    instructions = ""
    if instructions_path.is_file():
        try:
            import frontmatter

            text = instructions_path.read_text(encoding="utf-8")
            post = frontmatter.loads(text)
            body = post.content.strip()
            if body:
                instructions = body
        except Exception:
            pass

    # Assemble content
    parts: list[str] = []
    if instructions:
        parts.append(f"## Global Instructions\n\n{instructions}")
    parts.append(primer_content)

    full_content = "\n\n".join(parts)

    # Render template
    rendered = _CONTEXT_TEMPLATE.format(
        content=full_content,
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
        # Clean up temp file
        try:
            tmp_file.unlink(missing_ok=True)
        except Exception:
            pass
        return None
