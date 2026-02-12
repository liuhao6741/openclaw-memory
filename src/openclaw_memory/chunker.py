"""Markdown chunker: heading-based splitting, frontmatter parsing, content hashing."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

import frontmatter

# ---------------------------------------------------------------------------
# Token counting (lazy-loaded tiktoken)
# ---------------------------------------------------------------------------

_TOKENIZER = None


def _get_tokenizer():
    global _TOKENIZER
    if _TOKENIZER is None:
        import tiktoken
        _TOKENIZER = tiktoken.get_encoding("cl100k_base")
    return _TOKENIZER


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken cl100k_base encoding."""
    return len(_get_tokenizer().encode(text))


# ---------------------------------------------------------------------------
# Chunk data class
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    """A chunk of markdown content with metadata."""

    content: str
    source: str  # file path relative to memory root
    start_line: int
    end_line: int
    content_hash: str
    heading: str = ""  # nearest heading
    section: str = ""  # structured section: request/learned/completed/next
    token_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)  # from frontmatter

    @property
    def chunk_id(self) -> str:
        """Compute deterministic chunk ID from source + position + content hash."""
        raw = f"{self.source}:{self.start_line}:{self.end_line}:{self.content_hash}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Content hashing
# ---------------------------------------------------------------------------

def compute_content_hash(text: str) -> str:
    """SHA-256 hash of normalized text content."""
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Structured session section detection
# ---------------------------------------------------------------------------

_SECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "request": re.compile(r"^(#{0,6}\s*)?(请求|request)", re.IGNORECASE),
    "learned": re.compile(r"^(#{0,6}\s*)?(学到了什么|learned|what\s+.*learned)", re.IGNORECASE),
    "completed": re.compile(r"^(#{0,6}\s*)?(完成了什么|completed|what\s+.*completed)", re.IGNORECASE),
    "next": re.compile(r"^(#{0,6}\s*)?(下一步|next\s*steps?)", re.IGNORECASE),
}


def _detect_section(heading: str) -> str:
    """Detect structured session section from heading text."""
    for section_name, pattern in _SECTION_PATTERNS.items():
        if pattern.match(heading.strip()):
            return section_name
    return ""


# ---------------------------------------------------------------------------
# Markdown splitting
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def _split_by_headings(text: str) -> list[tuple[str, int, int, str]]:
    """Split markdown text by headings (level 2 and 3).

    Returns list of (content, start_line, end_line, heading_text).
    Each ### heading creates its own section for structured session detection.
    """
    lines = text.split("\n")
    sections: list[tuple[str, int, int, str]] = []
    current_start = 0
    current_heading = ""

    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m and i > 0:
            # End previous section
            section_text = "\n".join(lines[current_start:i]).strip()
            if section_text:
                sections.append((section_text, current_start + 1, i, current_heading))
            current_start = i
            current_heading = m.group(2).strip()
        elif m and i == 0:
            current_heading = m.group(2).strip()

    # Last section
    section_text = "\n".join(lines[current_start:]).strip()
    if section_text:
        sections.append((section_text, current_start + 1, len(lines), current_heading))

    return sections


def _detect_sections_in_chunk(content: str) -> str:
    """Detect structured session section from any heading within chunk content."""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("#"):
            detected = _detect_section(line.lstrip("#").strip())
            if detected:
                return detected
    return ""


def _split_long_section(
    content: str,
    start_line: int,
    max_chunk_size: int,
) -> list[tuple[str, int, int]]:
    """Split a long section at paragraph boundaries."""
    paragraphs = re.split(r"\n\n+", content)
    chunks: list[tuple[str, int, int]] = []
    current_parts: list[str] = []
    current_tokens = 0
    chunk_start = start_line

    for para in paragraphs:
        para_tokens = count_tokens(para)
        if current_tokens + para_tokens > max_chunk_size and current_parts:
            chunk_text = "\n\n".join(current_parts)
            line_count = chunk_text.count("\n") + 1
            chunks.append((chunk_text, chunk_start, chunk_start + line_count - 1))
            chunk_start = chunk_start + line_count
            current_parts = [para]
            current_tokens = para_tokens
        else:
            current_parts.append(para)
            current_tokens += para_tokens

    if current_parts:
        chunk_text = "\n\n".join(current_parts)
        line_count = chunk_text.count("\n") + 1
        chunks.append((chunk_text, chunk_start, chunk_start + line_count - 1))

    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown text.

    Returns (metadata_dict, body_text).
    """
    post = frontmatter.loads(text)
    return dict(post.metadata), post.content


def chunk_markdown(
    text: str,
    source: str = "",
    *,
    max_chunk_tokens: int = 500,
) -> list[Chunk]:
    """Split markdown text into chunks.

    1. Parse frontmatter (shared across all chunks).
    2. Split by headings.
    3. Further split sections exceeding *max_chunk_tokens*.
    4. Detect structured session sections (request/learned/completed/next).
    """
    metadata, body = parse_frontmatter(text)

    sections = _split_by_headings(body)
    if not sections and body.strip():
        sections = [(body.strip(), 1, body.count("\n") + 1, "")]

    chunks: list[Chunk] = []

    for content, start, end, heading in sections:
        tokens = count_tokens(content)
        section = _detect_section(heading) or _detect_sections_in_chunk(content)

        if tokens <= max_chunk_tokens:
            c_hash = compute_content_hash(content)
            chunks.append(Chunk(
                content=content,
                source=source,
                start_line=start,
                end_line=end,
                content_hash=c_hash,
                heading=heading,
                section=section,
                token_count=tokens,
                metadata=metadata,
            ))
        else:
            # Split long section
            sub_chunks = _split_long_section(content, start, max_chunk_tokens)
            for sub_content, sub_start, sub_end in sub_chunks:
                c_hash = compute_content_hash(sub_content)
                chunks.append(Chunk(
                    content=sub_content,
                    source=source,
                    start_line=sub_start,
                    end_line=sub_end,
                    content_hash=c_hash,
                    heading=heading,
                    section=section,
                    token_count=count_tokens(sub_content),
                    metadata=metadata,
                ))

    return chunks
