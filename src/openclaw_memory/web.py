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

from .storage import grep_search, _parse_turns

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------


class WebHandler(BaseHTTPRequestHandler):
    projects: dict[str, Path]  # {project_name: journal_dir}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self._send_html(_HTML_PAGE)
        elif path == "/api/projects":
            self._send_json(self._list_projects())
        elif path == "/api/files":
            project = params.get("project", [""])[0]
            self._send_json(self._list_files(project))
        elif path == "/api/file":
            project = params.get("project", [""])[0]
            name = params.get("name", [""])[0]
            self._send_json(self._read_file(project, name))
        elif path == "/api/search":
            q = params.get("q", [""])[0]
            since = params.get("since", [""])[0]
            self._send_json(self._search(q, since))
        else:
            self.send_error(404)

    def _list_projects(self) -> list[dict]:
        result = []
        for name, journal_dir in sorted(self.projects.items()):
            file_count = 0
            if journal_dir.is_dir():
                file_count = len(list(journal_dir.glob("*.md")))
            result.append({"name": name, "file_count": file_count})
        return result

    def _list_files(self, project: str) -> list[dict]:
        journal_dir = self.projects.get(project)
        if not journal_dir or not journal_dir.is_dir():
            return []
        files = sorted(journal_dir.glob("*.md"), reverse=True)
        return [
            {"project": project, "name": f.name, "date": f.stem, "size": f.stat().st_size}
            for f in files
        ]

    def _read_file(self, project: str, name: str) -> dict:
        if not name or ".." in name or "/" in name:
            return {"error": "Invalid filename"}
        journal_dir = self.projects.get(project)
        if not journal_dir:
            return {"error": f"Unknown project: {project}"}
        path = journal_dir / name
        if not path.is_file():
            return {"error": f"File not found: {name}"}
        content = path.read_text(encoding="utf-8")
        turns = _parse_turns(content, path.stem, name)
        return {
            "project": project, "name": name, "date": path.stem,
            "content": content, "turn_count": len(turns),
        }

    def _search(self, query: str, since: str) -> list[dict]:
        if not query:
            return []
        all_results: list[dict] = []
        for project_name, journal_dir in sorted(self.projects.items()):
            results = grep_search(journal_dir, query, since=since)
            for r in results:
                r["project"] = project_name
            all_results.extend(results)
        return all_results

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


