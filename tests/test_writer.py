"""Tests for the smart writer: quality gate, routing, privacy."""

from openclaw_memory.privacy import PrivacyFilter
from openclaw_memory.writer import GateResult, quality_gate, route_content


class TestQualityGate:
    def test_too_short(self):
        result = quality_gate("hi")
        assert not result.passed
        assert result.reason == "too_short"

    def test_filler_chinese(self):
        result = quality_gate("我来帮你看看这个问题吧")
        assert not result.passed
        assert result.reason == "filler"

    def test_filler_english(self):
        result = quality_gate("Let me help you check this")
        assert not result.passed
        assert result.reason == "filler"

    def test_code_path(self):
        result = quality_gate("/src/components/auth/Login.tsx")
        assert not result.passed
        assert result.reason == "code_or_path"

    def test_import_statement(self):
        result = quality_gate("import React from 'react'")
        assert not result.passed
        assert result.reason == "code_or_path"

    def test_speculative_chinese(self):
        result = quality_gate("可能是 React 版本不兼容导致的问题")
        assert not result.passed
        assert result.reason == "speculative"

    def test_speculative_english(self):
        result = quality_gate("maybe the issue is with the database connection pool")
        assert not result.passed
        assert result.reason == "speculative"

    def test_valid_preference(self):
        result = quality_gate("用户偏好使用 TypeScript strict 模式进行开发")
        assert result.passed

    def test_valid_decision(self):
        result = quality_gate("决定采用微服务架构来重构支付模块")
        assert result.passed

    def test_privacy_filter(self):
        pf = PrivacyFilter(patterns=[r"sk-[a-zA-Z0-9]{20,}"], enabled=True)
        result = quality_gate("API key is sk-abcdefghij1234567890xxx", pf)
        assert not result.passed
        assert result.reason == "privacy"

    def test_privacy_disabled(self):
        pf = PrivacyFilter(patterns=[r"sk-[a-zA-Z0-9]{20,}"], enabled=False)
        result = quality_gate("API key is sk-abcdefghij1234567890xxx", pf)
        assert result.passed


class TestRouting:
    def test_preference_route(self):
        route = route_content("用户偏好使用 Vim 键位绑定进行编辑")
        assert route.target_file == "user/preferences.md"
        assert route.is_global is True
        assert route.memory_type == "preference"
        assert route.importance == 4

    def test_instruction_route(self):
        route = route_content("所有 API 必须返回 snake_case 格式数据")
        assert route.target_file == "user/instructions.md"
        assert route.is_global is True
        assert route.memory_type == "instruction"
        assert route.importance == 5

    def test_entity_route(self):
        route = route_content("张三是后端团队负责人，主要维护支付服务")
        assert route.target_file == "user/entities.md"
        assert route.is_global is True
        assert route.memory_type == "entity"

    def test_decision_route(self):
        route = route_content("决定采用 Redis 作为缓存层来提升查询性能")
        assert route.target_file == "agent/decisions.md"
        assert route.is_global is False
        assert route.memory_type == "decision"
        assert route.importance == 5

    def test_pattern_route(self):
        route = route_content("发现 N+1 查询问题的解决方案是使用 selectinload")
        assert route.target_file == "agent/patterns.md"
        assert route.is_global is False
        assert route.memory_type == "pattern"

    def test_default_journal_route(self):
        route = route_content("今天完成了用户认证模块的代码审查工作")
        assert "journal/" in route.target_file
        assert route.is_global is False
        assert route.memory_type == "event"
        assert route.importance == 1
