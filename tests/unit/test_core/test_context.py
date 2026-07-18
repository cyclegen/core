"""test_context.py — ContextSelector のユニットテスト"""

import pytest

from cyclegen.core.context import ContextSelector
from cyclegen.models import ContextDefinition


class TestDetect:
    def test_detect_planning(self, context_selector: ContextSelector):
        result = context_selector.detect("設計方針を計画する")
        assert result == "planning"

    def test_detect_implementation(self, context_selector: ContextSelector):
        result = context_selector.detect("コードを実装する")
        assert result == "implementation"

    def test_detect_debugging(self, context_selector: ContextSelector):
        result = context_selector.detect("バグを修正する")
        assert result == "debugging"

    def test_detect_review(self, context_selector: ContextSelector):
        result = context_selector.detect("レビューして検証する")
        assert result == "review"

    def test_detect_learning(self, context_selector: ContextSelector):
        result = context_selector.detect("調査して理解する")
        assert result == "learning"

    def test_detect_default_fallback(self, context_selector: ContextSelector):
        """マッチなしの場合は implementation にフォールバック"""
        result = context_selector.detect("aaabbbccc")
        assert result == "implementation"

    def test_detect_highest_score_wins(self, context_selector: ContextSelector):
        """複数Contextにマッチする場合、最高スコアが勝つ"""
        result = context_selector.detect("計画を設計して方針を戦略的に考える")
        assert result == "planning"  # 4 keyword matches

    def test_detect_weight_affects_score(self):
        """weightがスコアに影響する"""
        contexts = {
            "high_weight": ContextDefinition(
                weight=2.0, keywords=["test"], layer_priority=[3, 4, 2, 5, 1]
            ),
            "low_weight": ContextDefinition(
                weight=0.3, keywords=["test", "x"], layer_priority=[1, 2, 3, 4, 5]
            ),
        }
        selector = ContextSelector(contexts)
        result = selector.detect("test content")
        assert result == "high_weight"


class TestValidate:
    def test_valid_context(self, context_selector: ContextSelector):
        assert context_selector.validate("planning") is True
        assert context_selector.validate("implementation") is True

    def test_invalid_context(self, context_selector: ContextSelector):
        assert context_selector.validate("nonexistent") is False


class TestGetLayerPriority:
    def test_known_context(self, context_selector: ContextSelector):
        lp = context_selector.get_layer_priority("planning")
        assert lp == [4, 5, 3, 2, 1]

    def test_unknown_context_returns_default(self, context_selector: ContextSelector):
        lp = context_selector.get_layer_priority("nonexistent")
        assert lp == [3, 4, 2, 5, 1]


class TestListContexts:
    def test_returns_all_contexts(self, context_selector: ContextSelector):
        contexts = context_selector.list_contexts()
        assert "planning" in contexts
        assert "implementation" in contexts
        assert len(contexts) == 7