def run_web(
    projects: dict[str, Path],
    host: str = "127.0.0.1",
    port: int = 8767,
    open_browser: bool = True,
) -> None:
    handler_class = type("Handler", (WebHandler,), {"projects": projects})
    server = HTTPServer((host, port), handler_class)
    url = f"http://{host}:{port}"

    print(f"\n  OpenClaw Memory — Chat History Viewer")
    print(f"  {'─' * 40}")
    print(f"  URL      : {url}")
    print(f"  Projects : {len(projects)}")
    for name, path in sorted(projects.items()):
        print(f"    {name} → {path}")
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
  width: 260px; min-width: 260px; background: var(--bg2);
  border-right: 1px solid var(--border); overflow-y: auto; padding: 8px 0;
}
.project-group { margin-bottom: 4px; }
.project-header {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 16px 4px; font-size: 11px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.5px; color: var(--text2); cursor: pointer; user-select: none;
}
.project-header:hover { color: var(--text); }
.project-header .arrow { font-size: 10px; transition: transform 0.15s; }
.project-header.collapsed .arrow { transform: rotate(-90deg); }
.project-files { overflow: hidden; }
.project-files.collapsed { display: none; }
.file-item {
  display: flex; align-items: center; gap: 8px;
  padding: 5px 16px 5px 28px; font-size: 13px; cursor: pointer; color: var(--text);
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
.turn-header { font-size: 12px; color: var(--text3); margin-bottom: 4px; font-family: var(--mono); }
.turn-title { font-size: 13px; color: var(--text2); margin-bottom: 10px; font-weight: 500; }
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
.project-tag {
  display: inline-block; font-size: 11px; padding: 1px 6px; border-radius: 4px;
  background: var(--accent-bg); color: var(--accent); margin-left: 8px; font-weight: 500;
}

.empty { text-align: center; padding: 60px; color: var(--text2); }

@media (max-width: 768px) { .sidebar { display: none; } .main { padding: 16px; } }
</style>
</head>
<body>

<header class="header">
  <div class="header-title">OpenClaw Memory</div>
  <input class="search-box" id="search" type="text" placeholder="Search chat history..." autocomplete="off">
  <button class="btn-icon" id="themeBtn" title="Toggle theme">&#x1f313;</button>
</header>

<div class="layout">
  <nav class="sidebar" id="sidebar"></nav>
  <main class="main" id="main"></main>
</div>

<script>
let projectsData = [];   // [{name, file_count}]
let filesCache = {};      // {projectName: [{project, name, date, size}]}
let currentProject = null;
let currentFile = null;
let searchTimer = null;
let multiProject = false;

document.addEventListener('DOMContentLoaded', async () => {
  if (localStorage.getItem('theme') === 'dark' || (!localStorage.getItem('theme') && matchMedia('(prefers-color-scheme:dark)').matches))
    document.documentElement.setAttribute('data-theme','dark');

  projectsData = await (await fetch('/api/projects')).json();
  multiProject = projectsData.length > 1;

  // Pre-fetch file lists for all projects
  await Promise.all(projectsData.map(async p => {
    filesCache[p.name] = await (await fetch('/api/files?project='+encodeURIComponent(p.name))).json();
  }));

  renderSidebar();
  showWelcome();

  document.getElementById('search').addEventListener('input', e => {
    clearTimeout(searchTimer);
    const q = e.target.value.trim();
    if (!q) { currentFile ? loadFile(currentProject, currentFile) : showWelcome(); return; }
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
  const totalFiles = Object.values(filesCache).reduce((s,arr) => s + arr.length, 0);
  if (!totalFiles) { sb.innerHTML = '<div class="empty">No journal files</div>'; return; }

  let html = '';
  for (const p of projectsData) {
    const files = filesCache[p.name] || [];
    if (!files.length) continue;

    const collapsed = localStorage.getItem('collapse_'+p.name) === '1';

    if (multiProject) {
      html += `<div class="project-group">
        <div class="project-header${collapsed?' collapsed':''}" data-project="${esc(p.name)}">
          <span class="arrow">&#9660;</span>
          <span>${esc(p.name)}</span>
          <span style="color:var(--text3);font-weight:400;font-size:10px;">(${files.length})</span>
        </div>
        <div class="project-files${collapsed?' collapsed':''}">`;
    }

    for (const f of files) {
      const isActive = currentProject === p.name && currentFile === f.name;
      html += `<div class="file-item${isActive?' active':''}" data-project="${esc(p.name)}" data-name="${esc(f.name)}">
        <span style="font-size:12px;">&#x1f4c5;</span><span class="file-date">${esc(f.date)}</span>
      </div>`;
    }

    if (multiProject) {
      html += `</div></div>`;
    }
  }

  sb.innerHTML = html;

  sb.querySelectorAll('.file-item').forEach(el =>
    el.addEventListener('click', () => {
      loadFile(el.dataset.project, el.dataset.name);
      document.getElementById('search').value = '';
    })
  );

  if (multiProject) {
    sb.querySelectorAll('.project-header').forEach(el =>
      el.addEventListener('click', () => {
        el.classList.toggle('collapsed');
        const filesDiv = el.nextElementSibling;
        filesDiv.classList.toggle('collapsed');
        localStorage.setItem('collapse_'+el.dataset.project, filesDiv.classList.contains('collapsed') ? '1' : '0');
      })
    );
  }
}

async function loadFile(project, name) {
  currentProject = project;
  currentFile = name;
  renderSidebar();
  const url = '/api/file?project='+encodeURIComponent(project)+'&name='+encodeURIComponent(name);
  const data = await (await fetch(url)).json();
  if (data.error) { document.getElementById('main').innerHTML = `<div class="empty">${esc(data.error)}</div>`; return; }
  const turns = parseTurns(data.content);
  let heading = `&#x1f4c5; ${esc(data.date)} <span style="color:var(--text3);font-weight:400;">(${turns.length} conversations)</span>`;
  if (multiProject) heading = `<span class="project-tag">${esc(project)}</span> ` + heading;
  document.getElementById('main').innerHTML = `<h2 style="margin-bottom:16px;font-size:16px;">${heading}</h2>` +
    turns.map(t => renderTurn(t)).join('');
}

function parseTurns(content) {
  const turns = [], lines = content.split('\n');
  let cur = null;
  for (const line of lines) {
    const m = line.match(/^## (\d{2}:\d{2}) \| ([^|]+)(?: \| (.+))?$/);
    if (m) {
      if (cur) turns.push(cur);
      cur = { time: m[1], model: m[2].trim(), title: (m[3] || '').trim(), lines: [] };
      continue;
    }
    if (/^---$/.test(line.trim()) && cur) { turns.push(cur); cur = null; continue; }
    if (cur) cur.lines.push(line);
  }
  if (cur) turns.push(cur);
  return turns;
}

function renderTurn(t) {
  const body = t.lines.join('\n');
  const html = window.marked ? marked.parse(body) : '<pre>'+esc(body)+'</pre>';
  const titleHtml = (t.title) ? `<div class="turn-title">${esc(t.title)}</div>` : '';
  return `<div class="turn"><div class="turn-header">${esc(t.time)} | ${esc(t.model)}</div>${titleHtml}<div class="turn-content">${html}</div></div>`;
}

async function doSearch(q) {
  const main = document.getElementById('main');
  main.innerHTML = '<div class="empty">Searching...</div>';
  const results = await (await fetch('/api/search?q='+encodeURIComponent(q))).json();
  if (!results.length) { main.innerHTML = `<div class="empty">No results for "${esc(q)}"</div>`; return; }
  main.innerHTML = `<p style="color:var(--text2);margin-bottom:16px;">Found <b>${results.length}</b> conversation(s) matching "<b>${esc(q)}</b>"</p>` +
    results.map(r => {
      const highlighted = r.content.replace(new RegExp('('+escRe(q)+')','gi'),'<mark>$1</mark>');
      const projectLabel = multiProject ? `<span class="project-tag">${esc(r.project||'')}</span>` : '';
      const titlePart = (r.title) ? `<div class="turn-title">${esc(r.title)}</div>` : '';
      return `<div class="turn search-result" onclick="loadFile('${escAttr(r.project||'')}','${escAttr(r.file)}');document.getElementById('search').value='';">
        <div class="turn-header">${r.date} ${r.time} | ${r.model}${r.truncated?' (truncated)':''}${projectLabel}</div>${titlePart}
        <div class="turn-content"><pre style="white-space:pre-wrap;word-break:break-word;">${highlighted}</pre></div>
      </div>`;
    }).join('');
}

function showWelcome() {
  const totalFiles = Object.values(filesCache).reduce((s,arr) => s + arr.length, 0);
  const projectCount = projectsData.length;
  let statsHtml = `<div class="stat"><div class="stat-val">${totalFiles}</div><div class="stat-lbl">Journal Files</div></div>`;
  if (multiProject) statsHtml = `<div class="stat"><div class="stat-val">${projectCount}</div><div class="stat-lbl">Projects</div></div>` + statsHtml;
  document.getElementById('main').innerHTML = `<div class="welcome">
    <h1>Chat History</h1>
    <p>Browse and search your AI conversation history.</p>
    <div class="stat-row">${statsHtml}</div>
    <p style="font-size:13px;color:var(--text3);margin-top:16px;">Press <kbd style="padding:2px 6px;background:var(--bg2);border:1px solid var(--border);border-radius:4px;font-size:12px;">/</kbd> to search</p>
  </div>`;
}

function esc(s) { const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
function escAttr(s) { return s.replace(/\\/g,'\\\\').replace(/'/g,"\\'"); }
function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'); }
</script>
</body>
</html>
"""
