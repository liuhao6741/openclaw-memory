"""Entry point: python -m openclaw_memory"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="claw-memory",
        description="OpenClaw Memory — record and search AI chat history",
    )
    sub = parser.add_subparsers(dest="command")

    # serve
    serve_p = sub.add_parser("serve", help="Start MCP server (default)")
    serve_p.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    serve_p.add_argument("--port", type=int, default=8765)

    # init
    sub.add_parser("init", help="Initialize memory for current project")

    # web
    web_p = sub.add_parser("web", help="Open chat history viewer in browser")
    web_p.add_argument("--host", default="127.0.0.1")
    web_p.add_argument("--port", type=int, default=8767)
    web_p.add_argument("--no-open", action="store_true", help="Don't auto-open browser")
    web_p.add_argument(
        "--scan-dir", type=str, default="",
        help="Parent directory to scan for multiple projects",
    )

    args = parser.parse_args()
    cmd = args.command or "serve"

    if cmd == "init":
        _run_init()
    elif cmd == "web":
        _run_web(args)
    else:
        _run_serve(args)


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

def _run_serve(args) -> None:
    from .server import mcp
    transport = getattr(args, "transport", "stdio") or "stdio"
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        port = getattr(args, "port", 8765) or 8765
        mcp.run(transport="sse", sse_params={"port": port})


# ---------------------------------------------------------------------------
# web
# ---------------------------------------------------------------------------

def _run_web(args) -> None:
    from .web import run_web
    from .storage import detect_journal_dir, scan_journal_dirs

    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8767)
    open_browser = not getattr(args, "no_open", False)
    scan_dir = getattr(args, "scan_dir", "") or ""

    if scan_dir:
        projects = scan_journal_dirs(Path(scan_dir))
        if not projects:
            print(f"No projects found under {scan_dir}")
            sys.exit(1)
    else:
        journal_dir = detect_journal_dir()
        project_name = journal_dir.parent.parent.name or "default"
        projects = {project_name: journal_dir}

    run_web(projects=projects, host=host, port=port, open_browser=open_browser)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

_CURSOR_RULE = """\
---
description: OpenClaw Memory — auto-record chat history
globs:
alwaysApply: true
---

## Chat History Recording

You have access to a chat history system via MCP tools (claw-memory).
It automatically records every conversation turn to local Markdown files.

### Rules

**At the end of every reply**, call `memory_log_conversation()`:
- `user_message`: The **complete** user message (full text, no truncation).
- `agent_response`: Your **complete** reply (every paragraph, no "..." or summary).
- `model`: The model you are using (e.g. "claude-4-opus").
- `code_changes`: If you created or modified files, list them (e.g. "- `src/foo.py` (created)").

If your reply is very long, call `memory_log_conversation(user_message, first_part)` then
`memory_log_conversation_append(remaining_part)` for each remaining chunk.

### Search

Use `memory_search(query)` when:
- User mentions "before", "last time", "remember", "we discussed"
- You need to recall a past conversation
- Use `since="YYYY-MM-DD"` to narrow by date
"""

_GITIGNORE_CONTENT = """\
# OpenClaw Memory — chat history (may contain sensitive content)
*
!.gitignore
"""


def _run_init() -> None:
    project_dir = Path.cwd()
    python_path = sys.executable

    print("OpenClaw Memory — Initializing\n")

    # 1. Create .openclaw_memory/journal/
    journal_dir = project_dir / ".openclaw_memory" / "journal"
    if not journal_dir.exists():
        journal_dir.mkdir(parents=True)
        print("[1/4] Created .openclaw_memory/journal/")
    else:
        print("[1/4] .openclaw_memory/journal/ already exists")

    # 2. Create .openclaw_memory/.gitignore
    gi_path = project_dir / ".openclaw_memory" / ".gitignore"
    if not gi_path.exists():
        gi_path.write_text(_GITIGNORE_CONTENT, encoding="utf-8")
        print("[2/4] Created .openclaw_memory/.gitignore")
    else:
        print("[2/4] .gitignore already exists")

    # 3. Create/update .cursor/mcp.json
    cursor_dir = project_dir / ".cursor"
    cursor_dir.mkdir(exist_ok=True)
    mcp_path = cursor_dir / "mcp.json"

    new_server = {
        "command": python_path,
        "args": ["-m", "openclaw_memory"],
        "disabled": False,
    }

    if mcp_path.exists():
        try:
            existing = json.loads(mcp_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
    else:
        existing = {}

    existing.setdefault("mcpServers", {})
    existing["mcpServers"]["claw-memory"] = new_server
    mcp_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("[3/4] Created .cursor/mcp.json")

    # 4. Create .cursor/rules/memory.mdc
    rules_dir = cursor_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rule_path = rules_dir / "memory.mdc"
    if not rule_path.exists():
        rule_path.write_text(_CURSOR_RULE, encoding="utf-8")
        print("[4/4] Created .cursor/rules/memory.mdc")
    else:
        print("[4/4] memory.mdc already exists")

    print(f"\n{'=' * 50}")
    print("Setup complete!")
    print(f"{'=' * 50}")
    print(f"  Chat history: .openclaw_memory/journal/")
    print(f"  Cursor MCP  : .cursor/mcp.json")
    print(f"  Agent rules : .cursor/rules/memory.mdc")
    print(f"\n  Restart Cursor to activate.")


if __name__ == "__main__":
    main()
