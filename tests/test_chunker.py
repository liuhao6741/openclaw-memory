"""Tests for the Markdown chunker."""

from openclaw_memory.chunker import (
    Chunk,
    chunk_markdown,
    compute_content_hash,
    parse_frontmatter,
)


def test_compute_content_hash_deterministic():
    h1 = compute_content_hash("Hello World")
    h2 = compute_content_hash("hello  world")
    assert h1 == h2  # normalized: lowercase + collapse whitespace


def test_compute_content_hash_different():
    h1 = compute_content_hash("Hello World")
    h2 = compute_content_hash("Goodbye World")
    assert h1 != h2


def test_parse_frontmatter():
    text = """---
type: preference
importance: 4
---
- Item 1
- Item 2
"""
    meta, body = parse_frontmatter(text)
    assert meta["type"] == "preference"
    assert meta["importance"] == 4
    assert "Item 1" in body


def test_parse_frontmatter_no_frontmatter():
    text = "# Just a heading\n\nSome content"
    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert "Just a heading" in body


def test_chunk_markdown_basic():
    text = """---
type: event
---
# Session Log

## Morning

- Fixed a bug in the auth module

## Afternoon

- Reviewed pull requests
- Deployed to staging
"""
    chunks = chunk_markdown(text, source="journal/2026-02-12.md")
    assert len(chunks) >= 2  # At least 2 sections

    # All chunks should have source set
    for c in chunks:
        assert c.source == "journal/2026-02-12.md"
        assert c.content_hash
        assert c.chunk_id
        assert c.token_count > 0

    # Metadata should be shared
    for c in chunks:
        assert c.metadata.get("type") == "event"


def test_chunk_markdown_structured_session():
    text = """---
type: event
---
## Session 14:30

### 请求
Implement webhook handling

### 学到了什么
- Stripe needs signature verification

### 完成了什么
- Implemented signature verification

### 下一步
- Handle failed events
"""
    chunks = chunk_markdown(text, source="journal/2026-02-12.md")
    sections = [c.section for c in chunks if c.section]
    # Should detect structured sections
    assert any(s in sections for s in ["request", "learned", "completed", "next"])


def test_chunk_markdown_empty():
    chunks = chunk_markdown("", source="empty.md")
    assert chunks == []


def test_chunk_id_unique():
    text = """## Section A

Content A

## Section B

Content B
"""
    chunks = chunk_markdown(text, source="test.md")
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))  # All unique
