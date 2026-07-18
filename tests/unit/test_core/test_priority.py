"""test_priority.py — PriorityManager のユニットテスト

CYCLE12再定義 + CYCLE12.7.4更新: Priority = 利用実績ベース。初期値0.5固定。
record_accessではPriority変動なし。
"""

import pytest

from cyclegen.core.priority import CURRENT_SCORE_VERSION, PRIORITY_THRESHOLDS, EventCounts, PriorityManager


class TestPriorityThresholds:
    def test_threshold_ordering(self):
        assert PRIORITY_THRESHOLDS["high"] > PRIORITY_THRESHOLDS["medium"]
        assert PRIORITY_THRESHOLDS["medium"] > PRIORITY_THRESHOLDS["low"]
        assert PRIORITY_THRESHOLDS["low"] > PRIORITY_THRESHOLDS["archive"]


class TestEstimateInitial:
    def test_always_returns_default(self, priority_manager: PriorityManager):
        """CYCLE12.7: 全件0.5固定。内容によらない。"""
        assert priority_manager.estimate_initial("これで確定した") == 0.5
        assert priority_manager.estimate_initial("新しい発見があった") == 0.5
        assert priority_manager.estimate_initial("とりあえずやってみる") == 0.5
        assert priority_manager.estimate_initial("普通のテキスト") == 0.5


class TestClassify:
    def test_high(self, priority_manager: PriorityManager):
        assert priority_manager.classify(1.0) == "high"
        assert priority_manager.classify(0.8) == "high"

    def test_medium(self, priority_manager: PriorityManager):
        assert priority_manager.classify(0.79) == "medium"
        assert priority_manager.classify(0.5) == "medium"

    def test_low(self, priority_manager: PriorityManager):
        assert priority_manager.classify(0.49) == "low"
        assert priority_manager.classify(0.2) == "low"

    def test_archive(self, priority_manager: PriorityManager):
        assert priority_manager.classify(0.19) == "archive"
        assert priority_manager.classify(0.0) == "archive"


class TestAccessBoost:
    def test_access_boost_no_change(self, priority_manager: PriorityManager):
        """CYCLE12.7.4: record_accessではPriority変動なし"""
        assert priority_manager.apply_access_boost(0.3) == 0.3
        assert priority_manager.apply_access_boost(0.5) == 0.5
        assert priority_manager.apply_access_boost(0.9) == 0.9

    def test_mark_used_boost(self, priority_manager: PriorityManager):
        """実利用時の自動増進 +0.05"""
        assert priority_manager.apply_mark_used_boost(0.3) == 0.35
        assert priority_manager.apply_mark_used_boost(0.5) == 0.55

    def test_mark_used_boost_cap_at_0_9(self, priority_manager: PriorityManager):
        """自動増進は0.9で頭打ち"""
        assert priority_manager.apply_mark_used_boost(0.87) == 0.9
        assert priority_manager.apply_mark_used_boost(0.9) == 0.9


class TestBoostAndDismiss:
    def test_boost(self, priority_manager: PriorityManager):
        """CYCLE12: +0.10"""
        assert priority_manager.apply_boost(0.5) == 0.6
        assert priority_manager.apply_boost(0.0) == 0.1

    def test_boost_ceiling(self, priority_manager: PriorityManager):
        assert priority_manager.apply_boost(0.95) == 1.0
        assert priority_manager.apply_boost(1.0) == 1.0

    def test_dismiss(self, priority_manager: PriorityManager):
        assert priority_manager.apply_dismiss(0.5) == 0.4
        assert priority_manager.apply_dismiss(1.0) == 0.9

    def test_dismiss_floor(self, priority_manager: PriorityManager):
        assert priority_manager.apply_dismiss(0.05) == 0.0
        assert priority_manager.apply_dismiss(0.0) == 0.0


class TestRecalculate:
    """CYCLE12.7.4: score_version=3。accessはPriority計算から除外。"""

    def test_no_events(self, priority_manager: PriorityManager):
        """イベントなし → 初期値0.5"""
        assert priority_manager.recalculate(EventCounts()) == 0.5

    def test_access_only_no_change(self, priority_manager: PriorityManager):
        """CYCLE12.7.4: access_countはPriorityに影響しない → 0.5のまま"""
        assert priority_manager.recalculate(EventCounts(access=5)) == 0.5
        assert priority_manager.recalculate(EventCounts(access=100)) == 0.5

    def test_mark_used(self, priority_manager: PriorityManager):
        """mark_used=2 → 0.5 + 0.05*2 = 0.6"""
        assert priority_manager.recalculate(EventCounts(mark_used=2)) == 0.6

    def test_boost(self, priority_manager: PriorityManager):
        """boost=3 → 0.5 + 0.10*3 = 0.8"""
        assert priority_manager.recalculate(EventCounts(boost=3)) == 0.8

    def test_dismiss(self, priority_manager: PriorityManager):
        """dismiss=2 → 0.5 - 0.20 = 0.3"""
        assert priority_manager.recalculate(EventCounts(dismiss=2)) == 0.3

    def test_combined(self, priority_manager: PriorityManager):
        """mark_used=3, boost=1 → 0.5+0.15+0.10 = 0.75"""
        ec = EventCounts(mark_used=3, boost=1)
        assert priority_manager.recalculate(ec) == 0.75

    def test_auto_cap_at_0_9(self, priority_manager: PriorityManager):
        """自動増進は0.9上限 → mark_used=100でも0.9"""
        assert priority_manager.recalculate(EventCounts(mark_used=100)) == 0.9

    def test_boost_can_exceed_auto_cap(self, priority_manager: PriorityManager):
        """mark_used=100(→0.9) + boost=1 → 1.0"""
        assert priority_manager.recalculate(EventCounts(mark_used=100, boost=1)) == 1.0

    def test_dismiss_floor_at_0(self, priority_manager: PriorityManager):
        """dismiss=10 → 0.5 - 1.0 = 0.0（下限）"""
        assert priority_manager.recalculate(EventCounts(dismiss=10)) == 0.0

    def test_dismiss_after_boost(self, priority_manager: PriorityManager):
        """boost=2, dismiss=1 → 0.5 + 0.20 - 0.10 = 0.6"""
        assert priority_manager.recalculate(EventCounts(boost=2, dismiss=1)) == 0.6

    def test_access_ignored_in_v3(self, priority_manager: PriorityManager):
        """access=100でも0.5のまま（v3ではaccess除外）"""
        assert priority_manager.recalculate(EventCounts(access=100, dismiss=0)) == 0.5


class TestCurrentScoreVersion:
    def test_version_is_3(self):
        assert CURRENT_SCORE_VERSION == 3
