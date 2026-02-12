"""Retriever: salience-based scoring, token budget, fast paths, hybrid search."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .store import ChunkRecord

if TYPE_CHECKING:
    from .embeddings import EmbeddingProvider
    from .store import VectorStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fast path rules
# ---------------------------------------------------------------------------

_FAST_PATH_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(偏好|preference|喜欢什么|习惯)", re.I), "user/preferences.md"),
    (re.compile(r"(指令|规则|规范|instruction|rule)", re.I), "user/instructions.md"),
    (re.compile(r"(任务|进度|task|todo|待办)", re.I), "TASKS.md"),
    (re.compile(r"(谁是|负责人|团队|成员|entity|people)", re.I), "user/entities.md"),
    (re.compile(r"(决策|决定|ADR|decision)", re.I), "agent/decisions.md"),
    (re.compile(r"(模式|方案|pattern|solution)", re.I), "agent/patterns.md"),
]

_TIMELINE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(最近|近期|这几天|today|recent|最近三天|past\s*\d+\s*days?)", re.I),
    re.compile(r"(上周|上个星期|last\s*week)", re.I),
    re.compile(r"(昨天|yesterday|前天)", re.I),
]


# ---------------------------------------------------------------------------
# Salience scoring
# ---------------------------------------------------------------------------

def compute_salience(
    semantic_score: float,
    reinforcement: int,
    max_reinforcement: int,
    updated_at: str,
    access_count: int,
    max_access: int,
    half_life_days: float = 30.0,
    *,
    w_semantic: float = 0.50,
    w_reinforcement: float = 0.20,
    w_recency: float = 0.20,
    w_access: float = 0.10,
) -> float:
    """Compute salience score using configurable multi-dimensional weighting.

    Default weights (configurable in .openclaw_memory.toml under [search]):
      salience = w_semantic * similarity + w_reinforcement * reinforcement_score
               + w_recency * recency_decay + w_access * access_frequency
    """
    # Reinforcement score (log-normalized)
    if max_reinforcement > 0:
        reinf_score = math.log(reinforcement + 1) / math.log(max_reinforcement + 2)
    else:
        reinf_score = 0.0

    # Recency decay (exponential half-life)
    try:
        updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days_old = max((now - updated).total_seconds() / 86400, 0)
        decay_lambda = math.log(2) / half_life_days
        recency = math.exp(-decay_lambda * days_old)
    except (ValueError, TypeError):
        recency = 0.5  # default if parsing fails

    # Access frequency (log-normalized)
    if max_access > 0:
        access_score = math.log(access_count + 1) / math.log(max_access + 2)
    else:
        access_score = 0.0

    salience = (
        w_semantic * semantic_score
        + w_reinforcement * reinf_score
        + w_recency * recency
        + w_access * access_score
    )
    return salience


# ---------------------------------------------------------------------------
# Search result
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    """A single search result with salience score."""
    content: str
    uri: str
    chunk_id: str
    salience: float
    semantic_score: float
    reinforcement: int
    token_count: int
    type: str = ""
    section: str = ""


@dataclass
class SearchResponse:
    """Complete search response with token accounting."""
    results: list[SearchResult] = field(default_factory=list)
    total_tokens: int = 0
    budget_remaining: int = 0
    fast_path_used: bool = False
    query: str = ""


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------

class Retriever:
    """Memory retriever with salience scoring, token budget, and fast paths."""

    def __init__(
        self,
        store: "VectorStore",
        embedder: "EmbeddingProvider",
        memory_roots: list[Path],
        *,
        default_max_tokens: int = 1500,
        half_life_days: float = 30.0,
        w_semantic: float = 0.50,
        w_reinforcement: float = 0.20,
        w_recency: float = 0.20,
        w_access: float = 0.10,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.memory_roots = memory_roots
        self.default_max_tokens = default_max_tokens
        self.half_life_days = half_life_days
        self.w_semantic = w_semantic
        self.w_reinforcement = w_reinforcement
        self.w_recency = w_recency
        self.w_access = w_access

    async def search(
        self,
        query: str,
        *,
        scope: str = "",
        max_tokens: int | None = None,
        top_k: int = 10,
    ) -> SearchResponse:
        """Search memories with salience scoring and token budget.

        Args:
            query: Search query text.
            scope: Filter scope (user/journal/agent/global or empty for all).
            max_tokens: Maximum total tokens in results (default from config).
            top_k: Maximum number of results before token budget cutoff.
        """
        budget = max_tokens or self.default_max_tokens

        # Try fast path first
        fast_result = self._try_fast_path(query, budget)
        if fast_result is not None:
            return fast_result

        # Try timeline path
        timeline_result = self._try_timeline_path(query, budget)
        if timeline_result is not None:
            return timeline_result

        # Full vector + FTS hybrid search
        return await self._hybrid_search(query, scope=scope, budget=budget, top_k=top_k)

    def _try_fast_path(self, query: str, budget: int) -> SearchResponse | None:
        """Check if query matches a fast-path rule (direct file read)."""
        for pattern, file_rel in _FAST_PATH_RULES:
            if pattern.search(query):
                # Try reading from each memory root
                for root in self.memory_roots:
                    file_path = root / file_rel
                    if file_path.is_file():
                        content = file_path.read_text(encoding="utf-8")
                        from .chunker import count_tokens
                        tokens = count_tokens(content)
                        return SearchResponse(
                            results=[SearchResult(
                                content=content,
                                uri=file_rel,
                                chunk_id="fast-path",
                                salience=1.0,
                                semantic_score=1.0,
                                reinforcement=0,
                                token_count=tokens,
                            )],
                            total_tokens=tokens,
                            budget_remaining=budget - tokens,
                            fast_path_used=True,
                            query=query,
                        )
        return None

    def _try_timeline_path(self, query: str, budget: int) -> SearchResponse | None:
        """Check if query is a timeline query (recent events)."""
        for pattern in _TIMELINE_PATTERNS:
            if pattern.search(query):
                return self._read_recent_journals(budget, days=7)
        return None

    def _read_recent_journals(self, budget: int, days: int = 7) -> SearchResponse:
        """Read recent journal files within token budget."""
        from .chunker import count_tokens

        results: list[SearchResult] = []
        total_tokens = 0

        for root in self.memory_roots:
            journal_dir = root / "journal"
            if not journal_dir.is_dir():
                continue

            # Get journal files sorted by name (date) descending
            files = sorted(journal_dir.glob("*.md"), reverse=True)[:days]

            for f in files:
                content = f.read_text(encoding="utf-8")
                tokens = count_tokens(content)

                if total_tokens + tokens > budget:
                    break

                results.append(SearchResult(
                    content=content,
                    uri=f"journal/{f.name}",
                    chunk_id="timeline",
                    salience=1.0,
                    semantic_score=1.0,
                    reinforcement=0,
                    token_count=tokens,
                ))
                total_tokens += tokens

        return SearchResponse(
            results=results,
            total_tokens=total_tokens,
            budget_remaining=budget - total_tokens,
            fast_path_used=True,
            query="timeline",
        )

    async def _hybrid_search(
        self,
        query: str,
        *,
        scope: str,
        budget: int,
        top_k: int,
    ) -> SearchResponse:
        """Full hybrid search: vector + FTS with salience reranking."""
        # Embed query
        query_embedding = await self.embedder.embed_single(query)

        # Scope → parent_dir filter
        parent_dir_filter = scope if scope and scope != "global" else ""

        # Vector search
        vec_results = self.store.vector_search(
            query_embedding,
            top_k=top_k * 2,
            parent_dir_filter=parent_dir_filter,
        )

        # FTS search
        fts_results = self.store.fts_search(
            query,
            top_k=top_k * 2,
            parent_dir_filter=parent_dir_filter,
        )

        # RRF merge
        merged = self._rrf_merge(vec_results, fts_results, k=60)

        # Get normalization constants
        max_reinf = self.store.get_max_reinforcement()
        max_access = self.store.get_max_access_count()

        # Compute salience scores
        scored: list[SearchResult] = []
        for record in merged:
            salience = compute_salience(
                semantic_score=record.score,
                reinforcement=record.reinforcement,
                max_reinforcement=max_reinf,
                updated_at=record.updated_at,
                access_count=record.access_count,
                max_access=max_access,
                half_life_days=self.half_life_days,
                w_semantic=self.w_semantic,
                w_reinforcement=self.w_reinforcement,
                w_recency=self.w_recency,
                w_access=self.w_access,
            )
            scored.append(SearchResult(
                content=record.content,
                uri=record.uri,
                chunk_id=record.id,
                salience=salience,
                semantic_score=record.score,
                reinforcement=record.reinforcement,
                token_count=record.token_count,
                type=record.type,
                section=record.section,
            ))

        # Sort by salience
        scored.sort(key=lambda r: r.salience, reverse=True)

        # Apply token budget
        final: list[SearchResult] = []
        total_tokens = 0
        for result in scored:
            if total_tokens + result.token_count > budget:
                break
            final.append(result)
            total_tokens += result.token_count
            if len(final) >= top_k:
                break

        # Update access counts
        chunk_ids = [r.chunk_id for r in final if r.chunk_id not in ("fast-path", "timeline")]
        if chunk_ids:
            self.store.increment_access_count(chunk_ids)

        return SearchResponse(
            results=final,
            total_tokens=total_tokens,
            budget_remaining=budget - total_tokens,
            fast_path_used=False,
            query=query,
        )

    def _rrf_merge(
        self,
        vec_results: list[ChunkRecord],
        fts_results: list[ChunkRecord],
        k: int = 60,
    ) -> list[ChunkRecord]:
        """Reciprocal Rank Fusion merge of vector and FTS results."""
        scores: dict[str, float] = {}
        records: dict[str, ChunkRecord] = {}

        for rank, record in enumerate(vec_results):
            rrf = 1.0 / (k + rank + 1)
            scores[record.id] = scores.get(record.id, 0) + rrf
            records[record.id] = record

        for rank, record in enumerate(fts_results):
            rrf = 1.0 / (k + rank + 1)
            scores[record.id] = scores.get(record.id, 0) + rrf
            if record.id not in records:
                records[record.id] = record

        # Update scores on records
        for cid, record in records.items():
            record.score = scores[cid]

        # Sort by RRF score
        sorted_records = sorted(records.values(), key=lambda r: r.score, reverse=True)
        return sorted_records
