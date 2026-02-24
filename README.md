# OpenClaw Memory

**Your AI conversations disappear after every session. OpenClaw Memory fixes that.**

Every time you chat with an AI coding assistant, valuable context — decisions, solutions, debugging steps — vanishes when the session ends. The next session starts from zero.

OpenClaw Memory automatically records every conversation turn to local Markdown files, making your entire AI chat history searchable and browsable. No cloud, no database — just plain text files in your project.

## How It Works

```
You chat with AI  →  Every turn auto-saved to .openclaw_memory/journal/2026-02-24.md
                  →  Search past conversations via MCP tool or web viewer
```

Each journal entry captures the complete conversation: timestamps, model used, your input, the AI's full response, and any code changes made.

## Quick Start

**1. Install**

```bash
pip install claw-memory
```

**2. Initialize in your project**

```bash
cd your-project
claw-memory init
```

This creates:
- `.openclaw_memory/journal/` — where chat history lives
- `.cursor/mcp.json` — connects the MCP server to Cursor
- `.cursor/rules/memory.mdc` — tells the AI agent to auto-record

**3. Restart Cursor** — that's it. Every conversation is now being recorded.

## Searching Past Conversations

The AI agent can search your history automatically. Just ask naturally:

> "We discussed this before, what was the solution?"
>
> "Last time we fixed a similar bug, how did we do it?"

The agent will call `memory_search()` behind the scenes and find matching conversations.

### Search via Web Viewer

```bash
# Single project (current directory)
claw-memory web

# Multiple projects — scan a parent directory
claw-memory web --scan-dir ~/projects
```

Opens a browser-based viewer where you can:
- Browse journal files by date
- Full-text search across all conversations
- Dark/light mode
- **Multi-project view**: use `--scan-dir` to scan a parent directory and browse all projects in one place, with sidebar grouped by project

## What Gets Recorded

Each conversation turn is saved as Markdown:

```markdown
## 14:32 | claude-4-opus

### User

How do I fix the N+1 query problem in the user list endpoint?

### Agent

The issue is in `api/users.py` where each user triggers a separate query for their roles...

### Code Changes

- `api/users.py` (modified)
- `tests/test_users.py` (modified)
```

## MCP Tools

| Tool | Purpose |
|---|---|
| `memory_log_conversation` | Record a complete conversation turn |
| `memory_log_conversation_append` | Append to the last turn (for long responses) |
| `memory_search` | Search chat history by keyword |

## Storage

All data is stored locally in `.openclaw_memory/journal/` as plain Markdown files — one file per day. No database, no cloud sync. You own your data.

The `.openclaw_memory/` directory is auto-gitignored to prevent accidental commits of chat history.

## Project Isolation

Each project gets its own `.openclaw_memory/` directory. MCP tools always operate on the current project only.

To view multiple projects together, use the web viewer with `--scan-dir`.

## License

Apache 2.0
