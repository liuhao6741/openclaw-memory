"""Tests for the primer module."""

import tempfile
from pathlib import Path

import frontmatter

from openclaw_memory.primer import (
    build_primer,
    write_session_to_journal,
    write_tasks,
)


def _make_memory_structure(root: Path) -> None:
    """Create a minimal memory structure for testing."""
    user_dir = root / "user"
    user_dir.mkdir(parents=True)

    # preferences.md
    post = frontmatter.Post(
        content="- Functional programming style\n- TypeScript strict mode\n",
        type="preference",
    )
    (user_dir / "preferences.md").write_text(frontmatter.dumps(post))

    # entities.md
    post = frontmatter.Post(
        content="- Alice: Backend engineer\n- Bob: Frontend lead\n",
        type="entity",
    )
    (user_dir / "entities.md").write_text(frontmatter.dumps(post))

    # instructions.md
    post = frontmatter.Post(
        content="- Always use snake_case\n- Run tests before commit\n",
        type="instruction",
    )
    (user_dir / "instructions.md").write_text(frontmatter.dumps(post))


def test_build_primer_basic():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_memory_structure(root)

        result = build_primer(
            global_root=root,
            project_name="test-project",
            project_description="A test project",
        )

        assert "Alice" in result
        assert "Functional programming" in result
        assert "test-project" in result


def test_build_primer_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "user").mkdir(parents=True)

        result = build_primer(global_root=root)
        assert "暂无记录" in result


def test_write_session_to_journal():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        (project_root / ".openclaw_memory" / "journal").mkdir(parents=True)

        summary = {
            "request": "Implement auth module",
            "learned": ["JWT needs refresh tokens", "Redis for blacklist"],
            "completed": ["Implemented login endpoint", "Added token refresh"],
            "next_steps": ["Add rate limiting"],
        }

        path = write_session_to_journal(project_root, summary)
        assert path.exists()

        content = path.read_text()
        assert "Implement auth module" in content
        assert "JWT needs refresh tokens" in content
        assert "Implemented login endpoint" in content
        assert "Add rate limiting" in content


def test_write_tasks():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        (project_root / ".openclaw_memory").mkdir(parents=True)

        tasks = [
            {"title": "Implement auth", "status": "done"},
            {"title": "Add tests", "status": "pending", "next_step": "Write unit tests"},
            {"title": "Deploy", "status": "pending", "related_files": ["deploy.yml"]},
        ]

        path = write_tasks(project_root, tasks)
        assert path.exists()

        content = path.read_text()
        assert "[x] Implement auth" in content
        assert "[ ] Add tests" in content
        assert "Write unit tests" in content
        assert "deploy.yml" in content
