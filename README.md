# OpenClaw Memory

A lightweight MCP memory server designed for AI agents. Markdown files as the single source of truth, zero external dependencies.

[中文文档](README_CN.md)

## Features

- **Markdown-first** — All memories stored as human-readable Markdown files, git-friendly
- **Zero dependencies** — Pure Python + SQLite, no external services required
- **Smart writing** — Quality gate, auto-routing, conflict detection, reinforcement counting
- **Salience-based retrieval** — Multi-dimensional scoring: semantic similarity + reinforcement + recency + access frequency
- **Token budget aware** — Never exceed your context window budget
- **Session primer** — Cold-start with structured context in ~500 tokens
- **Project isolation** — Global user memory + per-project working memory
- **Privacy protection** — Regex-based sensitive information filtering
- **V1 zero LLM dependency** — Only requires an embedding model (local option available)

## Quick Start

### 1. Install + Initialize (two commands)

```bash
# Step 1: Install (run once in the claw-memory source directory)
cd /path/to/claw-memory
pip install -e ".[local]"          # Local embedding (works offline)
# or pip install -e ".[openai]"   # OpenAI embedding (more accurate)

# Step 2: Initialize in any project, then restart Cursor
cd /path/to/your/project
claw-memory init
```

> **Note**: If `pip` is not in your PATH (e.g. using pyenv), use the virtualenv pip:
> `/path/to/claw-memory/.venv/bin/pip install -e ".[local]"`,
> then run init with `.venv/bin/claw-memory init`.

**The `init` command automatically handles all configuration:**

- Creates `~/.openclaw_memory/user/` global memory directory with template files
- Creates `.openclaw_memory/` project memory directory (journal, agent, etc.)
- Creates `.openclaw_memory.toml` project config (auto-detects embedding provider)
- Creates `.cursor/mcp.json` MCP server configuration
- Creates `.cursor/rules/memory.mdc` agent usage guide
- Creates `.openclaw_memory/.gitignore` (keeps index files out of git)

Optional flags:

```bash
# Specify embedding provider
claw-memory init --provider openai

# Specify project name
claw-memory init --name "my-awesome-project"

# Initialize global memory only (skip project-level files)
claw-memory init --global-only
```

**After init completes, restart Cursor and the agent will automatically use the memory tools.**

### Other Commands

```bash
# Start MCP server (usually called automatically by Cursor)
claw-memory serve

# SSE mode (for web clients)
claw-memory serve --transport sse --port 8765

# One-shot index of existing memory files
claw-memory index
```

### Manual Configuration (optional)

If you prefer not to use `init`, you can manually create `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "claw-memory": {
      "command": "python",
      "args": ["-m", "openclaw_memory"],
      "env": {
        "OPENCLAW_EMBEDDING_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

## MCP Tools

| Tool | When to use | Description |
|------|-------------|-------------|
| `memory_primer()` | Start of every session | Returns structured context: user identity, project info, preferences, recent activity, active tasks |
| `memory_search(query, scope?, max_tokens?)` | When you need to recall specific information | Semantic search with salience scoring and token budget control |
| `memory_log(content, type?)` | When you discover information worth remembering | Auto-classifies, deduplicates, detects conflicts, and routes to the right file |
| `memory_session_end(summary)` | End of session | Writes structured session summary, updates tasks and primer |
| `memory_update_tasks(tasks_json)` | When task status changes | Updates TASKS.md and primer |
| `memory_read(path)` | When you need full file content | Reads and returns complete Markdown file |

## How It Works

### Memory Directory Structure

```
~/.openclaw_memory/              # Global (cross-project)
├── config.toml                  # Global configuration
├── user/
│   ├── preferences.md           # Your preferences
│   ├── instructions.md          # Your rules for the agent
│   └── entities.md              # People, tools, projects
└── index.db                     # Global vector index

<project>/.openclaw_memory/      # Per-project
├── .openclaw_memory.toml        # Project configuration
├── PRIMER.md                    # Auto-maintained session primer
├── TASKS.md                     # Active task tracking
├── journal/YYYY-MM-DD.md        # Structured daily session logs
├── agent/
│   ├── patterns.md              # Reusable solution patterns
│   └── decisions.md             # Architecture decisions (ADRs)
└── index.db                     # Project vector index
```

### Smart Writing Pipeline

```
Input --> Quality Gate --> Privacy Filter --> Smart Router --> Reinforcement/Conflict Check --> Write
```

1. **Quality Gate**: Rejects noise (too short, filler phrases, pure code, speculation)
2. **Privacy Filter**: Blocks API keys, passwords, internal IPs (configurable regex)
3. **Smart Router**: Auto-classifies content to the right file by keyword patterns
4. **Reinforcement**: If highly similar memory exists (>0.92), increments reinforcement count instead of duplicating
5. **Conflict Detection**: If similar memory exists (0.85-0.92) with new info, replaces the old entry

### Salience-Based Retrieval

```
salience = 0.50 * semantic_similarity
         + 0.20 * reinforcement_score
         + 0.20 * recency_decay
         + 0.10 * access_frequency
```

Memories that are frequently mentioned (high reinforcement), recently updated, and often recalled naturally rank higher — no manual importance tuning needed.

### Token Budget

```python
# Returns as many relevant memories as fit within 1500 tokens
results = memory_search("webhook handling", max_tokens=1500)
```

## Configuration

### Project config (`.openclaw_memory.toml`)

```toml
[project]
name = "my-project"
description = "E-commerce platform"

[embedding]
provider = "openai"              # openai | ollama | local
model = "text-embedding-3-small" # optional, uses provider default

[privacy]
enabled = true
patterns = [
    'sk-[a-zA-Z0-9]{20,}',
    'ghp_[a-zA-Z0-9]{36}',
    'password\s*[:=]\s*\S+',
]

[search]
default_max_tokens = 1500
recency_half_life_days = 30
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENCLAW_EMBEDDING_PROVIDER` | Embedding provider | `local` |
| `OPENCLAW_EMBEDDING_MODEL` | Model name | Provider default |
| `OPENCLAW_MEMORY_ROOT` | Override memory root path | Auto-detect |
| `OPENAI_API_KEY` | OpenAI API key | — |

## Embedding Providers

| Provider | Dimension | Dependency | Use case |
|----------|-----------|------------|----------|
| OpenAI `text-embedding-3-small` | 1536 | API key | Best accuracy |
| Ollama `nomic-embed-text` | 768 | Local Ollama | Offline / privacy |
| sentence-transformers `all-MiniLM-L6-v2` | 384 | Pure local | Zero dependency |

## Design Decisions

This project was designed by analyzing four existing memory systems:

- **memsearch**: Markdown as source of truth, content-hash dedup, hybrid search
- **OpenViking**: Directory-based organization, L0/L1/L2 progressive loading
- **memU**: Reinforcement counting for importance, salience scoring formula
- **claude-mem**: Structured session summaries, project isolation, privacy tags

Key differences from all four:

- **Zero LLM dependency** in V1 (only embedding model needed)
- **Zero external service dependency** (pure Python + SQLite)
- **Smart writing pipeline** replaces LLM-based extraction with rule-based routing
- **Reinforcement + rules** hybrid for importance (data-driven + heuristic)
- **Token budget aware** retrieval (none of the four do this)

## License

Apache-2.0. See [LICENSE](LICENSE) for details.
