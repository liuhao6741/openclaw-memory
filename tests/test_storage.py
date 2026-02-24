"""Tests for openclaw_memory.storage module."""

from __future__ import annotations

from pathlib import Path

import pytest

from openclaw_memory.storage import (
    append_agent,
    grep_search,
    write_turn,
    _parse_turns,
)


@pytest.fixture
def journal_dir(tmp_path: Path) -> Path:
    d = tmp_path / "journal"
    d.mkdir()
    return d


class TestWriteTurn:
    def test_creates_journal_file(self, journal_dir: Path):
        path = write_turn(journal_dir, "Hello", "Hi there", model="test-model")
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "### User" in content
        assert "Hello" in content
        assert "### Agent" in content
        assert "Hi there" in content
        assert "test-model" in content

    def test_appends_separator_for_second_turn(self, journal_dir: Path):
        write_turn(journal_dir, "First", "Response 1")
        write_turn(journal_dir, "Second", "Response 2")
        content = (journal_dir / _today_filename()).read_text(encoding="utf-8")
        assert content.count("---") >= 1
        assert "First" in content
        assert "Second" in content

    def test_code_changes_section(self, journal_dir: Path):
        write_turn(journal_dir, "Do it", "Done", code_changes="- `foo.py` (created)")
        content = (journal_dir / _today_filename()).read_text(encoding="utf-8")
        assert "### Code Changes" in content
        assert "foo.py" in content

    def test_empty_code_changes_omitted(self, journal_dir: Path):
        write_turn(journal_dir, "Do it", "Done", code_changes="")
        content = (journal_dir / _today_filename()).read_text(encoding="utf-8")
        assert "### Code Changes" not in content


class TestAppendAgent:
    def test_appends_to_last_agent_section(self, journal_dir: Path):
        write_turn(journal_dir, "Q", "Part 1")
        ok = append_agent(journal_dir, "Part 2")
        assert ok
        content = (journal_dir / _today_filename()).read_text(encoding="utf-8")
        assert "Part 1" in content
        assert "Part 2" in content

    def test_returns_false_when_no_journal(self, journal_dir: Path):
        ok = append_agent(journal_dir, "chunk")
        assert not ok


class TestGrepSearch:
    def test_finds_matching_turn(self, journal_dir: Path):
        write_turn(journal_dir, "Fix the N+1 query", "Use joinedload()")
        results = grep_search(journal_dir, "N+1")
        assert len(results) == 1
        assert "N+1" in results[0]["content"]

    def test_case_insensitive(self, journal_dir: Path):
        write_turn(journal_dir, "Hello World", "response")
        assert len(grep_search(journal_dir, "hello world")) == 1

    def test_since_filter(self, journal_dir: Path):
        write_turn(journal_dir, "today", "response")
        results = grep_search(journal_dir, "today", since="9999-01-01")
        assert len(results) == 0

    def test_max_results(self, journal_dir: Path):
        for i in range(5):
            write_turn(journal_dir, f"question {i}", f"answer {i}")
        results = grep_search(journal_dir, "question", max_results=2)
        assert len(results) == 2

    def test_truncation(self, journal_dir: Path):
        long_response = "x" * 5000
        write_turn(journal_dir, "Q", long_response)
        results = grep_search(journal_dir, "Q")
        assert len(results) == 1
        assert results[0]["truncated"]
        assert "[...truncated" in results[0]["content"]

    def test_empty_dir(self, journal_dir: Path):
        results = grep_search(journal_dir, "anything")
        assert results == []


class TestParseTurns:
    def test_parses_single_turn(self):
        content = "## 14:32 | claude-4\n\n### User\n\nHello\n\n### Agent\n\nHi"
        turns = _parse_turns(content, "2026-02-24", "2026-02-24.md")
        assert len(turns) == 1
        assert turns[0]["time"] == "14:32"
        assert turns[0]["model"] == "claude-4"

    def test_parses_multiple_turns(self):
        content = (
            "## 10:00 | m1\n\n### User\n\nQ1\n\n### Agent\n\nA1\n\n"
            "---\n\n"
            "## 11:00 | m2\n\n### User\n\nQ2\n\n### Agent\n\nA2"
        )
        turns = _parse_turns(content, "2026-02-24", "2026-02-24.md")
        assert len(turns) == 2
        assert turns[0]["model"] == "m1"
        assert turns[1]["model"] == "m2"


def _today_filename() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d") + ".md"
