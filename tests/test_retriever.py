"""Tests for the retriever: salience scoring."""

from openclaw_memory.retriever import compute_salience


def test_salience_basic():
    """High semantic score should produce high salience."""
    score = compute_salience(
        semantic_score=0.9,
        reinforcement=0,
        max_reinforcement=10,
        updated_at="2026-02-12T10:00:00+00:00",
        access_count=0,
        max_access=10,
    )
    assert 0.0 < score < 1.0
    assert score > 0.4  # Semantic dominates


def test_salience_reinforcement_boost():
    """High reinforcement should boost salience."""
    low = compute_salience(
        semantic_score=0.5,
        reinforcement=0,
        max_reinforcement=10,
        updated_at="2026-02-12T10:00:00+00:00",
        access_count=0,
        max_access=10,
    )
    high = compute_salience(
        semantic_score=0.5,
        reinforcement=10,
        max_reinforcement=10,
        updated_at="2026-02-12T10:00:00+00:00",
        access_count=0,
        max_access=10,
    )
    assert high > low


def test_salience_recency_boost():
    """Recent memories should have higher salience than old ones."""
    recent = compute_salience(
        semantic_score=0.5,
        reinforcement=0,
        max_reinforcement=0,
        updated_at="2026-02-12T10:00:00+00:00",  # Today-ish
        access_count=0,
        max_access=0,
    )
    old = compute_salience(
        semantic_score=0.5,
        reinforcement=0,
        max_reinforcement=0,
        updated_at="2025-01-01T10:00:00+00:00",  # Over a year ago
        access_count=0,
        max_access=0,
    )
    assert recent > old


def test_salience_access_boost():
    """Frequently accessed memories should rank higher."""
    low_access = compute_salience(
        semantic_score=0.5,
        reinforcement=0,
        max_reinforcement=0,
        updated_at="2026-02-12T10:00:00+00:00",
        access_count=0,
        max_access=10,
    )
    high_access = compute_salience(
        semantic_score=0.5,
        reinforcement=0,
        max_reinforcement=0,
        updated_at="2026-02-12T10:00:00+00:00",
        access_count=10,
        max_access=10,
    )
    assert high_access > low_access


def test_salience_range():
    """Salience should always be between 0 and 1."""
    score = compute_salience(
        semantic_score=1.0,
        reinforcement=100,
        max_reinforcement=100,
        updated_at="2026-02-12T10:00:00+00:00",
        access_count=100,
        max_access=100,
    )
    assert 0.0 <= score <= 1.0
