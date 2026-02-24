"""Storage module: read/write journal files and grep search. Pure stdlib, no external deps."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Project root detection
# ---------------------------------------------------------------------------


def detect_journal_dir(cwd: Path | None = None) -> Path:
    """Detect the journal directory for the current project.

    Priority:
    1. OPENCLAW_MEMORY_DIR environment variable
    2. Walk up from cwd looking for .openclaw_memory/ directory
    3. Git root fallback
    4. cwd itself
    """
    env = os.environ.get("OPENCLAW_MEMORY_DIR")
    if env:
        return Path(env) / "journal"

    cwd = cwd or Path.cwd()

    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".openclaw_memory"
        if candidate.is_dir():
            return candidate / "journal"

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=str(cwd), timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()) / ".openclaw_memory" / "journal"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return cwd / ".openclaw_memory" / "journal"


def scan_journal_dirs(parent: Path, max_depth: int = 4) -> dict[str, Path]:
    """Recursively scan *parent* for projects containing .openclaw_memory/journal/.

    Returns ``{project_name: journal_dir_path}``.  The project name is the
    directory name that contains ``.openclaw_memory``.
    """
    found: dict[str, Path] = {}

    def _walk(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(directory.iterdir())
        except PermissionError:
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name.startswith(".") and entry.name != ".openclaw_memory":
                continue
            if entry.name == ".openclaw_memory":
                journal = entry / "journal"
                if journal.is_dir():
                    project_name = entry.parent.name or str(entry.parent)
                    found[project_name] = journal
            else:
                _walk(entry, depth + 1)

    _walk(parent, 0)
    return found


def ensure_journal_dir(journal_dir: Path) -> None:
    """Create journal directory if it doesn't exist."""
    journal_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Write conversation turn
# ---------------------------------------------------------------------------

_TURN_COUNTER: dict[str, int] = {}  # date -> counter for current process


def write_turn(
    journal_dir: Path,
    user_message: str,
    agent_response: str,
    model: str = "",
    code_changes: str = "",
) -> Path:
    """Append one conversation turn to today's journal file.

    Returns the path to the journal file.
    """
    ensure_journal_dir(journal_dir)

    today = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H:%M")
    journal_path = journal_dir / f"{today}.md"

    model_str = model or "unknown"
    header = f"## {time_str} | {model_str}"

    lines = [header, "", "### User", ""]
    lines.append(user_message.strip() if user_message else "(empty)")
    lines.append("")
    lines.append("### Agent")
    lines.append("")
    lines.append(agent_response.strip() if agent_response else "(empty)")

    if code_changes and code_changes.strip():
        lines.append("")
        lines.append("### Code Changes")
        lines.append("")
        lines.append(code_changes.strip())

    lines.append("")

    block = "\n".join(lines)

    if journal_path.is_file() and journal_path.stat().st_size > 0:
        with open(journal_path, "a", encoding="utf-8") as f:
            f.write("\n---\n\n")
            f.write(block)
    else:
        with open(journal_path, "w", encoding="utf-8") as f:
            f.write(block)

    return journal_path


# ---------------------------------------------------------------------------
# Append to last agent section
# ---------------------------------------------------------------------------


