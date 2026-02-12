"""Web viewer for OpenClaw Memory — browse memories in your browser."""

from __future__ import annotations

import json
import logging
import re
import threading
import webbrowser
from datetime import date
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import OpenClawConfig, load_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Memory browser logic
# ---------------------------------------------------------------------------


class MemoryBrowser:
    """Read-only browser for memory files."""

    def __init__(self, config: OpenClawConfig) -> None:
        self.config = config

    def get_tree(self) -> dict[str, Any]:
        """Build the file tree structure for global and project memories."""
        tree: dict[str, Any] = {"global": {}, "project": {}}

        # Global memory
        global_root = self.config.global_root
        if global_root.is_dir():
            tree["global"] = self._scan_dir(global_root, "global", global_root)

        # Project memory
        project_dir = self.config.project_memory_dir
        if project_dir and project_dir.is_dir():
            tree["project"] = self._scan_dir(project_dir, "project", project_dir)

        return tree

    def _scan_dir(self, dir_path: Path, scope: str, root: Path) -> dict[str, Any]:
        """Scan a directory and return its structure.

        Args:
            dir_path: Current directory being scanned.
            scope: 'global' or 'project'.
            root: The original root directory (for computing relative paths).
        """
        result: dict[str, Any] = {"files": [], "dirs": {}}

        if not dir_path.is_dir():
            return result

        for item in sorted(dir_path.iterdir()):
            if item.name.startswith(".") and item.name != ".openclaw_memory.toml":
                continue
            if item.name in ("index.db", "index.db-wal", "index.db-shm", "state.json"):
                continue

            if item.is_file() and item.suffix == ".md":
                rel = item.relative_to(root)
                result["files"].append({
                    "name": item.name,
                    "path": f"{scope}/{rel}",
                    "size": item.stat().st_size,
                    "modified": item.stat().st_mtime,
                })
            elif item.is_dir():
                sub = self._scan_dir(item, scope, root)
                if sub["files"] or sub["dirs"]:
                    result["dirs"][item.name] = sub

        return result

    def read_file(self, path: str) -> dict[str, Any]:
        """Read a memory file and return its content with metadata."""
        resolved = self._resolve_path(path)
        if resolved is None or not resolved.is_file():
            return {"error": f"File not found: {path}"}

        content = resolved.read_text(encoding="utf-8")

        # Parse frontmatter
        metadata: dict[str, Any] = {}
        body = content
        try:
            import frontmatter
            post = frontmatter.loads(content)
            metadata = dict(post.metadata)
            body = post.content
        except Exception:
            pass

        return {
            "path": path,
            "filename": resolved.name,
            "content": body,
            "metadata": metadata,
            "raw": content,
        }

    def search(self, query: str) -> list[dict[str, Any]]:
        """Simple text search across all memory files."""
        results: list[dict[str, Any]] = []
        query_lower = query.lower()

        # Search all roots
        roots: list[tuple[str, Path]] = [("global", self.config.global_root)]
        if self.config.project_memory_dir:
            roots.append(("project", self.config.project_memory_dir))

        for scope, root in roots:
            if not root.is_dir():
                continue
            for md_file in root.rglob("*.md"):
                if md_file.name.startswith("."):
                    continue
                try:
                    content = md_file.read_text(encoding="utf-8")
                except Exception:
                    continue

                if query_lower in content.lower():
                    rel = md_file.relative_to(root)
                    # Find matching lines with context
                    matches = self._find_matches(content, query_lower)
                    results.append({
                        "path": f"{scope}/{rel}",
                        "filename": md_file.name,
                        "matches": matches,
                        "match_count": len(matches),
                    })

        # Sort by match count descending
        results.sort(key=lambda x: x["match_count"], reverse=True)
        return results

    def _find_matches(self, content: str, query_lower: str, context_lines: int = 1) -> list[str]:
        """Find matching lines with surrounding context."""
        lines = content.split("\n")
        matches: list[str] = []

        for i, line in enumerate(lines):
            if query_lower in line.lower():
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                snippet = "\n".join(lines[start:end]).strip()
                if snippet and len(matches) < 5:  # Max 5 matches per file
                    matches.append(snippet)

        return matches

    def get_config_info(self) -> dict[str, Any]:
        """Return configuration information for display."""
        cfg = self.config
        return {
            "project_name": cfg.project.name or "(unnamed)",
            "project_description": cfg.project.description or "",
            "global_root": str(cfg.global_root),
            "project_root": str(cfg.project_root) if cfg.project_root else None,
            "project_memory_dir": str(cfg.project_memory_dir) if cfg.project_memory_dir else None,
            "embedding_provider": cfg.embedding.provider,
        }

    def _resolve_path(self, path: str) -> Path | None:
        """Resolve a scoped path like 'global/user/preferences.md' to filesystem path."""
        if not path:
            return None

        parts = path.split("/", maxsplit=1)
        if len(parts) != 2:
            return None

        scope, rel_path = parts

        if scope == "global":
            resolved = self.config.global_root / rel_path
        elif scope == "project" and self.config.project_memory_dir:
            resolved = self.config.project_memory_dir / rel_path
        else:
            return None

        # Security: ensure resolved path is under the expected root
        try:
            if scope == "global":
                resolved.resolve().relative_to(self.config.global_root.resolve())
            else:
                resolved.resolve().relative_to(self.config.project_memory_dir.resolve())
        except (ValueError, AttributeError):
            return None

        return resolved


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------


class MemoryWebHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the memory web viewer."""

    browser: MemoryBrowser  # Set by factory

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self._send_html(_HTML_PAGE)
        elif path == "/api/tree":
            self._send_json(self.browser.get_tree())
        elif path == "/api/file":
            file_path = params.get("path", [""])[0]
            self._send_json(self.browser.read_file(file_path))
        elif path == "/api/search":
            query = params.get("q", [""])[0]
            if not query:
                self._send_json([])
            else:
                self._send_json(self.browser.search(query))
        elif path == "/api/config":
            self._send_json(self.browser.get_config_info())
        else:
            self.send_error(404)

    def _send_json(self, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default access logs, use logger instead."""
        logger.debug(format, *args)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_web(host: str = "127.0.0.1", port: int = 8767, open_browser: bool = True) -> None:
    """Start the memory web viewer server."""
    config = load_config()
    browser = MemoryBrowser(config)

    # Create handler class with browser attached
    handler_class = type(
        "Handler",
        (MemoryWebHandler,),
        {"browser": browser},
    )

    server = HTTPServer((host, port), handler_class)
    url = f"http://{host}:{port}"

    print(f"\n  OpenClaw Memory Viewer")
    print(f"  {'─' * 40}")
    print(f"  URL      : {url}")
    print(f"  Global   : {config.global_root}")
    if config.project_root:
        print(f"  Project  : {config.project_root}")
    print(f"  {'─' * 40}")
    print(f"  Press Ctrl+C to stop.\n")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        server.shutdown()


# ---------------------------------------------------------------------------
# HTML Template (Single Page Application)
# ---------------------------------------------------------------------------

_HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenClaw Memory Viewer</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/highlight.js@11/styles/github.min.css" id="hljs-light">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/highlight.js@11/styles/github-dark.min.css" id="hljs-dark" disabled>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/highlight.js@11"></script>
<style>
:root {
  --bg: #ffffff;
  --bg-secondary: #f6f8fa;
  --bg-tertiary: #eef1f5;
  --text: #1f2328;
  --text-secondary: #656d76;
  --text-tertiary: #8b949e;
  --border: #d1d9e0;
  --border-light: #e8ecf0;
  --accent: #0969da;
  --accent-light: #ddf4ff;
  --accent-hover: #0550ae;
  --success: #1a7f37;
  --warning: #9a6700;
  --danger: #d1242f;
  --sidebar-width: 280px;
  --header-height: 56px;
  --radius: 8px;
  --shadow: 0 1px 3px rgba(0,0,0,0.08);
  --shadow-lg: 0 4px 12px rgba(0,0,0,0.1);
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif;
  --font-mono: "SF Mono", "Fira Code", Consolas, "Liberation Mono", Menlo, monospace;
  --transition: 0.2s ease;
}

[data-theme="dark"] {
  --bg: #0d1117;
  --bg-secondary: #161b22;
  --bg-tertiary: #21262d;
  --text: #e6edf3;
  --text-secondary: #8b949e;
  --text-tertiary: #6e7681;
  --border: #30363d;
  --border-light: #262c36;
  --accent: #58a6ff;
  --accent-light: #1c3a5c;
  --accent-hover: #79c0ff;
  --success: #3fb950;
  --warning: #d29922;
  --danger: #f85149;
  --shadow: 0 1px 3px rgba(0,0,0,0.3);
  --shadow-lg: 0 4px 12px rgba(0,0,0,0.4);
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: var(--font);
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  overflow: hidden;
  height: 100vh;
}

