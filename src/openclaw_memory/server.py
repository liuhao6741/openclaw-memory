"""MCP Server: 3 tools for chat history recording and search."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .storage import append_agent, detect_journal_dir, grep_search, write_turn

# ---------------------------------------------------------------------------
# Lazy state
# ---------------------------------------------------------------------------

_journal_dir = None


def _get_journal_dir():
    global _journal_dir
    if _journal_dir is None:
        _journal_dir = detect_journal_dir()
    return _journal_dir


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="claw-memory",
    instructions=(
        "OpenClaw Memory records your complete chat history to local Markdown files. "
        "At the end of every reply, call memory_log_conversation() with the complete "
        "user message and your entire reply (no truncation). If your reply is very long, "
        "call memory_log_conversation() with the first part, then "
        "memory_log_conversation_append() for remaining parts. "
        "Use memory_search() to find past conversations by keyword."
    ),
)


@mcp.tool()
async def memory_log_conversation(
    user_message: str,
    agent_response: str = "",
    model: str = "",
    code_changes: str = "",
) -> str:
    """Record one full conversation turn to today's journal.

    You MUST pass the **complete** user message and your **entire** reply — no
    truncation, no summary, no "..." or "see above". If your reply is very long,
    pass the first part here then use memory_log_conversation_append() for the rest.

    Args:
        user_message: The user's full message in this turn.
        agent_response: Your full reply (complete text, every paragraph).
        model: The model used for this response (e.g. "claude-4-opus").
        code_changes: Optional. Files created/modified, e.g. "- `src/foo.py` (created)".
    """
    journal_dir = _get_journal_dir()
    path = write_turn(journal_dir, user_message, agent_response, model, code_changes)
    return f"Recorded in {path.name}"


@mcp.tool()
async def memory_log_conversation_append(agent_response_chunk: str) -> str:
    """Append more text to the last Agent section in today's journal.

    Use after memory_log_conversation() when your full reply did not fit in one
    call. Can be called multiple times; each chunk is appended to the same turn.

    Args:
        agent_response_chunk: Next part of your reply to append (no truncation).
    """
    journal_dir = _get_journal_dir()
    ok = append_agent(journal_dir, agent_response_chunk)
    if ok:
        return "Appended to last conversation turn."
    return "No conversation turn found today; call memory_log_conversation first."


@mcp.tool()
async def memory_search(
    query: str,
    since: str = "",
    max_results: int = 0,
) -> str:
    """Search chat history for a keyword, returning full conversation turns.

    Args:
        query: Text to search for (case-insensitive).
        since: Only search from this date onward (YYYY-MM-DD format).
        max_results: Max turns to return (0 = no limit).
    """
    journal_dir = _get_journal_dir()
    results = grep_search(journal_dir, query, since=since, max_results=max_results)

    if not results:
        return f'No matches found for "{query}".'

    parts: list[str] = []
    for r in results:
        header = f"[{r['date']} {r['time']} | {r['model']}]"
        if r["truncated"]:
            header += " (truncated)"
        parts.append(f"{header}\n{r['content']}")

    footer = f"\n\n---\nFound {len(results)} matching conversation(s)."
    if since:
        footer += f" (since {since})"

    return "\n\n---\n\n".join(parts) + footer
