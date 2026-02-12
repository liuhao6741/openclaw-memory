"""Smart writer: quality gate, privacy filter, routing, reinforcement, conflict detection."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import frontmatter

from .privacy import PrivacyFilter

if TYPE_CHECKING:
    from .embeddings import EmbeddingProvider
    from .store import VectorStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------

# Filler phrases that should not be stored as memories
_FILLER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"^(我来|让我|I'll|Let me|I will)\s*(帮你|看看|help|check|look)",
        r"^(好的|OK|Sure|Alright|Got it)",
        r"^(当然|Of course|Certainly)",
        r"^(没问题|No problem)",
        r"^(这是|Here is|Here's|This is)\s*(the|a)?\s*(code|file|result)",
    ]
]

# Patterns for pure code/path content
_CODE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p) for p in [
        r"^[/\\][\w/\\.-]+$",                 # Pure file path
        r"^[\w/\\.-]+\.(py|js|ts|go|rs|java|cpp|c|h)$",  # File with extension
        r"^(import|from|require|include)\s+",  # Import statement
        r"^\s*[\{\[\(]",                        # Starts with bracket
    ]
]

# Speculative prefixes
_SPECULATIVE_PREFIXES = [
    "可能", "也许", "或许", "大概", "probably", "maybe", "perhaps",
    "might be", "could be", "not sure",
]


@dataclass
class GateResult:
    """Result of quality gate check."""
    passed: bool
    reason: str = ""


def quality_gate(content: str, privacy_filter: PrivacyFilter | None = None) -> GateResult:
    """Check if content passes quality gate for memory storage."""
    text = content.strip()

    # Length check (10 chars for CJK, 20 for ASCII)
    min_len = 10 if any("\u4e00" <= c <= "\u9fff" for c in text) else 20
    if len(text) < min_len:
        return GateResult(False, "too_short")

    # Filler check
    for pat in _FILLER_PATTERNS:
        if pat.match(text):
            return GateResult(False, "filler")

    # Code/path check
    for pat in _CODE_PATTERNS:
        if pat.match(text):
            return GateResult(False, "code_or_path")

    # Speculation check
    text_lower = text.lower()
    for prefix in _SPECULATIVE_PREFIXES:
        if text_lower.startswith(prefix):
            return GateResult(False, "speculative")

    # Privacy check
    if privacy_filter and privacy_filter.contains_sensitive(text):
        return GateResult(False, "privacy")

    return GateResult(True)


# ---------------------------------------------------------------------------
# Smart routing
# ---------------------------------------------------------------------------

# (pattern, target_file_relative_path, is_global, default_importance)
_ROUTING_RULES: list[tuple[re.Pattern[str], str, bool, int]] = [
    # Instruction type (highest priority - check first)
    (re.compile(r"(必须|不要|不允许|禁止|always|never|must|规范|规则|要求|请总是)", re.I),
     "user/instructions.md", True, 5),

    # Decision type
    (re.compile(r"(决定|采用|选择了?|决策|ADR|decided|chose|adopt)", re.I),
     "agent/decisions.md", False, 5),

    # Pattern type (before entity to avoid false matches on "是")
    (re.compile(r"(发现|总结|规律|模式|解决方案|pattern|solution|workaround|原因是)", re.I),
     "agent/patterns.md", False, 3),

    # Preference type
    (re.compile(r"(偏好|喜欢|习惯|prefer|like to|fond of|favor)", re.I),
     "user/preferences.md", True, 4),

    # Entity type (more specific: require CJK name or "Name is/role" pattern)
    (re.compile(r"([\u4e00-\u9fff]{2,4})(是|担任|负责)", re.I),
     "user/entities.md", True, 3),
    (re.compile(r"(\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+(is|role is|works on|leads?|maintains?)", re.I),
     "user/entities.md", True, 3),
]


@dataclass
class RouteResult:
    """Result of smart routing."""
    target_file: str      # relative path from memory root
    is_global: bool       # True = write to global, False = write to project
    memory_type: str       # preference/instruction/entity/decision/pattern/event
    importance: int        # 1-5


def route_content(content: str) -> RouteResult:
    """Determine which file to write content to based on keyword patterns."""
    text = content.strip()

    for pattern, target, is_global, importance in _ROUTING_RULES:
        if pattern.search(text):
            # Infer type from target path
            if "instruction" in target:
                mem_type = "instruction"
            elif "decision" in target:
                mem_type = "decision"
            elif "preference" in target:
                mem_type = "preference"
            elif "entities" in target:
                mem_type = "entity"
            elif "pattern" in target:
                mem_type = "pattern"
            else:
                mem_type = "event"

            return RouteResult(target, is_global, mem_type, importance)

    # Default: journal
    today = date.today().isoformat()
    return RouteResult(f"journal/{today}.md", False, "event", 1)


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

def _ensure_file_with_frontmatter(
    file_path: Path,
    memory_type: str,
    importance: int,
) -> None:
    """Ensure a markdown file exists with proper frontmatter."""
    if file_path.exists():
        return

    file_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    post = frontmatter.Post(
        content="",
        type=memory_type,
        importance=importance,
        reinforcement=0,
        created=now,
        updated=now,
        status="active",
    )
    file_path.write_text(frontmatter.dumps(post), encoding="utf-8")


def _append_to_file(file_path: Path, content: str) -> None:
    """Append a list item to a markdown file."""
    _ensure_file_with_frontmatter(file_path, "", 1)
    text = file_path.read_text(encoding="utf-8")
    post = frontmatter.loads(text)

    # Update timestamp
    post.metadata["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Append as list item
    body = post.content.rstrip()
    if body and not body.endswith("\n"):
        body += "\n"
    body += f"- {content}\n"
    post.content = body

    file_path.write_text(frontmatter.dumps(post), encoding="utf-8")


def _replace_in_file(file_path: Path, old_content: str, new_content: str) -> bool:
    """Replace an existing list item in a markdown file. Returns True if replaced."""
    if not file_path.exists():
        return False

    text = file_path.read_text(encoding="utf-8")
    post = frontmatter.loads(text)

    # Find and replace the list item
    lines = post.content.split("\n")
    replaced = False

    for i, line in enumerate(lines):
        stripped = line.lstrip("- ").strip()
        if stripped == old_content.strip():
            lines[i] = f"- {new_content}"
            replaced = True
            break

    if replaced:
        post.content = "\n".join(lines)
        post.metadata["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        file_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    return replaced


def _increment_reinforcement_in_file(file_path: Path) -> None:
    """Increment reinforcement counter in frontmatter."""
    if not file_path.exists():
        return
    text = file_path.read_text(encoding="utf-8")
    post = frontmatter.loads(text)
    post.metadata["reinforcement"] = post.metadata.get("reinforcement", 0) + 1
    post.metadata["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    file_path.write_text(frontmatter.dumps(post), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main write function
# ---------------------------------------------------------------------------

@dataclass
class WriteResult:
    """Result of a memory write operation."""
    action: str  # "appended" | "replaced" | "reinforced" | "rejected"
    target_file: str
    reason: str = ""
    memory_type: str = ""


async def smart_write(
    content: str,
    global_root: Path,
    project_root: Path | None,
    store: "VectorStore",
    embedder: "EmbeddingProvider",
    privacy_filter: PrivacyFilter | None = None,
    memory_type: str | None = None,
    *,
    reinforce_threshold: float = 0.92,
    conflict_threshold: float = 0.85,
) -> WriteResult:
    """Write a memory through the full smart pipeline.

    Pipeline: quality gate → privacy → route → embed → dedup/conflict → write.

    Args:
        reinforce_threshold: Similarity >= this → reinforce existing (default 0.92).
        conflict_threshold: Similarity >= this → replace conflicting (default 0.85).
    """
    # 1. Quality gate
    gate = quality_gate(content, privacy_filter)
    if not gate.passed:
        logger.info(f"Quality gate rejected: {gate.reason}")
        return WriteResult("rejected", "", gate.reason)

    # 2. Route (or use explicit type)
    if memory_type:
        route = _route_by_type(memory_type)
    else:
        route = route_content(content)

    # 3. Resolve file path
    if route.is_global:
        file_path = global_root / route.target_file
    elif project_root:
        file_path = project_root / ".openclaw_memory" / route.target_file
    else:
        # No project, fall back to global
        file_path = global_root / route.target_file

    # 4. Embed for dedup/conflict detection
    embedding = await embedder.embed_single(content)

    # 5. Find similar existing memories
    similar = store.find_similar(embedding, threshold=conflict_threshold)

    # Filter to same file for conflict detection in list-type files
    same_file_similar = [s for s in similar if s.uri == _relative_uri_for_store(file_path, global_root, project_root)]

    # 6. Reinforcement vs conflict vs append
    if same_file_similar:
        best = same_file_similar[0]

        if best.score >= reinforce_threshold:
            # Very high similarity → reinforce, don't duplicate
            store.increment_reinforcement(best.id)
            _increment_reinforcement_in_file(file_path)
            logger.info(f"Reinforced existing memory (score={best.score:.2f}): {best.content[:50]}")
            return WriteResult("reinforced", route.target_file, f"score={best.score:.2f}", route.memory_type)

        elif best.score >= conflict_threshold:
            # Moderate similarity → conflict, replace
            old_content = best.content.lstrip("- ").strip()
            replaced = _replace_in_file(file_path, old_content, content)
            if replaced:
                logger.info(f"Replaced conflicting memory (score={best.score:.2f})")
                return WriteResult("replaced", route.target_file, f"score={best.score:.2f}", route.memory_type)

    # 7. Append new memory
    _ensure_file_with_frontmatter(file_path, route.memory_type, route.importance)
    _append_to_file(file_path, content)
    logger.info(f"Appended new memory to {route.target_file}")
    return WriteResult("appended", route.target_file, "", route.memory_type)


def _route_by_type(memory_type: str) -> RouteResult:
    """Route by explicit memory type."""
    type_map = {
        "preference": ("user/preferences.md", True, 4),
        "instruction": ("user/instructions.md", True, 5),
        "entity": ("user/entities.md", True, 3),
        "decision": ("agent/decisions.md", False, 5),
        "pattern": ("agent/patterns.md", False, 3),
        "event": (f"journal/{date.today().isoformat()}.md", False, 1),
    }
    target, is_global, importance = type_map.get(
        memory_type,
        (f"journal/{date.today().isoformat()}.md", False, 1),
    )
    return RouteResult(target, is_global, memory_type, importance)


def _relative_uri_for_store(file_path: Path, global_root: Path, project_root: Path | None) -> str:
    """Compute the URI as stored in the vector store."""
    try:
        return str(file_path.relative_to(global_root))
    except ValueError:
        pass
    if project_root:
        try:
            return str(file_path.relative_to(project_root / ".openclaw_memory"))
        except ValueError:
            pass
    return str(file_path)