/* --- Header --- */
.header {
  position: fixed;
  top: 0; left: 0; right: 0;
  height: var(--header-height);
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  padding: 0 20px;
  z-index: 100;
  gap: 16px;
}

.header-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
  font-size: 15px;
  color: var(--text);
  white-space: nowrap;
  flex-shrink: 0;
}

.header-logo svg {
  width: 24px; height: 24px;
  fill: var(--accent);
}

.search-container {
  flex: 1;
  max-width: 560px;
  position: relative;
}

.search-input {
  width: 100%;
  height: 36px;
  padding: 0 12px 0 36px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  font-family: var(--font);
  outline: none;
  transition: border-color var(--transition), box-shadow var(--transition);
}

.search-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-light);
}

.search-icon {
  position: absolute;
  left: 10px; top: 50%;
  transform: translateY(-50%);
  color: var(--text-tertiary);
  pointer-events: none;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.btn-icon {
  width: 36px; height: 36px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--transition);
}

.btn-icon:hover {
  background: var(--bg-tertiary);
  color: var(--text);
}

/* --- Layout --- */
.layout {
  display: flex;
  margin-top: var(--header-height);
  height: calc(100vh - var(--header-height));
}

/* --- Sidebar --- */
.sidebar {
  width: var(--sidebar-width);
  min-width: var(--sidebar-width);
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  overflow-y: auto;
  padding: 12px 0;
  flex-shrink: 0;
}

.sidebar-section {
  margin-bottom: 4px;
}

.sidebar-heading {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 16px;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
}

.sidebar-heading:hover {
  color: var(--text);
}

.sidebar-heading .arrow {
  transition: transform var(--transition);
  font-size: 10px;
}

.sidebar-heading.collapsed .arrow {
  transform: rotate(-90deg);
}

.sidebar-group {
  overflow: hidden;
  transition: max-height 0.3s ease;
}

.sidebar-group.collapsed {
  max-height: 0 !important;
}

.sidebar-dir {
  padding: 0;
}

.sidebar-dir-label {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 16px 4px 24px;
  font-size: 13px;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
}

.sidebar-dir-label:hover {
  color: var(--text);
  background: var(--bg-tertiary);
}

.sidebar-dir-label .dir-icon {
  font-size: 14px;
}

.sidebar-file {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 16px 4px 40px;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
  text-decoration: none;
  border-radius: 0;
  transition: all var(--transition);
}

.sidebar-file:hover {
  background: var(--bg-tertiary);
}

.sidebar-file.active {
  background: var(--accent-light);
  color: var(--accent);
  font-weight: 500;
}

.sidebar-file .file-icon {
  font-size: 14px;
  flex-shrink: 0;
}

.sidebar-file .file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar-file.depth-2 {
  padding-left: 56px;
}

/* --- Main Content --- */
.main {
  flex: 1;
  overflow-y: auto;
  padding: 32px 48px;
  min-width: 0;
}

/* Welcome screen */
.welcome {
  max-width: 640px;
  margin: 60px auto;
  text-align: center;
}

.welcome h1 {
  font-size: 28px;
  font-weight: 700;
  margin-bottom: 12px;
  color: var(--text);
}

.welcome p {
  font-size: 16px;
  color: var(--text-secondary);
  margin-bottom: 32px;
  line-height: 1.7;
}

.welcome-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 32px;
}

.stat-card {
  padding: 20px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-light);
  border-radius: var(--radius);
  text-align: center;
}

.stat-card .stat-value {
  font-size: 32px;
  font-weight: 700;
  color: var(--accent);
}

.stat-card .stat-label {
  font-size: 13px;
  color: var(--text-secondary);
  margin-top: 4px;
}

.welcome-shortcuts {
  display: flex;
  justify-content: center;
  gap: 12px;
  flex-wrap: wrap;
}

.shortcut-btn {
  padding: 8px 16px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  transition: all var(--transition);
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.shortcut-btn:hover {
  background: var(--accent-light);
  border-color: var(--accent);
  color: var(--accent);
}

/* File viewer */
.file-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border-light);
}

.file-breadcrumb {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: var(--text-secondary);
}

