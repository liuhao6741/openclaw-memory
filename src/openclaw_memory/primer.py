"""Primer module: build and maintain PRIMER.md from template + file extraction."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import frontmatter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _extract_items(file_path: Path, max_items: int = 5) -> list[str]:
    """Extract list items from a markdown file, sorted by reinforcement (if available)."""
    if not file_path.is_file():
        return []

    text = file_path.read_text(encoding="utf-8")
    post = frontmatter.loads(text)
    body = post.content.strip()

    if not body:
        return []

    # Parse list items
    items: list[str] = []
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("- "):
            items.append(line[2:].strip())

    # For now, return last N items (most recently added)
    # Future: sort by per-item reinforcement if tracked
    return items[-max_items:] if len(items) > max_items else items


def _extract_recent_completed(journal_dir: Path, days: int = 3) -> list[str]:
    """Extract 'completed' items from recent journal files."""
    if not journal_dir.is_dir():
        return []

    today = date.today()
    entries: list[str] = []

    for i in range(days):
        d = today - timedelta(days=i)
        file_path = journal_dir / f"{d.isoformat()}.md"
        if not file_path.is_file():
            continue

        text = file_path.read_text(encoding="utf-8")
        in_completed = False
        session_heading = ""

        for line in text.split("\n"):
            stripped = line.strip()

            # Track session headings
            if stripped.startswith("## Session") or stripped.startswith("## session"):
                session_heading = stripped.replace("## ", "").strip()
                in_completed = False

            # Detect completed section
            if stripped.lower().startswith("### 完成了什么") or stripped.lower().startswith("### completed"):
                in_completed = True
                continue

            # Stop at next heading
            if stripped.startswith("###") and in_completed:
                in_completed = False
                continue

            # Collect completed items
            if in_completed and stripped.startswith("- "):
                prefix = f"{d.isoformat()}"
                if session_heading:
                    prefix += f" {session_heading}"
                entries.append(f"{prefix}：{stripped[2:]}")

    return entries[:10]  # Max 10 recent entries


def _read_tasks(tasks_path: Path) -> str:
    """Read TASKS.md content (just the body, no frontmatter)."""
    if not tasks_path.is_file():
        return "（暂无）"

    text = tasks_path.read_text(encoding="utf-8")
    post = frontmatter.loads(text)
    body = post.content.strip()
    return body if body else "（暂无）"


# ---------------------------------------------------------------------------
# Primer template
# ---------------------------------------------------------------------------

_PRIMER_TEMPLATE = """## 用户身份
{entities}

## 项目概况
{project_info}

## 关键偏好
{preferences}

## 近期上下文（最近 {days} 天）
{recent_context}

