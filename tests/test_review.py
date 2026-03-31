"""Tests for script review manager."""

import pytest
from agent.review import ReviewManager, get_review_manager


class TestReviewManager:
    def test_submit_returns_approved(self):
        """当前实现：submit 直接返回 approved。"""
        manager = ReviewManager()
        result = manager.submit("atomic.test", "return function() end")
        assert result["status"] == "approved"
        assert result["name"] == "atomic.test"
        assert result["reviewer"] == "auto-pass"

    def test_check_status_returns_approved(self):
        """当前实现：check_status 始终返回 approved。"""
        manager = ReviewManager()
        result = manager.check_status("atomic.test")
        assert result["status"] == "approved"

    def test_get_review_manager_returns_singleton(self):
        """get_review_manager 返回全局实例。"""
        m1 = get_review_manager()
        m2 = get_review_manager()
        assert m1 is m2