.file-breadcrumb .sep {
  color: var(--text-tertiary);
}

.file-breadcrumb .current {
  color: var(--text);
  font-weight: 600;
}

.file-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 20px;
}

.meta-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  font-size: 12px;
  border-radius: 20px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-light);
  color: var(--text-secondary);
}

.meta-badge.type { background: var(--accent-light); color: var(--accent); border-color: transparent; }
.meta-badge.importance { background: #fff8c5; color: #9a6700; border-color: transparent; }
[data-theme="dark"] .meta-badge.importance { background: #3d2e00; color: #d29922; }

/* Markdown content */
.markdown-body {
  font-size: 15px;
  line-height: 1.75;
  color: var(--text);
  max-width: 820px;
}

.markdown-body h1 { font-size: 24px; font-weight: 700; margin: 24px 0 16px; padding-bottom: 8px; border-bottom: 1px solid var(--border-light); }
.markdown-body h2 { font-size: 20px; font-weight: 600; margin: 20px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--border-light); }
.markdown-body h3 { font-size: 17px; font-weight: 600; margin: 16px 0 8px; }
.markdown-body h4 { font-size: 15px; font-weight: 600; margin: 12px 0 8px; }

.markdown-body p { margin: 0 0 12px; }
.markdown-body ul, .markdown-body ol { margin: 0 0 12px; padding-left: 24px; }
.markdown-body li { margin: 4px 0; }

.markdown-body code {
  background: var(--bg-secondary);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 0.88em;
}

.markdown-body pre {
  background: var(--bg-secondary);
  border: 1px solid var(--border-light);
  border-radius: var(--radius);
  padding: 16px;
  overflow-x: auto;
  margin: 0 0 16px;
}

.markdown-body pre code {
  background: none;
  padding: 0;
  font-size: 13px;
  line-height: 1.5;
}

.markdown-body blockquote {
  margin: 0 0 12px;
  padding: 4px 16px;
  border-left: 4px solid var(--accent);
  color: var(--text-secondary);
  background: var(--bg-secondary);
  border-radius: 0 var(--radius) var(--radius) 0;
}

.markdown-body table {
  width: 100%;
  border-collapse: collapse;
  margin: 0 0 16px;
}

.markdown-body th, .markdown-body td {
  border: 1px solid var(--border);
  padding: 8px 12px;
  text-align: left;
}

.markdown-body th {
  background: var(--bg-secondary);
  font-weight: 600;
}

.markdown-body hr {
  border: none;
  border-top: 2px solid var(--border-light);
  margin: 24px 0;
}

.markdown-body a {
  color: var(--accent);
  text-decoration: none;
}

.markdown-body a:hover {
  text-decoration: underline;
}

.markdown-body img {
  max-width: 100%;
  border-radius: var(--radius);
}

/* Search results */
.search-results {
  max-width: 820px;
}

.search-results-header {
  font-size: 14px;
  color: var(--text-secondary);
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-light);
}

.search-result-item {
  margin-bottom: 20px;
  padding: 16px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-light);
  border-radius: var(--radius);
  cursor: pointer;
  transition: all var(--transition);
}

.search-result-item:hover {
  border-color: var(--accent);
  box-shadow: var(--shadow);
}

.search-result-path {
  font-size: 14px;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 8px;
}

.search-result-match {
  font-size: 13px;
  color: var(--text-secondary);
  background: var(--bg);
  padding: 8px 12px;
  border-radius: 4px;
  margin-top: 6px;
  font-family: var(--font-mono);
  white-space: pre-wrap;
  word-break: break-word;
  border-left: 3px solid var(--accent);
}

.search-result-match mark {
  background: #fff8c5;
  color: inherit;
  padding: 1px 2px;
  border-radius: 2px;
}

[data-theme="dark"] .search-result-match mark {
  background: #3d2e00;
}

/* Raw view toggle */
.view-toggle {
  display: flex;
  gap: 4px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 2px;
}

.view-toggle button {
  padding: 4px 12px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition);
}

.view-toggle button.active {
  background: var(--bg);
  color: var(--text);
  box-shadow: var(--shadow);
}

/* Raw content */
.raw-content {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.6;
  background: var(--bg-secondary);
  padding: 16px;
  border-radius: var(--radius);
  border: 1px solid var(--border-light);
}

/* Loading */
.loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 60px;
  color: var(--text-tertiary);
}

