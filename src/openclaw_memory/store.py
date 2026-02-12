"""Vector store: sqlite-vec + FTS5 hybrid search with reinforcement support."""

from __future__ import annotations

import json
import sqlite3
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _serialize_f32(vec: list[float]) -> bytes:
    """Serialize a float32 vector to bytes for sqlite-vec."""
    return struct.pack(f"{len(vec)}f", *vec)


@dataclass
class ChunkRecord:
    """A stored chunk record."""

    id: str
    uri: str
    content: str
    content_hash: str
    parent_dir: str
    type: str = ""
    section: str = ""
    importance: int = 1
    reinforcement: int = 0
    access_count: int = 0
    token_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    score: float = 0.0  # populated during search


class VectorStore:
    """SQLite-based vector store with FTS5 full-text search."""

    def __init__(self, db_path: Path | str, dimension: int = 384) -> None:
        self.db_path = Path(db_path)
        self.dimension = dimension
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._load_extensions()
            self._create_tables()
        return self._conn

    def _load_extensions(self) -> None:
        """Load sqlite-vec extension."""
        self.conn.enable_load_extension(True)
        try:
            import sqlite_vec
            sqlite_vec.load(self.conn)
        except ImportError:
            raise ImportError(
                "sqlite-vec is required. Install with: pip install sqlite-vec"
            )
        finally:
            self.conn.enable_load_extension(False)

    def _create_tables(self) -> None:
        """Create tables if they don't exist."""
        self.conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                uri TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                parent_dir TEXT NOT NULL,
                type TEXT DEFAULT '',
                section TEXT DEFAULT '',
                importance INTEGER DEFAULT 1,
                reinforcement INTEGER DEFAULT 0,
                access_count INTEGER DEFAULT 0,
                token_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_uri ON chunks(uri);
            CREATE INDEX IF NOT EXISTS idx_chunks_parent_dir ON chunks(parent_dir);
            CREATE INDEX IF NOT EXISTS idx_chunks_content_hash ON chunks(content_hash);
            CREATE INDEX IF NOT EXISTS idx_chunks_type ON chunks(type);

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
                id TEXT PRIMARY KEY,
                embedding float[{self.dimension}]
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                content, uri, section,
                content=chunks,
                content_rowid=rowid
            );
        """)
        self.conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def upsert(self, chunk_id: str, record: dict[str, Any], embedding: list[float]) -> None:
        """Insert or update a chunk and its embedding."""
        now = self._now()
        existing = self.conn.execute(
            "SELECT id FROM chunks WHERE id = ?", (chunk_id,)
        ).fetchone()

        if existing:
            # Update content and metadata, keep reinforcement/access_count
            self.conn.execute("""
                UPDATE chunks SET
                    uri = ?, content = ?, content_hash = ?, parent_dir = ?,
                    type = ?, section = ?, importance = ?, token_count = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                record["uri"], record["content"], record["content_hash"],
                record["parent_dir"], record.get("type", ""),
                record.get("section", ""), record.get("importance", 1),
                record.get("token_count", 0), now, chunk_id,
            ))
            # Update embedding
            self.conn.execute(
                "DELETE FROM chunks_vec WHERE id = ?", (chunk_id,)
            )
        else:
            self.conn.execute("""
                INSERT INTO chunks (
                    id, uri, content, content_hash, parent_dir,
                    type, section, importance, reinforcement, access_count,
                    token_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)
            """, (
                chunk_id, record["uri"], record["content"],
                record["content_hash"], record["parent_dir"],
                record.get("type", ""), record.get("section", ""),
                record.get("importance", 1), record.get("token_count", 0),
                now, now,
            ))

        # Insert embedding
        self.conn.execute(
            "INSERT INTO chunks_vec (id, embedding) VALUES (?, ?)",
            (chunk_id, _serialize_f32(embedding)),
        )
        self.conn.commit()

        # Sync FTS (rebuild triggers don't work with virtual tables)
        self._sync_fts_for(chunk_id)

    def _sync_fts_for(self, chunk_id: str) -> None:
        """Sync FTS index for a specific chunk."""
        row = self.conn.execute(
            "SELECT rowid, content, uri, section FROM chunks WHERE id = ?",
            (chunk_id,),
        ).fetchone()
        if row:
            # Delete old FTS entry if exists, then insert
            self.conn.execute(
                "INSERT INTO chunks_fts(chunks_fts, rowid, content, uri, section) "
                "VALUES('delete', ?, ?, ?, ?)",
                (row["rowid"], row["content"], row["uri"], row["section"]),
            )
            self.conn.execute(
                "INSERT INTO chunks_fts(rowid, content, uri, section) VALUES(?, ?, ?, ?)",
                (row["rowid"], row["content"], row["uri"], row["section"]),
            )
            self.conn.commit()

    def delete_by_uri(self, uri: str) -> int:
        """Delete all chunks for a given URI. Returns count deleted."""
        # Get IDs to delete from vec table
        rows = self.conn.execute(
            "SELECT id, rowid FROM chunks WHERE uri = ?", (uri,)
        ).fetchall()

        for row in rows:
            # Delete FTS
            fts_row = self.conn.execute(
                "SELECT content, uri, section FROM chunks WHERE rowid = ?",
                (row["rowid"],),
            ).fetchone()
            if fts_row:
                self.conn.execute(
                    "INSERT INTO chunks_fts(chunks_fts, rowid, content, uri, section) "
                    "VALUES('delete', ?, ?, ?, ?)",
                    (row["rowid"], fts_row["content"], fts_row["uri"], fts_row["section"]),
                )
            # Delete vec
            self.conn.execute("DELETE FROM chunks_vec WHERE id = ?", (row["id"],))

        # Delete chunks
        count = self.conn.execute("DELETE FROM chunks WHERE uri = ?", (uri,)).rowcount
        self.conn.commit()
        return count

    def increment_reinforcement(self, chunk_id: str) -> None:
        """Increment reinforcement count for a chunk."""
        now = self._now()
        self.conn.execute(
            "UPDATE chunks SET reinforcement = reinforcement + 1, updated_at = ? WHERE id = ?",
            (now, chunk_id),
        )
        self.conn.commit()

    def increment_access_count(self, chunk_ids: list[str]) -> None:
        """Increment access count for retrieved chunks."""
        if not chunk_ids:
            return
        now = self._now()
        placeholders = ",".join("?" for _ in chunk_ids)
        self.conn.execute(
            f"UPDATE chunks SET access_count = access_count + 1, updated_at = ? "
            f"WHERE id IN ({placeholders})",
            [now, *chunk_ids],
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Search operations
    # ------------------------------------------------------------------

    def vector_search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 10,
        parent_dir_filter: str = "",
        type_filter: str = "",
    ) -> list[ChunkRecord]:
        """Cosine similarity search via sqlite-vec."""
        # sqlite-vec search
        rows = self.conn.execute(
            """
            SELECT v.id, v.distance
            FROM chunks_vec v
            WHERE v.embedding MATCH ?
            ORDER BY v.distance
            LIMIT ?
            """,
            (_serialize_f32(query_embedding), top_k * 3),  # over-fetch for filtering
        ).fetchall()

        if not rows:
            return []

        # Fetch metadata and apply filters
        ids = [r["id"] for r in rows]
        distances = {r["id"]: r["distance"] for r in rows}

        placeholders = ",".join("?" for _ in ids)
        filter_clauses = []
        filter_params: list[Any] = []

        if parent_dir_filter:
            filter_clauses.append("parent_dir LIKE ?")
            filter_params.append(f"{parent_dir_filter}%")
        if type_filter:
            filter_clauses.append("type = ?")
            filter_params.append(type_filter)

        where = f"AND {' AND '.join(filter_clauses)}" if filter_clauses else ""

        chunk_rows = self.conn.execute(
            f"SELECT * FROM chunks WHERE id IN ({placeholders}) {where}",
            [*ids, *filter_params],
        ).fetchall()

        results: list[ChunkRecord] = []
        for row in chunk_rows:
            dist = distances.get(row["id"], 1.0)
            # Convert distance to similarity (sqlite-vec returns L2 or cosine distance)
            similarity = 1.0 - dist if dist <= 1.0 else 1.0 / (1.0 + dist)
            record = ChunkRecord(
                id=row["id"],
                uri=row["uri"],
                content=row["content"],
                content_hash=row["content_hash"],
                parent_dir=row["parent_dir"],
                type=row["type"],
                section=row["section"],
                importance=row["importance"],
                reinforcement=row["reinforcement"],
                access_count=row["access_count"],
                token_count=row["token_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                score=similarity,
            )
            results.append(record)

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def fts_search(
        self,
        query: str,
        *,
        top_k: int = 10,
        parent_dir_filter: str = "",
    ) -> list[ChunkRecord]:
        """BM25 full-text search via FTS5."""
        filter_clause = ""
        filter_params: list[Any] = []

        if parent_dir_filter:
            filter_clause = "AND c.parent_dir LIKE ?"
            filter_params.append(f"{parent_dir_filter}%")

        rows = self.conn.execute(
            f"""
            SELECT c.*, bm25(chunks_fts) as rank
            FROM chunks_fts fts
            JOIN chunks c ON c.rowid = fts.rowid
            WHERE chunks_fts MATCH ?
            {filter_clause}
            ORDER BY rank
            LIMIT ?
            """,
            [query, *filter_params, top_k],
        ).fetchall()

        results: list[ChunkRecord] = []
        for row in rows:
            # BM25 returns negative scores (lower = better)
            bm25_score = -row["rank"] if row["rank"] else 0.0
            # Normalize to 0-1 range (rough approximation)
            normalized = min(bm25_score / 20.0, 1.0)
            record = ChunkRecord(
                id=row["id"],
                uri=row["uri"],
                content=row["content"],
                content_hash=row["content_hash"],
                parent_dir=row["parent_dir"],
                type=row["type"],
                section=row["section"],
                importance=row["importance"],
                reinforcement=row["reinforcement"],
                access_count=row["access_count"],
                token_count=row["token_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                score=normalized,
            )
            results.append(record)

        return results

    def find_similar(
        self,
        query_embedding: list[float],
        *,
        uri_filter: str = "",
        threshold: float = 0.85,
        top_k: int = 5,
    ) -> list[ChunkRecord]:
        """Find chunks similar to query, used for dedup/conflict detection."""
        results = self.vector_search(query_embedding, top_k=top_k)
        filtered = [r for r in results if r.score >= threshold]
        if uri_filter:
            filtered = [r for r in filtered if r.uri == uri_filter]
        return filtered

    def get_by_id(self, chunk_id: str) -> ChunkRecord | None:
        """Get a single chunk by ID."""
        row = self.conn.execute(
            "SELECT * FROM chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
        if not row:
            return None
        return ChunkRecord(
            id=row["id"],
            uri=row["uri"],
            content=row["content"],
            content_hash=row["content_hash"],
            parent_dir=row["parent_dir"],
            type=row["type"],
            section=row["section"],
            importance=row["importance"],
            reinforcement=row["reinforcement"],
            access_count=row["access_count"],
            token_count=row["token_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_all_uris(self) -> set[str]:
        """Get all unique URIs in the store."""
        rows = self.conn.execute("SELECT DISTINCT uri FROM chunks").fetchall()
        return {row["uri"] for row in rows}

    def get_max_reinforcement(self) -> int:
        """Get the maximum reinforcement count across all chunks."""
        row = self.conn.execute(
            "SELECT MAX(reinforcement) as max_r FROM chunks"
        ).fetchone()
        return row["max_r"] or 0 if row else 0

    def get_max_access_count(self) -> int:
        """Get the maximum access count across all chunks."""
        row = self.conn.execute(
            "SELECT MAX(access_count) as max_a FROM chunks"
        ).fetchone()
        return row["max_a"] or 0 if row else 0

    def get_stats(self) -> dict[str, Any]:
        """Get store statistics."""
        row = self.conn.execute("""
            SELECT
                COUNT(*) as total_chunks,
                COUNT(DISTINCT uri) as total_files,
                SUM(token_count) as total_tokens,
                MAX(reinforcement) as max_reinforcement,
                MAX(access_count) as max_access_count
            FROM chunks
        """).fetchone()
        return dict(row) if row else {}

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
