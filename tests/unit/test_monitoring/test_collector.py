"""test_collector.py — DiagnosticsCollector のユニットテスト"""

from __future__ import annotations

import sqlite3

import pytest

from cyclegen.config import DEFAULT_CONTEXTS
from cyclegen.core.classifier import AutoLayerClassifier
from cyclegen.core.context import ContextSelector
from cyclegen.core.layer import LayerHierarchy
from cyclegen.core.memory_system import MemorySystem3D
from cyclegen.core.priority import PriorityManager
from cyclegen.models import ContextDefinition, EventType
from cyclegen.monitoring.collector import DiagnosticsCollector
from cyclegen.monitoring.event_log import EventLogger
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
    collector = DiagnosticsCollector(event_logger, persistence)
    yield system, event_logger, collector
    persistence.close()


class TestCollect:
    def test_empty(self, setup):
        _, _, collector = setup
        report = collector.collect()
        assert report.total_memories == 0
        assert report.search_stats.total_searches == 0

    def test_with_memories(self, setup):
        system, event_logger, collector = setup
        system.store("テスト1", layer=3, priority=0.7, context="implementation")
        system.store("テスト2", layer=4, priority=0.9, context="planning")
        system.store("テスト3", layer=3, priority=0.3, context="debugging")

        report = collector.collect()
        assert report.total_memories == 3
        assert report.layer_distribution.get(3, 0) == 2
        assert report.layer_distribution.get(4, 0) == 1

    def test_priority_distribution(self, setup):
        system, _, collector = setup
        system.store("high", priority=0.9)
        system.store("medium", priority=0.6)
        system.store("low", priority=0.3)

        report = collector.collect()
        assert report.priority_distribution["high"] == 1
        assert report.priority_distribution["medium"] == 1
        assert report.priority_distribution["low"] == 1

    def test_search_stats(self, setup):
        system, event_logger, collector = setup
        event_logger.log(EventType.SEARCH, details={"query": "test", "top_score": 80})
        event_logger.log(EventType.SEARCH, details={"query": "test2", "top_score": 60})
        event_logger.log(EventType.BOOST, "m1")
        event_logger.log(EventType.DISMISS, "m2")

        report = collector.collect()
        assert report.search_stats.total_searches == 2
        assert report.search_stats.avg_score == 70.0
        assert report.search_stats.boost_count == 1
        assert report.search_stats.dismiss_count == 1
        assert report.search_stats.boost_rate == 0.5

    def test_pinned_and_archived(self, setup):
        system, _, collector = setup
        m1 = system.store("pinned", priority=0.8)
        m2 = system.store("archived", priority=0.5)
        system.pin(m1.id)
        system.archive(m2.id)

        report = collector.collect()
        assert report.pinned_count == 1
        assert report.archived_count == 1
        assert report.total_memories == 1  # active only

    def test_precision_stats_empty(self, setup):
        _, _, collector = setup
        report = collector.collect()
        assert report.precision_stats.total_recalled == 0
        assert report.precision_stats.used_count == 0
        assert report.precision_stats.precision_rate == 0.0

    def test_precision_stats_with_data(self, setup):
        system, event_logger, collector = setup
        m1 = system.store("記憶A", layer=3, priority=0.7)
        m2 = system.store("記憶B", layer=3, priority=0.6)
        # 検索でm1, m2が返された
        event_logger.log(EventType.SEARCH, details={
            "query": "test", "top_score": 80,
            "recalled_ids": [m1.id, m2.id],
        })
        # m1だけ実際に使われた
        event_logger.log(EventType.RECALL_USED, m1.id)

        report = collector.collect()
        assert report.precision_stats.total_recalled == 2
        assert report.precision_stats.used_count == 1
        assert report.precision_stats.precision_rate == 0.5

    def test_precision_stats_multiple_searches(self, setup):
        system, event_logger, collector = setup
        m1 = system.store("記憶X", layer=3, priority=0.7)
        # 2回の検索で合計3件recalled
        event_logger.log(EventType.SEARCH, details={
            "query": "q1", "top_score": 70, "recalled_ids": [m1.id],
        })
        event_logger.log(EventType.SEARCH, details={
            "query": "q2", "top_score": 60, "recalled_ids": [m1.id, "m_other"],
        })
        # 2件mark_used
        event_logger.log(EventType.RECALL_USED, m1.id)
        event_logger.log(EventType.RECALL_USED, m1.id)

        report = collector.collect()
        assert report.precision_stats.total_recalled == 3
        assert report.precision_stats.used_count == 2
        assert abs(report.precision_stats.precision_rate - 2/3) < 0.01

    def test_session_precision_basic(self, setup):
        """session_idでsearchとrecall_usedを紐付けてセッション別Precisionを算出（CYCLE13.2 FR031 P1）"""
        system, event_logger, collector = setup
        m1 = system.store("記憶A", layer=3, priority=0.7)
        m2 = system.store("記憶B", layer=3, priority=0.6)
        # session sess_1: 2件返して1件利用 → precision 0.5
        event_logger.log(EventType.SEARCH, details={
            "query": "q", "top_score": 80, "recalled_ids": [m1.id, m2.id],
            "session_id": "sess_1",
        })
        event_logger.log(EventType.RECALL_USED, m1.id, {"session_id": "sess_1"})

        report = collector.collect()
        ps = report.precision_stats
        assert ps.session_count == 1
        assert ps.session_precision["sess_1"] == 0.5
        assert ps.avg_session_precision == 0.5

    def test_session_precision_two_sessions(self, setup):
        """複数セッションの平均Precisionを算出する（CYCLE13.2 FR031 P1）"""
        system, event_logger, collector = setup
        m1 = system.store("記憶X", layer=3, priority=0.7)
        m2 = system.store("記憶Y", layer=3, priority=0.6)
        # sess_A: 1件返して1件利用 → 1.0
        event_logger.log(EventType.SEARCH, details={
            "query": "a", "top_score": 70, "recalled_ids": [m1.id],
            "session_id": "sess_A",
        })
        event_logger.log(EventType.RECALL_USED, m1.id, {"session_id": "sess_A"})
        # sess_B: 2件返して0件利用 → 0.0
        event_logger.log(EventType.SEARCH, details={
            "query": "b", "top_score": 60, "recalled_ids": [m1.id, m2.id],
            "session_id": "sess_B",
        })

        report = collector.collect()
        ps = report.precision_stats
        assert ps.session_count == 2
        assert ps.session_precision["sess_A"] == 1.0
        assert ps.session_precision["sess_B"] == 0.0
        assert ps.avg_session_precision == 0.5

    def test_session_precision_ignores_cross_session_used(self, setup):
        """別セッションで利用された記憶は当該セッションのPrecisionに加算されない（CYCLE13.2 FR031 P1）"""
        system, event_logger, collector = setup
        m1 = system.store("記憶P", layer=3, priority=0.7)
        # sess_1で返したが、利用は別セッションsess_2に記録された
        event_logger.log(EventType.SEARCH, details={
            "query": "p", "top_score": 70, "recalled_ids": [m1.id],
            "session_id": "sess_1",
        })
        event_logger.log(EventType.RECALL_USED, m1.id, {"session_id": "sess_2"})

        report = collector.collect()
        ps = report.precision_stats
        # sess_1は利用0件、sess_2は検索なし → sess_1のみカウントされ0.0
        assert ps.session_precision.get("sess_1") == 0.0
        assert "sess_2" not in ps.session_precision

    def test_session_precision_no_session_id(self, setup):
        """session_idなしの旧イベントはセッション集計に含まれない（後方互換、CYCLE13.2 FR031 P1）"""
        system, event_logger, collector = setup
        m1 = system.store("記憶Q", layer=3, priority=0.7)
        event_logger.log(EventType.SEARCH, details={
            "query": "q", "top_score": 70, "recalled_ids": [m1.id],
        })
        event_logger.log(EventType.RECALL_USED, m1.id)

        report = collector.collect()
        ps = report.precision_stats
        # 旧来の全体precisionは算出されるが、session別は0
        assert ps.precision_rate == 1.0
        assert ps.session_count == 0
        assert ps.avg_session_precision == 0.0