.spinner {
  width: 20px; height: 20px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
  margin-right: 10px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Empty state */
.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--text-secondary);
}

.empty-state .icon {
  font-size: 48px;
  margin-bottom: 16px;
  opacity: 0.5;
}

/* Scrollbar */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-tertiary); }

/* Responsive */
@media (max-width: 768px) {
  .sidebar { display: none; }
  .main { padding: 16px; }
  .welcome-stats { grid-template-columns: 1fr; }
}
</style>
</head>
<body>

<!-- Header -->
<header class="header">
  <div class="header-logo">
    <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
    <span>OpenClaw Memory</span>
    <span id="projectName" style="color: var(--text-secondary); font-weight: 400; font-size: 13px;"></span>
  </div>
  <div class="search-container">
    <svg class="search-icon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M11.5 7a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Zm-.82 4.74a6 6 0 1 1 1.06-1.06l3.04 3.04a.75.75 0 1 1-1.06 1.06l-3.04-3.04Z"/></svg>
    <input class="search-input" type="text" id="searchInput" placeholder="Search memories..." autocomplete="off">
  </div>
  <div class="header-actions">
    <button class="btn-icon" id="themeToggle" title="Toggle theme">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" id="themeIcon"><path d="M8 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0 1.5a5.5 5.5 0 1 1 0-11 5.5 5.5 0 0 1 0 11ZM8 0a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0V.75A.75.75 0 0 1 8 0Zm0 12a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0v-1.5A.75.75 0 0 1 8 12ZM2 8a.75.75 0 0 1-.75.75H.75a.75.75 0 0 1 0-1.5h.5A.75.75 0 0 1 2 8Zm13.25-.75a.75.75 0 0 1 0 1.5h-.5a.75.75 0 0 1 0-1.5h.5Z"/></svg>
    </button>
    <button class="btn-icon" id="refreshBtn" title="Refresh">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 3a5 5 0 1 0 4.546 2.914.75.75 0 0 1 1.362-.628A6.5 6.5 0 1 1 8 1.5v2A.75.75 0 0 1 8 3Z"/><path d="M8 0a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-.75.75H4.5a.75.75 0 0 1 0-1.5h2.69L8 .75A.75.75 0 0 1 8 0Z"/></svg>
    </button>
  </div>
</header>

<!-- Layout -->
<div class="layout">
  <!-- Sidebar -->
  <nav class="sidebar" id="sidebar">
    <div class="loading" id="sidebarLoading">
      <div class="spinner"></div> Loading...
    </div>
  </nav>

  <!-- Main Content -->
  <main class="main" id="mainContent">
    <div class="loading" id="mainLoading" style="display:none;">
      <div class="spinner"></div> Loading...
    </div>
    <div id="contentArea"></div>
  </main>
</div>

<script>
// ---- State ----
let currentFile = null;
let currentView = 'rendered'; // 'rendered' | 'raw'
let treeData = null;
let configData = null;
let searchTimeout = null;

// ---- Init ----
document.addEventListener('DOMContentLoaded', async () => {
  // Theme
  const saved = localStorage.getItem('openclaw-theme');
  if (saved === 'dark' || (!saved && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
    setTheme('dark');
  }

  // Load data
  const [tree, config] = await Promise.all([
    fetchJSON('/api/tree'),
    fetchJSON('/api/config'),
  ]);
  treeData = tree;
  configData = config;

  document.getElementById('projectName').textContent = config.project_name !== '(unnamed)' ? '/ ' + config.project_name : '';

  renderSidebar(tree);
  showWelcome();

  // Event listeners
  document.getElementById('searchInput').addEventListener('input', onSearchInput);
  document.getElementById('themeToggle').addEventListener('click', toggleTheme);
  document.getElementById('refreshBtn').addEventListener('click', refresh);
  document.getElementById('searchInput').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      e.target.value = '';
      if (currentFile) loadFile(currentFile);
      else showWelcome();
    }
  });

  // Keyboard shortcut: / to focus search
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && document.activeElement !== document.getElementById('searchInput')) {
      e.preventDefault();
      document.getElementById('searchInput').focus();
    }
  });
});

// ---- API ----
async function fetchJSON(url) {
  const res = await fetch(url);
  return res.json();
}