def append_agent(journal_dir: Path, chunk: str) -> bool:
    """Append text to the last '### Agent' section in today's journal.

    Returns True if appended successfully, False if no journal/section found.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    journal_path = journal_dir / f"{today}.md"

    if not journal_path.is_file():
        return False

    chunk = (chunk or "").strip()
    if not chunk:
        return True

    content = journal_path.read_text(encoding="utf-8")

    marker = "### Agent\n"
    idx = content.rfind(marker)
    if idx == -1:
        return False

    start = idx + len(marker)

    # Find the end of this turn: next "---" separator or next "## " heading or EOF
    rest = content[start:]
    end_offset = len(content)
    for pattern in ["\n---\n", "\n## "]:
        pos = rest.find(pattern)
        if pos != -1:
            end_offset = min(end_offset, start + pos)

    existing = content[start:end_offset].rstrip()
    if existing:
        new_section = existing + "\n\n" + chunk
    else:
        new_section = "\n" + chunk

    new_content = content[:start] + new_section + content[end_offset:]
    journal_path.write_text(new_content, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Grep search
# ---------------------------------------------------------------------------

_TURN_HEADER_RE = re.compile(r"^## (\d{2}:\d{2}) \| (.+)$")
_SEPARATOR_RE = re.compile(r"^---$")

MAX_AGENT_DISPLAY = 2000  # truncate long Agent sections in search results


def grep_search(
    journal_dir: Path,
    query: str,
    since: str = "",
    max_results: int = 0,
) -> list[dict]:
    """Search journal files for query, returning full conversation turns.

    Args:
        journal_dir: Path to the journal directory.
        query: Text to search for (case-insensitive).
        since: Only search files from this date onward (YYYY-MM-DD).
        max_results: Max number of matching turns to return (0 = no limit).

    Returns list of dicts with keys: date, time, model, content, file, truncated.
    """
    if not journal_dir.is_dir():
        return []

    query_lower = query.lower()
    md_files = sorted(journal_dir.glob("*.md"))

    if since:
        md_files = [f for f in md_files if f.stem >= since]

    results: list[dict] = []

    for md_file in md_files:
        date_str = md_file.stem
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        turns = _parse_turns(content, date_str, md_file.name)

        for turn in turns:
            if query_lower in turn["content"].lower():
                # Truncate long Agent sections for display
                truncated = False
                display_content = turn["content"]
                agent_marker = "### Agent\n"
                agent_idx = display_content.find(agent_marker)
                if agent_idx != -1:
                    agent_start = agent_idx + len(agent_marker)
                    # Find where agent section ends
                    next_section = -1
                    for sec in ["### Code Changes", "### User"]:
                        pos = display_content.find(sec, agent_start)
                        if pos != -1:
                            next_section = pos if next_section == -1 else min(next_section, pos)

                    agent_end = next_section if next_section != -1 else len(display_content)
                    agent_text = display_content[agent_start:agent_end]

                    if len(agent_text) > MAX_AGENT_DISPLAY:
                        truncated = True
                        short = agent_text[:MAX_AGENT_DISPLAY].rstrip()
                        display_content = (
                            display_content[:agent_start]
                            + short
                            + f"\n\n[...truncated, full content in {md_file.name}]"
                            + display_content[agent_end:]
                        )

                results.append({
                    "date": date_str,
                    "time": turn["time"],
                    "model": turn["model"],
                    "content": display_content,
                    "file": md_file.name,
                    "truncated": truncated,
                })

                if max_results and len(results) >= max_results:
                    return results

    return results


def _parse_turns(content: str, date_str: str, filename: str) -> list[dict]:
    """Parse a journal file into individual conversation turns."""
    turns: list[dict] = []
    lines = content.split("\n")

    current_turn_lines: list[str] = []
    current_time = ""
    current_model = ""

    for line in lines:
        header_match = _TURN_HEADER_RE.match(line)
        sep_match = _SEPARATOR_RE.match(line.strip())

        if header_match:
            # Save previous turn
            if current_turn_lines:
                turns.append({
                    "time": current_time,
                    "model": current_model,
                    "content": "\n".join(current_turn_lines).strip(),
                })

            current_time = header_match.group(1)
            current_model = header_match.group(2)
            current_turn_lines = [line]
        elif sep_match and current_turn_lines:
            # Turn separator — save current turn
            turns.append({
                "time": current_time,
                "model": current_model,
                "content": "\n".join(current_turn_lines).strip(),
            })
            current_turn_lines = []
            current_time = ""
            current_model = ""
        elif current_turn_lines:
            current_turn_lines.append(line)

    # Save last turn
    if current_turn_lines:
        turns.append({
            "time": current_time,
            "model": current_model,
            "content": "\n".join(current_turn_lines).strip(),
        })

    return turns
