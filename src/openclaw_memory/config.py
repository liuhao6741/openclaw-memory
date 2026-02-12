"""Configuration module: dual-layer TOML config + environment variable overrides."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class EmbeddingConfig:
    provider: str = "local"  # openai | ollama | local
    model: str = ""  # empty = use provider default
    api_key: str = ""
    base_url: str = ""
    dimension: int = 0  # 0 = auto-detect from provider


@dataclass
class PrivacyConfig:
    enabled: bool = True
    patterns: list[str] = field(default_factory=lambda: [
        r"sk-[a-zA-Z0-9]{20,}",           # OpenAI API Key
        r"ghp_[a-zA-Z0-9]{36}",           # GitHub Token
        r"password\s*[:=]\s*\S+",          # Password assignment
        r"secret\s*[:=]\s*\S+",            # Secret assignment
        r"192\.168\.\d+\.\d+",             # Internal IPs
        r"10\.\d+\.\d+\.\d+",             # Internal IPs
        r"localhost:\d+",                   # Local services
    ])


@dataclass
class SearchConfig:
    default_max_tokens: int = 1500
    recency_half_life_days: float = 30.0
    default_top_k: int = 10


@dataclass
class ProjectMeta:
    name: str = ""
    description: str = ""


@dataclass
class OpenClawConfig:
    """Merged configuration from global + project layers."""

    project: ProjectMeta = field(default_factory=ProjectMeta)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    search: SearchConfig = field(default_factory=SearchConfig)

    # Resolved paths
    global_root: Path = field(default_factory=lambda: Path.home() / ".openclaw_memory")
    project_root: Path | None = None  # None = no project detected

    @property
    def global_user_dir(self) -> Path:
        return self.global_root / "user"

    @property
    def global_index_db(self) -> Path:
        return self.global_root / "index.db"

    @property
    def project_memory_dir(self) -> Path | None:
        if self.project_root is None:
            return None
        return self.project_root / ".openclaw_memory"

    @property
    def project_index_db(self) -> Path | None:
        d = self.project_memory_dir
        return d / "index.db" if d else None


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file, returning {} if it doesn't exist."""
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base* (non-destructive)."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _apply_env(cfg: dict[str, Any]) -> dict[str, Any]:
    """Apply OPENCLAW_* environment variables as overrides.

    Mapping: ``OPENCLAW_SECTION_FIELD`` → ``cfg[section][field]``.
    Example: ``OPENCLAW_EMBEDDING_PROVIDER=openai`` → ``cfg["embedding"]["provider"] = "openai"``.
    """
    prefix = "OPENCLAW_"
    for key, val in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix):].lower().split("_", maxsplit=1)
        if len(parts) == 2:
            section, field_name = parts
            cfg.setdefault(section, {})[field_name] = val
        elif len(parts) == 1:
            cfg[parts[0]] = val
    return cfg


def _detect_project_root(cwd: Path | None = None) -> Path | None:
    """Detect project root by looking for .openclaw.toml or falling back to git root."""
    cwd = cwd or Path.cwd()

    # Walk up looking for .openclaw_memory.toml
    for parent in [cwd, *cwd.parents]:
        if (parent / ".openclaw_memory.toml").is_file():
            return parent
        if (parent / ".openclaw_memory" / ".openclaw_memory.toml").is_file():
            return parent

    # Fallback: git root
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return None


def _build_config_dict(
    global_root: Path,
    project_root: Path | None,
) -> dict[str, Any]:
    """Build merged config dict: defaults → global TOML → project TOML → env vars."""
    # Global config
    global_cfg = _load_toml(global_root / "config.toml")

    # Project config
    project_cfg: dict[str, Any] = {}
    if project_root:
        project_cfg = _load_toml(project_root / ".openclaw_memory.toml")
        if not project_cfg:
            project_cfg = _load_toml(project_root / ".openclaw_memory" / ".openclaw_memory.toml")

    # Merge: global → project → env
    merged = _deep_merge(global_cfg, project_cfg)
    merged = _apply_env(merged)
    return merged


def _dict_to_dataclass(raw: dict[str, Any], global_root: Path, project_root: Path | None) -> OpenClawConfig:
    """Convert raw merged dict into typed OpenClawConfig."""
    cfg = OpenClawConfig(global_root=global_root, project_root=project_root)

    # Project meta
    proj = raw.get("project", {})
    cfg.project.name = proj.get("name", "")
    cfg.project.description = proj.get("description", "")

    # Embedding
    emb = raw.get("embedding", {})
    cfg.embedding.provider = emb.get("provider", cfg.embedding.provider)
    cfg.embedding.model = emb.get("model", cfg.embedding.model)
    cfg.embedding.api_key = emb.get("api_key", "") or os.environ.get("OPENAI_API_KEY", "")
    cfg.embedding.base_url = emb.get("base_url", cfg.embedding.base_url)
    dim = emb.get("dimension", 0)
    cfg.embedding.dimension = int(dim) if dim else 0

    # Privacy
    priv = raw.get("privacy", {})
    if "enabled" in priv:
        cfg.privacy.enabled = bool(priv["enabled"])
    if "patterns" in priv:
        cfg.privacy.patterns = list(priv["patterns"])

    # Search
    srch = raw.get("search", {})
    if "default_max_tokens" in srch:
        cfg.search.default_max_tokens = int(srch["default_max_tokens"])
    if "recency_half_life_days" in srch:
        cfg.search.recency_half_life_days = float(srch["recency_half_life_days"])
    if "default_top_k" in srch:
        cfg.search.default_top_k = int(srch["default_top_k"])

    return cfg


def load_config(
    cwd: Path | None = None,
    global_root: Path | None = None,
) -> OpenClawConfig:
    """Load and merge configuration from all layers.

    Priority (lowest → highest):
    1. Built-in defaults
    2. ``~/.openclaw_memory/config.toml``
    3. ``<project>/.openclaw_memory.toml``
    4. ``OPENCLAW_*`` environment variables
    """
    global_root = global_root or Path.home() / ".openclaw_memory"
    project_root = _detect_project_root(cwd)
    raw = _build_config_dict(global_root, project_root)
    return _dict_to_dataclass(raw, global_root, project_root)


def ensure_directories(cfg: OpenClawConfig) -> None:
    """Create memory directories if they don't exist."""
    # Global
    cfg.global_user_dir.mkdir(parents=True, exist_ok=True)

    # Project
    pdir = cfg.project_memory_dir
    if pdir:
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "journal").mkdir(exist_ok=True)
        (pdir / "agent").mkdir(exist_ok=True)