// ---- Theme ----
function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('openclaw-theme', theme);
  const lightCSS = document.getElementById('hljs-light');
  const darkCSS = document.getElementById('hljs-dark');
  if (theme === 'dark') {
    lightCSS.disabled = true;
    darkCSS.disabled = false;
  } else {
    lightCSS.disabled = false;
    darkCSS.disabled = true;
  }
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  setTheme(current === 'dark' ? 'light' : 'dark');
}

// ---- Refresh ----
async function refresh() {
  treeData = await fetchJSON('/api/tree');
  renderSidebar(treeData);
  if (currentFile) loadFile(currentFile);
}

// ---- Sidebar ----
function renderSidebar(tree) {
  const sidebar = document.getElementById('sidebar');
  document.getElementById('sidebarLoading').style.display = 'none';

  let html = '';

  // Global section
  if (tree.global && (tree.global.files?.length || Object.keys(tree.global.dirs || {}).length)) {
    html += renderSection('Global Memory', 'global', tree.global, '🌐');
  }

  // Project section
  if (tree.project && (tree.project.files?.length || Object.keys(tree.project.dirs || {}).length)) {
    html += renderSection('Project Memory', 'project', tree.project, '📂');
  }

  if (!html) {
    html = '<div class="empty-state"><div class="icon">📭</div><p>No memory files found</p></div>';
  }

  sidebar.innerHTML = html;

  // Add click handlers
  sidebar.querySelectorAll('.sidebar-heading').forEach(el => {
    el.addEventListener('click', () => {
      el.classList.toggle('collapsed');
      const group = el.nextElementSibling;
      if (group) group.classList.toggle('collapsed');
    });
  });

  sidebar.querySelectorAll('.sidebar-dir-label').forEach(el => {
    el.addEventListener('click', () => {
      const group = el.nextElementSibling;
      if (group) {
        group.style.display = group.style.display === 'none' ? '' : 'none';
        const arrow = el.querySelector('.arrow');
        if (arrow) arrow.style.transform = group.style.display === 'none' ? 'rotate(-90deg)' : '';
      }
    });
  });

  sidebar.querySelectorAll('.sidebar-file').forEach(el => {
    el.addEventListener('click', () => {
      const path = el.dataset.path;
      loadFile(path);
      document.getElementById('searchInput').value = '';
    });
  });

  // Highlight active
  if (currentFile) highlightActive(currentFile);
}

function renderSection(title, scope, data, icon) {
  let html = `
    <div class="sidebar-section">
      <div class="sidebar-heading">
        <span class="arrow">▼</span>
        ${icon} ${title}
      </div>
      <div class="sidebar-group">`;

  // Top-level files
  if (data.files) {
    for (const f of data.files) {
      html += renderFileItem(f, 1);
    }
  }

  // Directories
  if (data.dirs) {
    for (const [dirName, dirData] of Object.entries(data.dirs)) {
      html += renderDirItem(dirName, dirData, 1);
    }
  }

  html += '</div></div>';
  return html;
}

function renderDirItem(name, data, depth) {
  const icon = getDirIcon(name);
  let html = `
    <div class="sidebar-dir">
      <div class="sidebar-dir-label" style="padding-left: ${16 + depth * 16}px">
        <span class="arrow" style="font-size: 10px;">▼</span>
        <span class="dir-icon">${icon}</span>
        <span>${name}</span>
      </div>
      <div class="sidebar-dir-contents">`;

  if (data.files) {
    for (const f of data.files) {
      html += renderFileItem(f, depth + 1);
    }
  }

  if (data.dirs) {
    for (const [subName, subData] of Object.entries(data.dirs)) {
      html += renderDirItem(subName, subData, depth + 1);
    }
  }

  html += '</div></div>';
  return html;
}

function renderFileItem(file, depth) {
  const icon = getFileIcon(file.name);
  const pl = 16 + depth * 16;
  return `<div class="sidebar-file" data-path="${file.path}" style="padding-left: ${pl}px">
    <span class="file-icon">${icon}</span>
    <span class="file-name">${file.name}</span>
  </div>`;
}

function getDirIcon(name) {
  const icons = { user: '👤', journal: '📓', agent: '🤖' };
  return icons[name] || '📁';
}

function getFileIcon(name) {
  if (name === 'TASKS.md') return '📋';
  if (name === 'PRIMER.md') return '📌';
  if (name.includes('preferences')) return '⚙️';
  if (name.includes('instructions')) return '📜';
  if (name.includes('entities')) return '👥';
  if (name.includes('decisions')) return '🎯';
  if (name.includes('patterns')) return '🔄';
  if (/^\d{4}-\d{2}-\d{2}\.md$/.test(name)) return '📅';
  return '📄';
}

