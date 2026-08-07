"""test_idle_recall.py — CYCLE19.5（A5-2）空振り常連の検出

「返却されるのに一度も使われない記憶」を、累積で見つけて候補として提示する。
提示するだけで何も書き換えない（HITL）。
"""

from __future__ import annotations

import pytest

from cyclegen.config import DEFAULT_CONTEXTS
from cyclegen.core.classifier import AutoLayerClassifier
from cyclegen.core.context import ContextSelector
from cyclegen.core.layer import LayerHierarchy
from cyclegen.core.memory_system import MemorySystem3D
from cyclegen.core.priority import PriorityManager
from cyclegen.models import ContextDefinition, EventType
from cyclegen.monitoring.event_log import EventLogger
from cyclegen.monitoring.idle_recall import (
    MIN_SLOTS_FOR_ESTIMATE,
    MIN_THRESHOLD,
    SUGGEST_DISMISS,
    SUGGEST_SPLIT,
    IdleRecallAnalyzer,
    derive_threshold,
)
from cyclegen.persistence.md_sqlite import MdWithSQLitePersistence
from cyclegen.search.engine import SearchEngine


@pytest.fixture
def setup(tmp_path):
    persistence = MdWithSQLitePersistence(tmp_path)
    event_logger = EventLogger(persistence.conn)
    contexts = {name: ContextDefinition(**d) for name, d in DEFAULT_CONTEXTS.items()}
    system = MemorySystem3D(
        persistence=persistence,
        layer_hierarchy=LayerHierarchy(),
        priority_manager=PriorityManager(),
        context_selector=ContextSelector(contexts),
        classifier=AutoLayerClassifier(),
        search_engine=SearchEngine(),
    )
    analyzer = IdleRecallAnalyzer(event_logger, persistence)
    yield system, event_logger, analyzer
    persistence.close()


def _log_searches(event_logger: EventLogger, recalled: list[str], times: int) -> None:
    """同じ記憶が返り続けた検索をtimes回ぶん記録する。"""
    for _ in range(times):
        event_logger.log(EventType.SEARCH, details={"recalled_ids": recalled})


def _log_used(event_logger: EventLogger, memory_id: str, times: int = 1) -> None:
    for _ in range(times):
        event_logger.log(EventType.RECALL_USED, memory_id, {"session_id": "s1"})


class TestDeriveThreshold:
    """N = ceil(log(0.05) / log(1 − 捕捉率))。利用者が育つほど閾値が下がる。"""

    def test_low_capture_rate_gives_high_threshold(self):
        assert derive_threshold(0.10) == 29

    def test_threshold_falls_as_capture_rate_rises(self):
        assert derive_threshold(0.30) == 9
        assert derive_threshold(0.50) == MIN_THRESHOLD
        assert derive_threshold(0.10) > derive_threshold(0.30) > derive_threshold(0.50)

    def test_floor_at_min_threshold(self):
        """捕捉率が高くても、5回未満の空振りは判断材料にしない。"""
        assert derive_threshold(0.9) == MIN_THRESHOLD
        assert derive_threshold(1.0) == MIN_THRESHOLD

    def test_zero_capture_rate_cannot_derive(self):
        """正のシグナルが1件も無いなら、負のシグナルは使えない。"""
        assert derive_threshold(0.0) is None


class TestNoProposalWhenDataIsThin:
    """空の庭に剪定の提案をしない。"""

    def test_empty_store(self, setup):
        _, _, analyzer = setup
        report = analyzer.analyze()
        assert report.candidates == []
        assert report.threshold is None
        assert report.reason_no_threshold  # なぜ出せないかは持っている

    def test_few_slots_no_proposal(self, setup):
        """返却スロットが薄いうちは、捕捉率がいくら高くても提案しない。"""
        system, event_logger, analyzer = setup
        m = system.store("使われない記憶", layer=3)
        _log_searches(event_logger, [m.id], 10)
        _log_used(event_logger, m.id, 5)  # 捕捉率50%だが母数が薄い
        report = analyzer.analyze()
        assert report.total_slots < MIN_SLOTS_FOR_ESTIMATE
        assert report.candidates == []

    def test_no_mark_used_at_all(self, setup):
        """mark_usedが0件なら、全件が容疑者になってしまうので提案しない。"""
        system, event_logger, analyzer = setup
        m = system.store("使われない記憶", layer=3)
        _log_searches(event_logger, [m.id], 200)
        report = analyzer.analyze()
        assert report.capture_rate == 0.0
        assert report.threshold is None
        assert report.candidates == []


