"""Web viewer for chat history — browse and search journal files in your browser."""

from __future__ import annotations

import json
import logging
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .storage import detect_journal_dir, grep_search, _parse_turns

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------


class WebHandler(BaseHTTPRequestHandler):
    journal_dir: Path

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self._send_html(_HTML_PAGE)
        elif path == "/api/files":
            self._send_json(self._list_files())
        elif path == "/api/file":
            name = params.get("name", [""])[0]
            self._send_json(self._read_file(name))
        elif path == "/api/search":
            q = params.get("q", [""])[0]
            since = params.get("since", [""])[0]
            self._send_json(self._search(q, since))
        else:
            self.send_error(404)

    def _list_files(self) -> list[dict]:
        if not self.journal_dir.is_dir():
            return []
        files = sorted(self.journal_dir.glob("*.md"), reverse=True)
        return [
            {"name": f.name, "date": f.stem, "size": f.stat().st_size}
            for f in files
        ]

    def _read_file(self, name: str) -> dict:
        if not name or ".." in name or "/" in name:
            return {"error": "Invalid filename"}
        path = self.journal_dir / name
        if not path.is_file():
            return {"error": f"File not found: {name}"}
        content = path.read_text(encoding="utf-8")
        turns = _parse_turns(content, path.stem, name)
        return {"name": name, "date": path.stem, "content": content, "turn_count": len(turns)}

    def _search(self, query: str, since: str) -> list[dict]:
        if not query:
            return []
        return grep_search(self.journal_dir, query, since=since)

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
        logger.debug(format, *args)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_web(host: str = "127.0.0.1", port: int = 8767, open_browser: bool = True) -> None:
    journal_dir = detect_journal_dir()

    handler_class = type("Handler", (WebHandler,), {"journal_dir": journal_dir})
    server = HTTPServer((host, port), handler_class)
    url = f"http://{host}:{port}"

    print(f"\n  OpenClaw Memory — Chat History Viewer")
    print(f"  {'─' * 40}")
    print(f"  URL     : {url}")
    print(f"  Journal : {journal_dir}")
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
# HTML SPA
# ---------------------------------------------------------------------------

_HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenClaw Memory — Chat History</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
:root {
  --bg: #ffffff; --bg2: #f6f8fa; --bg3: #eef1f5;
  --text: #1f2328; --text2: #656d76; --text3: #8b949e;
  --border: #d1d9e0; --accent: #0969da; --accent-bg: #ddf4ff;
  --radius: 8px;
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  --mono: "SF Mono", "Fira Code", Consolas, monospace;
}
[data-theme="dark"] {
  --bg: #0d1117; --bg2: #161b22; --bg3: #21262d;
  --text: #e6edf3; --text2: #8b949e; --text3: #6e7681;
  --border: #30363d; --accent: #58a6ff; --accent-bg: #1c3a5c;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: var(--font); background: var(--bg); color: var(--text); height: 100vh; overflow: hidden; }

.header {
  position: fixed; top: 0; left: 0; right: 0; height: 52px;
  background: var(--bg2); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; padding: 0 16px; gap: 12px; z-index: 100;
}
.header-title { font-weight: 600; font-size: 14px; white-space: nowrap; }
.search-box {
  flex: 1; max-width: 480px; height: 34px; padding: 0 12px;
  border: 1px solid var(--border); border-radius: 6px;
  background: var(--bg); color: var(--text); font-size: 13px; outline: none;
}
.search-box:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-bg); }
.btn-icon {
  width: 34px; height: 34px; border: 1px solid var(--border); border-radius: 6px;
  background: var(--bg); color: var(--text2); cursor: pointer;
  display: flex; align-items: center; justify-content: center; font-size: 16px;
}
.btn-icon:hover { background: var(--bg3); }

.layout { display: flex; margin-top: 52px; height: calc(100vh - 52px); }

.sidebar {
  width: 240px; min-width: 240px; background: var(--bg2);
  border-right: 1px solid var(--border); overflow-y: auto; padding: 8px 0;
}
.file-item {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 16px; font-size: 13px; cursor: pointer; color: var(--text);
}
.file-item:hover { background: var(--bg3); }
.file-item.active { background: var(--accent-bg); color: var(--accent); font-weight: 500; }
.file-date { color: var(--text2); font-size: 12px; }

.main { flex: 1; overflow-y: auto; padding: 24px 40px; min-width: 0; }

.welcome { max-width: 560px; margin: 80px auto; text-align: center; }
.welcome h1 { font-size: 24px; margin-bottom: 8px; }
.welcome p { color: var(--text2); font-size: 15px; line-height: 1.7; }
.stat-row { display: flex; justify-content: center; gap: 24px; margin: 24px 0; }
.stat { text-align: center; }
.stat-val { font-size: 28px; font-weight: 700; color: var(--accent); }
.stat-lbl { font-size: 12px; color: var(--text2); }

.turn { margin-bottom: 24px; padding: 16px; background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); }
.turn-header { font-size: 12px; color: var(--text3); margin-bottom: 12px; font-family: var(--mono); }
.turn h3 { font-size: 13px; font-weight: 600; color: var(--accent); margin: 12px 0 6px; text-transform: uppercase; letter-spacing: 0.5px; }
.turn-content { font-size: 14px; line-height: 1.7; }
.turn-content pre { background: var(--bg3); padding: 12px; border-radius: 6px; overflow-x: auto; margin: 8px 0; }
.turn-content code { font-family: var(--mono); font-size: 0.88em; }
.turn-content p { margin: 6px 0; }
.turn-content ul, .turn-content ol { margin: 6px 0; padding-left: 20px; }