function highlightActive(path) {
  document.querySelectorAll('.sidebar-file').forEach(el => {
    el.classList.toggle('active', el.dataset.path === path);
  });
}

// ---- File Loading ----
async function loadFile(path) {
  currentFile = path;
  currentView = 'rendered';
  highlightActive(path);

  const contentArea = document.getElementById('contentArea');
  contentArea.innerHTML = '<div class="loading"><div class="spinner"></div> Loading...</div>';

  const data = await fetchJSON('/api/file?path=' + encodeURIComponent(path));

  if (data.error) {
    contentArea.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${escapeHtml(data.error)}</p></div>`;
    return;
  }

  renderFile(data);
}

function renderFile(data) {
  const contentArea = document.getElementById('contentArea');

  // Breadcrumb
  const parts = data.path.split('/');
  let breadcrumbHtml = parts.map((p, i) =>
    i === parts.length - 1
      ? `<span class="current">${escapeHtml(p)}</span>`
      : `<span>${escapeHtml(p)}</span>`
  ).join('<span class="sep">/</span>');

  // Meta badges
  let metaHtml = '';
  if (data.metadata && Object.keys(data.metadata).length > 0) {
    const m = data.metadata;
    if (m.type) metaHtml += `<span class="meta-badge type">${m.type}</span>`;
    if (m.importance) metaHtml += `<span class="meta-badge importance">importance: ${m.importance}</span>`;
    if (m.reinforcement) metaHtml += `<span class="meta-badge">reinforcement: ${m.reinforcement}</span>`;
    if (m.created) metaHtml += `<span class="meta-badge">created: ${m.created}</span>`;
    if (m.updated && m.updated !== m.created) metaHtml += `<span class="meta-badge">updated: ${m.updated}</span>`;
    if (m.status) metaHtml += `<span class="meta-badge">${m.status}</span>`;
    if (m.sessions) metaHtml += `<span class="meta-badge">sessions: ${m.sessions}</span>`;
  }

  let html = `
    <div class="file-header">
      <div class="file-breadcrumb">${breadcrumbHtml}</div>
      <div class="view-toggle">
        <button class="${currentView === 'rendered' ? 'active' : ''}" onclick="switchView('rendered')">Rendered</button>
        <button class="${currentView === 'raw' ? 'active' : ''}" onclick="switchView('raw')">Raw</button>
      </div>
    </div>`;

  if (metaHtml) {
    html += `<div class="file-meta">${metaHtml}</div>`;
  }

  if (currentView === 'rendered') {
    const rendered = renderMarkdown(data.content);
    html += `<div class="markdown-body" id="markdownContent">${rendered}</div>`;
  } else {
    html += `<div class="raw-content">${escapeHtml(data.raw)}</div>`;
  }

  contentArea.innerHTML = html;

  // Highlight code blocks
  contentArea.querySelectorAll('pre code').forEach(block => {
    if (window.hljs) hljs.highlightElement(block);
  });
}

window._currentFileData = null;
const _origLoadFile = loadFile;
loadFile = async function(path) {
  currentFile = path;
  currentView = 'rendered';
  highlightActive(path);

  const contentArea = document.getElementById('contentArea');
  contentArea.innerHTML = '<div class="loading"><div class="spinner"></div> Loading...</div>';

  const data = await fetchJSON('/api/file?path=' + encodeURIComponent(path));
  window._currentFileData = data;

  if (data.error) {
    contentArea.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${escapeHtml(data.error)}</p></div>`;
    return;
  }

  renderFile(data);
};

function switchView(view) {
  currentView = view;
  if (window._currentFileData) renderFile(window._currentFileData);
}

// ---- Markdown Rendering ----
function renderMarkdown(text) {
  if (window.marked) {
    marked.setOptions({
      highlight: function(code, lang) {
        if (window.hljs && lang && hljs.getLanguage(lang)) {
          return hljs.highlight(code, { language: lang }).value;
        }
        return code;
      },
      breaks: true,
      gfm: true,
    });
    return marked.parse(text || '');
  }
  // Fallback: basic conversion
  return '<pre>' + escapeHtml(text || '') + '</pre>';
}