class TestDoesNotReadMemoriesWhenSilent:
    """言うことが無いと分かった時点で記憶を読まない。

    load_all はストアが育つほど重い（母艦2,161件で約1秒）。
    候補が出ない利用者が毎CYCLEその費用を払う理由はない。
    """

    def test_load_all_is_not_called_when_data_is_thin(self, setup, monkeypatch):
        system, event_logger, analyzer = setup
        m = system.store("使われない記憶", layer=3)
        _log_searches(event_logger, [m.id], 10)

        def _boom(*args, **kwargs):
            raise AssertionError("候補が出ないのに記憶を読み込んでいる")

        monkeypatch.setattr(analyzer.persistence, "load_all", _boom)
        assert analyzer.analyze().candidates == []

    def test_load_all_is_called_when_candidates_exist(self, setup, monkeypatch):
        system, event_logger, analyzer = setup
        idle = system.store("空振りする記憶", layer=3)
        used = system.store("使われる記憶", layer=3)
        _log_searches(event_logger, [idle.id, used.id], 60)
        _log_used(event_logger, used.id, 30)

        calls = []
        original = analyzer.persistence.load_all
        monkeypatch.setattr(
            analyzer.persistence,
            "load_all",
            lambda *a, **k: (calls.append(1), original(*a, **k))[1],
        )
        report = analyzer.analyze()
        assert len(calls) == 1
        assert [c.memory.id for c in report.candidates] == [idle.id]


class TestCandidateSelection:
    def test_detects_idle_memory(self, setup):
        system, event_logger, analyzer = setup
        idle = system.store("空振りする記憶", layer=3)
        used = system.store("使われる記憶", layer=3)
        _log_searches(event_logger, [idle.id, used.id], 60)
        _log_used(event_logger, used.id, 30)  # 捕捉率 30/120 = 25% → N=11

        report = analyzer.analyze()
        assert report.threshold == derive_threshold(report.capture_rate)
        assert [c.memory.id for c in report.candidates] == [idle.id]
        assert report.candidates[0].recall_count == 60

    def test_boosted_memory_is_not_a_candidate(self, setup):
        """boostは利用者の明示的な肯定。空振り扱いにしない。"""
        system, event_logger, analyzer = setup
        boosted = system.store("boostされた記憶", layer=3)
        used = system.store("使われる記憶", layer=3)
        _log_searches(event_logger, [boosted.id, used.id], 60)
        _log_used(event_logger, used.id, 30)
        event_logger.log(EventType.BOOST, boosted.id, {"new_priority": 0.6})

        report = analyzer.analyze()
        assert boosted.id not in [c.memory.id for c in report.candidates]

    def test_pinned_memory_is_not_a_candidate(self, setup):
        """明示の判断（pin）は、累積の統計より強い。"""
        system, event_logger, analyzer = setup
        pinned = system.store("ピン留めした記憶", layer=3)
        used = system.store("使われる記憶", layer=3)
        system.pin(pinned.id)
        _log_searches(event_logger, [pinned.id, used.id], 60)
        _log_used(event_logger, used.id, 30)

        report = analyzer.analyze()
        assert pinned.id not in [c.memory.id for c in report.candidates]

    def test_unknown_id_is_skipped_and_counted(self, setup):
        """Personal層に無い記憶（Org層など）はdismissも分割もできないので候補にしない。

        ただし黙って落とさず、件数を報告に残す。
        """
        system, event_logger, analyzer = setup
        used = system.store("使われる記憶", layer=3)
        _log_searches(event_logger, ["mem_20260101_000000_deadbeef", used.id], 60)
        _log_used(event_logger, used.id, 30)

        report = analyzer.analyze()
        assert report.candidates == []
        assert report.skipped_unknown == 1

    def test_sorted_by_recall_count(self, setup):
        system, event_logger, analyzer = setup
        a = system.store("よく出てくる記憶", layer=3)
        b = system.store("たまに出てくる記憶", layer=3)
        used = system.store("使われる記憶", layer=3)
        _log_searches(event_logger, [a.id, b.id, used.id], 40)
        _log_searches(event_logger, [a.id, used.id], 40)
        _log_used(event_logger, used.id, 40)

        report = analyzer.analyze()
        ids = [c.memory.id for c in report.candidates]
        assert ids == [a.id, b.id]

    def test_occupied_slots(self, setup):
        system, event_logger, analyzer = setup
        idle = system.store("空振りする記憶", layer=3)
        used = system.store("使われる記憶", layer=3)
        _log_searches(event_logger, [idle.id, used.id], 60)
        _log_used(event_logger, used.id, 30)

        report = analyzer.analyze()
        assert report.occupied_slots == 60
        assert report.occupied_ratio == pytest.approx(0.5)

    def test_threshold_override_is_honored(self, setup):
        """検証・再現のため閾値を明示できる（母艦での受入確認に使う）。"""
        system, event_logger, analyzer = setup
        idle = system.store("空振りする記憶", layer=3)
        _log_searches(event_logger, [idle.id], 200)

        # 捕捉率0でも、明示すれば閾値は使われる
        report = analyzer.analyze(threshold_override=30)
        assert report.threshold == 30
        assert [c.memory.id for c in report.candidates] == [idle.id]


