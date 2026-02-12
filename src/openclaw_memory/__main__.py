"""Entry point: python -m openclaw_memory"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="openclaw-memory",
        description="OpenClaw Memory — MCP memory server for AI agents",
    )
    sub = parser.add_subparsers(dest="command")

    # --- serve (default) ---
    serve_p = sub.add_parser("serve", help="Start MCP server (default)")
    serve_p.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    serve_p.add_argument("--port", type=int, default=8765)
    serve_p.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="WARNING")

    # --- init ---
    init_p = sub.add_parser("init", help="Initialize memory for current project (one command setup)")
    init_p.add_argument("--provider", choices=["openai", "ollama", "local"], default=None,
                        help="Embedding provider (default: auto-detect)")
    init_p.add_argument("--name", default="", help="Project name (default: directory name)")
    init_p.add_argument("--global-only", action="store_true", help="Only init global ~/.openclaw_memory/")

    # --- index ---
    sub.add_parser("index", help="Index memory files and exit")

    # --- backward compat: no subcommand = serve ---
    # Also support old --index flag
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8765, help=argparse.SUPPRESS)
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="WARNING", help=argparse.SUPPRESS)
    parser.add_argument("--index", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level if hasattr(args, "log_level") and args.log_level else "WARNING"),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    cmd = args.command

    # Backward compat
    if cmd is None:
        if getattr(args, "index", False):
            cmd = "index"
        else:
            cmd = "serve"

    if cmd == "init":
        _run_init(args)
    elif cmd == "index":
        asyncio.run(_run_index())
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
# init — the one-command setup
# ---------------------------------------------------------------------------

_CURSOR_RULE = """\
---
description: OpenClaw Memory usage guide for agent
globs:
alwaysApply: true
---

## Memory System

You have access to a persistent memory system via MCP tools. Follow these rules:

1. **Session start**: Always call `memory_primer()` first to load context.
2. **During work**: Call `memory_log(content)` when you discover:
   - User preferences ("I prefer...", "Please always...")
   - Technical decisions ("Decided to use...", "Chose...")
   - Reusable patterns ("The solution is...", "Root cause was...")
   - Facts about people/projects/tools
3. **Need to recall**: Call `memory_search(query)` when you need past context.
4. **Session end**: When the user says goodbye or ends the session, call `memory_session_end()` with a structured summary.

