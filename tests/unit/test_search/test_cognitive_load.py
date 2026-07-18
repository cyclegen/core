"""test_cognitive_load.py — CognitiveLoadManager のユニットテスト"""

from __future__ import annotations

import pytest

from cyclegen.models import Coordinates, Memory, SearchResult
from cyclegen.search.cognitive_load import CognitiveLoadManager


def _make_result(score: float, id: str = "m1") -> SearchResult:
    return SearchResult(
        memory=Memory(
            id=id,
            content="test",
            coordinates=Coordinates(layer=3, priority=0.5, context="impl"),
        ),
        score=score,
        source="personal",
        reason="test",
    )


@pytest.fixture
def manager() -> CognitiveLoadManager:
    return CognitiveLoadManager()


class TestApplyLimit:
    def test_default_limit_7(self, manager):
        results = [_make_result(90 - i, f"m{i}") for i in range(10)]
        limited = manager.apply_limit(results)
        assert len(limited) == 7

    def test_custom_limit(self, manager):
        results = [_make_result(90 - i, f"m{i}") for i in range(10)]
        limited = manager.apply_limit(results, max_items=3)
        assert len(limited) == 3

    def test_fewer_than_limit(self, manager):
        results = [_make_result(90), _make_result(80, "m2")]
        limited = manager.apply_limit(results)
        assert len(limited) == 2

    def test_empty_list(self, manager):
        assert manager.apply_limit([]) == []

    def test_preserves_order(self, manager):
        results = [_make_result(90 - i, f"m{i}") for i in range(10)]
        limited = manager.apply_limit(results, max_items=5)
        assert [r.memory.id for r in limited] == ["m0", "m1", "m2", "m3", "m4"]

    def test_custom_default(self):
        manager = CognitiveLoadManager(default_max_items=5)
        results = [_make_result(90 - i, f"m{i}") for i in range(10)]
        limited = manager.apply_limit(results)
        assert len(limited) == 5