class TestSuggestion:
    """dismiss一択にしない。長文は「内容が悪い」のではなく「何にでも当たる」可能性がある。"""

    def test_long_content_suggests_split(self, setup):
        system, event_logger, analyzer = setup
        long_mem = system.store("長" * 500, layer=3)
        for _ in range(9):
            system.store("短い記憶", layer=3)
        used = system.store("使われる記憶", layer=3)
        _log_searches(event_logger, [long_mem.id, used.id], 60)
        _log_used(event_logger, used.id, 30)

        report = analyzer.analyze()
        assert report.candidates[0].suggestion == SUGGEST_SPLIT

    def test_short_content_suggests_dismiss(self, setup):
        system, event_logger, analyzer = setup
        short_mem = system.store("短い記憶", layer=3)
        for _ in range(9):
            system.store("これはコーパス中央値を作るための記憶" * 5, layer=3)
        used = system.store("使われる記憶", layer=3)
        _log_searches(event_logger, [short_mem.id, used.id], 60)
        _log_used(event_logger, used.id, 30)

        report = analyzer.analyze()
        assert report.candidates[0].suggestion == SUGGEST_DISMISS


class TestReadOnly:
    """受入条件3: 提示だけで何も書き換えない。"""

    def test_analyze_does_not_modify_memories(self, setup):
        system, event_logger, analyzer = setup
        idle = system.store("空振りする記憶", layer=3)
        used = system.store("使われる記憶", layer=3)
        _log_searches(event_logger, [idle.id, used.id], 60)
        _log_used(event_logger, used.id, 30)

        before = system.persistence.load(idle.id)
        analyzer.analyze()
        after = system.persistence.load(idle.id)

        assert after.coordinates.priority == before.coordinates.priority
        assert after.tags == before.tags
        assert after.archived == before.archived
        assert after.access_count == before.access_count

    @pytest.mark.asyncio
    async def test_async_matches_sync(self, setup):
        system, event_logger, analyzer = setup
        idle = system.store("空振りする記憶", layer=3)
        used = system.store("使われる記憶", layer=3)
        _log_searches(event_logger, [idle.id, used.id], 60)
        _log_used(event_logger, used.id, 30)

        sync_report = analyzer.analyze()
        async_report = await analyzer.async_analyze()
        assert [c.memory.id for c in sync_report.candidates] == [
            c.memory.id for c in async_report.candidates
        ]
        assert sync_report.threshold == async_report.threshold