Do NOT log: debug steps, code snippets, file paths, uncertain guesses.
"""

_GITIGNORE_CONTENT = """\
# OpenClaw Memory (keep markdown, ignore index)
index.db
index.db-wal
index.db-shm
state.json
"""


def _run_init(args) -> None:
    project_dir = Path.cwd()
    global_root = Path.home() / ".openclaw_memory"
    project_name = args.name or project_dir.name
    provider = args.provider or _detect_provider()
    python_path = sys.executable  # The python running this command

    print(f"OpenClaw Memory — Initializing\n")

    # --- 1. Global ~/.openclaw_memory/ ---
    _init_global(global_root)

    if args.global_only:
        print("\nDone (global only).")
        return

    # --- 2. Project .openclaw_memory/ ---
    _init_project(project_dir, project_name, provider)

    # --- 3. .cursor/mcp.json ---
    _init_cursor_mcp(project_dir, python_path, provider)

    # --- 4. .cursor/rules/memory.mdc ---
    _init_cursor_rule(project_dir)

    # --- 5. .gitignore for .openclaw_memory/ ---
    _init_gitignore(project_dir)

    # --- Summary ---
    print(f"\n{'='*50}")
    print(f"Setup complete!")
    print(f"{'='*50}")
    print(f"  Global memory : {global_root}/")
    print(f"  Project memory: {project_dir}/.openclaw_memory/")
    print(f"  Cursor MCP    : {project_dir}/.cursor/mcp.json")
    print(f"  Cursor Rule   : {project_dir}/.cursor/rules/memory.mdc")
    print(f"  Provider      : {provider}")
    print()

    if provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
        print("  NOTE: Set OPENAI_API_KEY in .cursor/mcp.json or environment.")
    elif provider == "ollama":
        print("  NOTE: Make sure Ollama is running: ollama serve")
        print("        And pull the model: ollama pull nomic-embed-text")

    print(f"\n  Restart Cursor to activate. Agent will auto-use memory tools.")


def _detect_provider() -> str:
    """Auto-detect the best available embedding provider."""
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    # Check if ollama is running
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        return "ollama"
    except Exception:
        pass
    # Check if sentence-transformers is installed
    try:
        import sentence_transformers  # noqa: F401
        return "local"
    except ImportError:
        pass
    # Default to local (will prompt to install)
    return "local"


def _init_global(global_root: Path) -> None:
    """Create global ~/.openclaw_memory/ structure."""
    user_dir = global_root / "user"
    created = []

    if not user_dir.exists():
        user_dir.mkdir(parents=True)
        created.append("~/.openclaw_memory/user/")

    # Template files (only if they don't exist)
    today = date.today().isoformat()

    templates = {
        user_dir / "preferences.md": f"---\ntype: preference\nimportance: 4\nreinforcement: 0\ncreated: '{today}'\nupdated: '{today}'\nstatus: active\n---\n",
        user_dir / "instructions.md": f"---\ntype: instruction\nimportance: 5\nreinforcement: 0\ncreated: '{today}'\nupdated: '{today}'\nstatus: active\n---\n",
        user_dir / "entities.md": f"---\ntype: entity\nimportance: 3\nreinforcement: 0\ncreated: '{today}'\nupdated: '{today}'\nstatus: active\n---\n",
    }

    for path, content in templates.items():
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            created.append(f"  {path.name}")

    if created:
        print(f"[1/5] Global memory initialized: ~/.openclaw_memory/")
        for c in created:
            print(f"       + {c}")
    else:
        print(f"[1/5] Global memory: already exists (skipped)")


def _init_project(project_dir: Path, project_name: str, provider: str) -> None:
    """Create project .openclaw_memory/ structure."""
    oc_dir = project_dir / ".openclaw_memory"
    created = []

    for sub in ["journal", "agent"]:
        d = oc_dir / sub
        if not d.exists():
            d.mkdir(parents=True)
            created.append(sub + "/")

    # .openclaw_memory.toml at project root
    toml_path = project_dir / ".openclaw_memory.toml"
    if not toml_path.exists():
        toml_content = f'[project]\nname = "{project_name}"\ndescription = ""\n\n[embedding]\nprovider = "{provider}"\n\n[privacy]\nenabled = true\n\n[search]\ndefault_max_tokens = 1500\n'
        toml_path.write_text(toml_content, encoding="utf-8")
        created.append(".openclaw_memory.toml")

    # TASKS.md
    tasks_path = oc_dir / "TASKS.md"
    if not tasks_path.exists():
        tasks_path.write_text("---\ntype: tasks\nupdated: ''\n---\n", encoding="utf-8")
        created.append("TASKS.md")

    if created:
        print(f"[2/5] Project memory initialized: .openclaw_memory/")
        for c in created:
            print(f"       + {c}")
    else:
        print(f"[2/5] Project memory: already exists (skipped)")


def _init_cursor_mcp(project_dir: Path, python_path: str, provider: str) -> None:
    """Create or update .cursor/mcp.json."""
    cursor_dir = project_dir / ".cursor"
    cursor_dir.mkdir(exist_ok=True)
    mcp_path = cursor_dir / "mcp.json"

    # Build env
    env: dict[str, str] = {"OPENCLAW_EMBEDDING_PROVIDER": provider}
    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if api_key:
            env["OPENAI_API_KEY"] = api_key
        else:
            env["OPENAI_API_KEY"] = "YOUR_API_KEY_HERE"
    elif provider == "local":
        # Force offline mode — model already cached, skip slow HuggingFace Hub check
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"

    new_server = {
        "command": python_path,
        "args": ["-m", "openclaw_memory"],
        "env": env,
    }

    if mcp_path.exists():
        try:
            existing = json.loads(mcp_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
    else:
        existing = {}

    existing.setdefault("mcpServers", {})
    existing["mcpServers"]["openclaw-memory"] = new_server
    mcp_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[3/5] Cursor MCP config: .cursor/mcp.json")


def _init_cursor_rule(project_dir: Path) -> None:
    """Create .cursor/rules/memory.mdc."""
    rules_dir = project_dir / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rule_path = rules_dir / "memory.mdc"

    if not rule_path.exists():
        rule_path.write_text(_CURSOR_RULE, encoding="utf-8")
        print(f"[4/5] Cursor Rule created: .cursor/rules/memory.mdc")
    else:
        print(f"[4/5] Cursor Rule: already exists (skipped)")


def _init_gitignore(project_dir: Path) -> None:
    """Add .openclaw_memory gitignore entries."""
    gi_path = project_dir / ".openclaw_memory" / ".gitignore"
    if not gi_path.exists():
        gi_path.write_text(_GITIGNORE_CONTENT, encoding="utf-8")
        print(f"[5/5] Gitignore: .openclaw_memory/.gitignore")
    else:
        print(f"[5/5] Gitignore: already exists (skipped)")


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

async def _run_index() -> None:
    """Run one-shot indexing of memory files."""
    from .config import ensure_directories, load_config
    from .embeddings import get_provider
    from .indexer import index_directory
    from .store import VectorStore

    cfg = load_config()
    ensure_directories(cfg)
    embedder = get_provider(cfg.embedding)

    # Index global memory
    print(f"Indexing global memory: {cfg.global_root}")
    global_store = VectorStore(cfg.global_index_db, dimension=embedder.dimension)
    results = await index_directory(cfg.global_root, global_store, embedder)
    total = sum(results.values())
    print(f"  Indexed {total} chunks from {len(results)} files")

    # Index project memory
    if cfg.project_memory_dir:
        print(f"Indexing project memory: {cfg.project_memory_dir}")
        project_store = VectorStore(cfg.project_index_db, dimension=embedder.dimension)
        results = await index_directory(cfg.project_memory_dir, project_store, embedder)
        total = sum(results.values())
        print(f"  Indexed {total} chunks from {len(results)} files")

    print("Done.")


if __name__ == "__main__":
    main()