.search-result { cursor: pointer; }
.search-result:hover { border-color: var(--accent); }
.search-result mark { background: #fff8c5; padding: 1px 2px; border-radius: 2px; }
[data-theme="dark"] .search-result mark { background: #3d2e00; }

.empty { text-align: center; padding: 60px; color: var(--text2); }

@media (max-width: 768px) { .sidebar { display: none; } .main { padding: 16px; } }
</style>
</head>
<body>

<header class="header">
  <div class="header-title">OpenClaw Memory</div>
  <input class="search-box" id="search" type="text" placeholder="Search chat history..." autocomplete="off">
  <button class="btn-icon" id="themeBtn" title="Toggle theme">🌓</button>
</header>

<div class="layout">
  <nav class="sidebar" id="sidebar"></nav>
  <main class="main" id="main"></main>
</div>

<script>
let files = [], currentFile = null, searchTimer = null;

document.addEventListener('DOMContentLoaded', async () => {
  if (localStorage.getItem('theme') === 'dark' || (!localStorage.getItem('theme') && matchMedia('(prefers-color-scheme:dark)').matches))
    document.documentElement.setAttribute('data-theme','dark');

  files = await (await fetch('/api/files')).json();
  renderSidebar();
  showWelcome();

  document.getElementById('search').addEventListener('input', e => {
    clearTimeout(searchTimer);
    const q = e.target.value.trim();
    if (!q) { currentFile ? loadFile(currentFile) : showWelcome(); return; }
    searchTimer = setTimeout(() => doSearch(q), 300);
  });

  document.getElementById('themeBtn').addEventListener('click', () => {
    const d = document.documentElement.getAttribute('data-theme') === 'dark' ? '' : 'dark';
    document.documentElement.setAttribute('data-theme', d || '');
    localStorage.setItem('theme', d || 'light');
  });

  document.addEventListener('keydown', e => {
    if (e.key === '/' && document.activeElement !== document.getElementById('search')) {
      e.preventDefault(); document.getElementById('search').focus();
    }
  });
});

function renderSidebar() {
  const sb = document.getElementById('sidebar');
  if (!files.length) { sb.innerHTML = '<div class="empty">No journal files</div>'; return; }
  sb.innerHTML = files.map(f =>
    `<div class="file-item${currentFile===f.name?' active':''}" data-name="${f.name}">
      <span>📅</span><span class="file-date">${f.date}</span>
    </div>`
  ).join('');
  sb.querySelectorAll('.file-item').forEach(el =>
    el.addEventListener('click', () => { loadFile(el.dataset.name); document.getElementById('search').value=''; })
  );
}

async function loadFile(name) {
  currentFile = name;
  renderSidebar();
  const data = await (await fetch('/api/file?name='+encodeURIComponent(name))).json();
  if (data.error) { document.getElementById('main').innerHTML = `<div class="empty">${esc(data.error)}</div>`; return; }
  const turns = parseTurns(data.content);
  document.getElementById('main').innerHTML = `<h2 style="margin-bottom:16px;font-size:16px;">📅 ${esc(data.date)} <span style="color:var(--text3);font-weight:400;">(${turns.length} conversations)</span></h2>` +
    turns.map(t => renderTurn(t)).join('');
}

function parseTurns(content) {
  const turns = [], lines = content.split('\n');
  let cur = null;
  for (const line of lines) {
    const m = line.match(/^## (\d{2}:\d{2}) \| (.+)$/);
    if (m) { if (cur) turns.push(cur); cur = {time:m[1],model:m[2],lines:[]}; continue; }
    if (/^---$/.test(line.trim()) && cur) { turns.push(cur); cur = null; continue; }
    if (cur) cur.lines.push(line);
  }
  if (cur) turns.push(cur);
  return turns;
}

function renderTurn(t) {
  const body = t.lines.join('\n');
  const html = window.marked ? marked.parse(body) : '<pre>'+esc(body)+'</pre>';
  return `<div class="turn"><div class="turn-header">${esc(t.time)} | ${esc(t.model)}</div><div class="turn-content">${html}</div></div>`;
}

async function doSearch(q) {
  const main = document.getElementById('main');
  main.innerHTML = '<div class="empty">Searching...</div>';
  const results = await (await fetch('/api/search?q='+encodeURIComponent(q))).json();
  if (!results.length) { main.innerHTML = `<div class="empty">No results for "${esc(q)}"</div>`; return; }
  main.innerHTML = `<p style="color:var(--text2);margin-bottom:16px;">Found <b>${results.length}</b> conversation(s) matching "<b>${esc(q)}</b>"</p>` +
    results.map(r => {
      const highlighted = r.content.replace(new RegExp('('+escRe(q)+')','gi'),'<mark>$1</mark>');
      return `<div class="turn search-result" onclick="loadFileAndScroll('${r.file}')">
        <div class="turn-header">${r.date} ${r.time} | ${r.model}${r.truncated?' (truncated)':''}</div>
        <div class="turn-content"><pre style="white-space:pre-wrap;word-break:break-word;">${highlighted}</pre></div>
      </div>`;
    }).join('');
}

function loadFileAndScroll(name) { document.getElementById('search').value=''; loadFile(name); }

function showWelcome() {
  const total = files.length;
  document.getElementById('main').innerHTML = `<div class="welcome">
    <h1>Chat History</h1>
    <p>Browse and search your AI conversation history.</p>
    <div class="stat-row"><div class="stat"><div class="stat-val">${total}</div><div class="stat-lbl">Journal Files</div></div></div>
    <p style="font-size:13px;color:var(--text3);margin-top:16px;">Press <kbd style="padding:2px 6px;background:var(--bg2);border:1px solid var(--border);border-radius:4px;font-size:12px;">/</kbd> to search</p>
  </div>`;
}

function esc(s) { const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'); }
</script>
</body>
</html>
"""