// ---- Search ----
function onSearchInput(e) {
  clearTimeout(searchTimeout);
  const query = e.target.value.trim();

  if (!query) {
    if (currentFile) loadFile(currentFile);
    else showWelcome();
    return;
  }

  searchTimeout = setTimeout(() => performSearch(query), 300);
}

async function performSearch(query) {
  const contentArea = document.getElementById('contentArea');
  contentArea.innerHTML = '<div class="loading"><div class="spinner"></div> Searching...</div>';

  const results = await fetchJSON('/api/search?q=' + encodeURIComponent(query));

  if (!results || results.length === 0) {
    contentArea.innerHTML = `
      <div class="search-results">
        <div class="search-results-header">No results for "<strong>${escapeHtml(query)}</strong>"</div>
        <div class="empty-state"><div class="icon">🔍</div><p>Try a different search term</p></div>
      </div>`;
    return;
  }

  const totalMatches = results.reduce((a, r) => a + r.match_count, 0);
  let html = `
    <div class="search-results">
      <div class="search-results-header">
        Found <strong>${totalMatches}</strong> matches in <strong>${results.length}</strong> files for "<strong>${escapeHtml(query)}</strong>"
      </div>`;

  for (const r of results) {
    html += `
      <div class="search-result-item" onclick="loadFile('${r.path}'); document.getElementById('searchInput').value='';">
        <div class="search-result-path">${getFileIcon(r.filename)} ${escapeHtml(r.path)}</div>`;

    for (const match of r.matches) {
      const highlighted = highlightSearchTerm(escapeHtml(match), query);
      html += `<div class="search-result-match">${highlighted}</div>`;
    }

    html += '</div>';
  }

  html += '</div>';
  contentArea.innerHTML = html;
}

function highlightSearchTerm(text, query) {
  const regex = new RegExp('(' + escapeRegExp(query) + ')', 'gi');
  return text.replace(regex, '<mark>$1</mark>');
}

function escapeRegExp(string) {
  return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// ---- Welcome Screen ----
function showWelcome() {
  const contentArea = document.getElementById('contentArea');

  // Count files
  let globalCount = 0, projectCount = 0, journalCount = 0;
  if (treeData) {
    globalCount = countFiles(treeData.global);
    projectCount = countFiles(treeData.project);
    if (treeData.project?.dirs?.journal) {
      journalCount = countFiles(treeData.project.dirs.journal);
    }
  }

  contentArea.innerHTML = `
    <div class="welcome">
      <h1>OpenClaw Memory</h1>
      <p>Browse and search your AI agent's persistent memory.<br>Use the sidebar to navigate or the search bar to find specific memories.</p>

      <div class="welcome-stats">
        <div class="stat-card">
          <div class="stat-value">${globalCount}</div>
          <div class="stat-label">Global Memories</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">${projectCount}</div>
          <div class="stat-label">Project Memories</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">${journalCount}</div>
          <div class="stat-label">Journal Entries</div>
        </div>
      </div>

      <div class="welcome-shortcuts">
        ${configData?.project_root ? `
        <a class="shortcut-btn" onclick="openQuickFile('project/TASKS.md')">📋 Tasks</a>
        <a class="shortcut-btn" onclick="openQuickFile('project/PRIMER.md')">📌 Primer</a>
        ` : ''}
        <a class="shortcut-btn" onclick="openQuickFile('global/user/preferences.md')">⚙️ Preferences</a>
        <a class="shortcut-btn" onclick="openQuickFile('global/user/instructions.md')">📜 Instructions</a>
        <a class="shortcut-btn" onclick="openQuickFile('global/user/entities.md')">👥 Entities</a>
      </div>

      <p style="margin-top: 24px; font-size: 13px; color: var(--text-tertiary);">
        Press <kbd style="padding: 2px 6px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 4px; font-size: 12px;">/</kbd> to search
      </p>
    </div>`;
}

function openQuickFile(path) {
  loadFile(path);
}

function countFiles(node) {
  if (!node) return 0;
  let count = (node.files || []).length;
  if (node.dirs) {
    for (const sub of Object.values(node.dirs)) {
      count += countFiles(sub);
    }
  }
  return count;
}

// ---- Utils ----
function escapeHtml(text) {
  const el = document.createElement('div');
  el.textContent = text;
  return el.innerHTML;
}
</script>

</body>
</html>
"""