## 进行中任务
{tasks}
"""


def build_primer(
    global_root: Path,
    project_root: Path | None = None,
    project_name: str = "",
    project_description: str = "",
) -> str:
    """Build PRIMER.md content from template + file extraction.

    No LLM required — pure template-based assembly.
    """
    # Entities
    entities_items = _extract_items(global_root / "user" / "entities.md", max_items=5)
    entities = "\n".join(f"- {item}" for item in entities_items) if entities_items else "（暂无记录）"

    # Project info
    if project_name:
        project_info = f"- {project_name}"
        if project_description:
            project_info += f" | {project_description}"
    else:
        project_info = "（暂无记录）"

    # Preferences (from global)
    pref_items = _extract_items(global_root / "user" / "preferences.md", max_items=5)
    preferences = "\n".join(f"- {item}" for item in pref_items) if pref_items else "（暂无记录）"

    # Recent context (from project journal)
    days = 3
    if project_root:
        journal_dir = project_root / ".openclaw_memory" / "journal"
        recent_items = _extract_recent_completed(journal_dir, days=days)
    else:
        recent_items = []
    recent_context = "\n".join(f"- {item}" for item in recent_items) if recent_items else "（暂无记录）"

    # Tasks
    if project_root:
        tasks = _read_tasks(project_root / ".openclaw_memory" / "TASKS.md")
    else:
        tasks = "（暂无）"

    return _PRIMER_TEMPLATE.format(
        entities=entities,
        project_info=project_info,
        preferences=preferences,
        days=days,
        recent_context=recent_context,
        tasks=tasks,
    ).strip() + "\n"


def write_primer(
    global_root: Path,
    project_root: Path | None = None,
    project_name: str = "",
    project_description: str = "",
) -> Path | None:
    """Build and write PRIMER.md to the project memory directory.

    Returns the path to the written file, or None if no project root.
    """
    if not project_root:
        return None

    content = build_primer(global_root, project_root, project_name, project_description)
    primer_path = project_root / ".openclaw_memory" / "PRIMER.md"
    primer_path.parent.mkdir(parents=True, exist_ok=True)
    primer_path.write_text(content, encoding="utf-8")
    logger.info(f"Updated PRIMER.md at {primer_path}")
    return primer_path


def write_session_to_journal(
    project_root: Path,
    summary: dict[str, Any],
) -> Path:
    """Write a structured session summary to today's journal file.

    Args:
        summary: Dict with keys: request, learned, completed, next_steps
    """
    from datetime import datetime as dt

    today = date.today().isoformat()
    journal_dir = project_root / ".openclaw_memory" / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_path = journal_dir / f"{today}.md"

    time_str = dt.now().strftime("%H:%M")

    # Build session block
    lines = [f"\n## Session {time_str}\n"]

    if summary.get("request"):
        lines.append("### 请求")
        lines.append(summary["request"])
        lines.append("")

    if summary.get("learned"):
        lines.append("### 学到了什么")
        learned = summary["learned"]
        if isinstance(learned, list):
            for item in learned:
                lines.append(f"- {item}")
        else:
            lines.append(f"- {learned}")
        lines.append("")

    if summary.get("completed"):
        lines.append("### 完成了什么")
        completed = summary["completed"]
        if isinstance(completed, list):
            for item in completed:
                lines.append(f"- {item}")
        else:
            lines.append(f"- {completed}")
        lines.append("")

    if summary.get("next_steps"):
        lines.append("### 下一步")
        next_steps = summary["next_steps"]
        if isinstance(next_steps, list):
            for item in next_steps:
                lines.append(f"- {item}")
        else:
            lines.append(f"- {next_steps}")
        lines.append("")

    session_text = "\n".join(lines)

    # Append to journal or create new
    if journal_path.is_file():
        existing = journal_path.read_text(encoding="utf-8")
        # Update session count in frontmatter
        post = frontmatter.loads(existing)
        post.metadata["sessions"] = post.metadata.get("sessions", 0) + 1
        post.metadata["updated"] = today
        post.content = post.content.rstrip() + "\n\n---\n" + session_text
        journal_path.write_text(frontmatter.dumps(post), encoding="utf-8")
    else:
        post = frontmatter.Post(
            content=session_text.strip(),
            type="event",
            created=today,
            updated=today,
            sessions=1,
        )
        journal_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    logger.info(f"Wrote session summary to {journal_path}")
    return journal_path


def write_tasks(
    project_root: Path,
    tasks: list[dict[str, Any]],
) -> Path:
    """Write or update TASKS.md.

    Args:
        tasks: List of dicts with keys: title, status, progress, next_step, related_files
    """
    tasks_path = project_root / ".openclaw_memory" / "TASKS.md"
    tasks_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for task in tasks:
        status = task.get("status", "pending")
        checkbox = "[x]" if status == "done" else "[ ]"
        title = task.get("title", "Untitled")
        lines.append(f"- {checkbox} {title}")

        if task.get("progress"):
            lines.append(f"  - 进展：{task['progress']}")
        if task.get("next_step"):
            lines.append(f"  - 下一步：{task['next_step']}")
        if task.get("related_files"):
            files = ", ".join(task["related_files"])
            lines.append(f"  - 相关文件：{files}")

    today = date.today().isoformat()
    post = frontmatter.Post(
        content="\n".join(lines),
        type="tasks",
        updated=today,
    )
    tasks_path.write_text(frontmatter.dumps(post), encoding="utf-8")
    logger.info(f"Updated TASKS.md at {tasks_path}")
    return tasks_path
