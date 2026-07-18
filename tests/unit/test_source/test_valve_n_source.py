"""test_valve_n_source.py — IntegratedSearchValve N-Sourceモードのテスト

CYCLE7.7.2: from_sources()構築によるN-Source検索の動作を検証:
- 単一ソース検索
- 複数ソース統合検索
- local_bonus加算
- source_min_slots保証
- オフライン耐性
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cyclegen.models import (
    Coordinates,
    Memory,
    SearchResponse,
    SearchResult,
    ValveConfig,
)
from cyclegen.search.cognitive_load import CognitiveLoadManager
from cyclegen.search.engine import SearchEngine
from cyclegen.search.valve import IntegratedSearchValve
from cyclegen.source.memory_source import MemorySource


def _make_memory(id: str = "m1", content: str = "テスト") -> Memory:
    return Memory(
        id=id, content=content,
        coordinates=Coordinates(layer=3, priority=0.5, context="implementation"),
    )


def _make_result(id: str, score: float, source: str = "personal") -> SearchResult:
    return SearchResult(
        memory=_make_memory(id=id), score=score, source=source, reason="test",
    )


def _make_local_source(name: str = "personal", memories: list[Memory] | None = None) -> MemorySource:
    """ローカルソースのモックを作成"""
    backend = MagicMock()
    backend.load_all.return_value = memories or []
    engine = MagicMock(spec=SearchEngine)
    return MemorySource(
        name=name, backend=backend, search_engine=engine,
        source_label=name, is_local=True,
    )


def _make_cloud_source(name: str = "org", results: list[SearchResult] | None = None) -> MemorySource:
    """cloudソースのモックを作成"""
    client = MagicMock()
    client.search.return_value = results or []
    return MemorySource(
        name=name, client=client, source_label=name, is_local=False,
    )


class TestNSourceSingleSource:
    """N-Source: 単一ソース"""

    def test_single_local_source(self):
        memories = [_make_memory("p1", "Python")]
        source = _make_local_source(memories=memories)
        source.search_engine.search.return_value = SearchResponse(
            memories=[_make_result("p1", 60.0)],
            total_candidates=1, search_time_ms=1.0,
        )

        valve = IntegratedSearchValve.from_sources(
            sources=[source],
            cognitive_load=CognitiveLoadManager(7),
            valve_config=ValveConfig(local_bonus=20),
        )
        response = valve.search("Python", [])
        assert len(response.memories) == 1
        assert response.memories[0].source == "personal"
        assert response.memories[0].score == 80.0  # 60 + 20 bonus

    def test_single_cloud_source(self):
        results = [_make_result("o1", 70.0, "org")]
        source = _make_cloud_source(results=results)

        valve = IntegratedSearchValve.from_sources(
            sources=[source],
            cognitive_load=CognitiveLoadManager(7),
            valve_config=ValveConfig(local_bonus=20),
        )
        response = valve.search("query", [])
        assert len(response.memories) == 1
        assert response.memories[0].source == "org"
        assert response.memories[0].score == 70.0  # bonusなし


class TestNSourceMultiSource:
    """N-Source: 複数ソース統合"""

    def test_two_sources_merged(self):
        local = _make_local_source(memories=[_make_memory("p1")])
        local.search_engine.search.return_value = SearchResponse(
            memories=[_make_result("p1", 50.0)],
            total_candidates=1, search_time_ms=1.0,
        )
        cloud = _make_cloud_source(results=[_make_result("o1", 60.0, "org")])

        valve = IntegratedSearchValve.from_sources(
            sources=[local, cloud],
            cognitive_load=CognitiveLoadManager(7),
            valve_config=ValveConfig(local_bonus=20, source_min_slots={}),
        )
        response = valve.search("query", [])

        assert len(response.memories) == 2
        sources = {r.source for r in response.memories}
        assert "personal" in sources
        assert "org" in sources
        # ソート確認: personal=70(50+20), org=60
        assert response.memories[0].score == 70.0
        assert response.memories[1].score == 60.0

    def test_three_sources(self):
        """3ソース（personal + team + org）"""
        personal = _make_local_source("personal", [_make_memory("p1")])
        personal.search_engine.search.return_value = SearchResponse(
            memories=[_make_result("p1", 50.0)],
            total_candidates=1, search_time_ms=1.0,
        )
        team = _make_cloud_source("team", [_make_result("t1", 40.0, "team")])
        org = _make_cloud_source("org", [_make_result("o1", 30.0, "org")])

        valve = IntegratedSearchValve.from_sources(
            sources=[personal, team, org],
            cognitive_load=CognitiveLoadManager(7),
            valve_config=ValveConfig(local_bonus=10, source_min_slots={}),
        )
        response = valve.search("query", [])

        assert len(response.memories) == 3
        # personal=60(50+10), team=40, org=30
        scores = [r.score for r in response.memories]
        assert scores == sorted(scores, reverse=True)


class TestNSourceLocalBonus:
    """N-Source: local_bonus"""

    def test_local_bonus_applied_to_local_only(self):
        local = _make_local_source(memories=[_make_memory("p1")])
        local.search_engine.search.return_value = SearchResponse(
            memories=[_make_result("p1", 50.0)],
            total_candidates=1, search_time_ms=1.0,
        )
        cloud = _make_cloud_source(results=[_make_result("o1", 50.0, "org")])

        valve = IntegratedSearchValve.from_sources(
            sources=[local, cloud],
            cognitive_load=CognitiveLoadManager(7),
            valve_config=ValveConfig(local_bonus=20, source_min_slots={}),
        )
        response = valve.search("query", [])

        personal_r = [r for r in response.memories if r.source == "personal"][0]
        org_r = [r for r in response.memories if r.source == "org"][0]
        assert personal_r.score == 70.0  # 50 + 20
        assert org_r.score == 50.0       # bonusなし

    def test_local_bonus_capped_at_100(self):
        local = _make_local_source(memories=[_make_memory("p1")])
        local.search_engine.search.return_value = SearchResponse(
            memories=[_make_result("p1", 90.0)],
            total_candidates=1, search_time_ms=1.0,
        )

        valve = IntegratedSearchValve.from_sources(
            sources=[local],
            cognitive_load=CognitiveLoadManager(7),
            valve_config=ValveConfig(local_bonus=30),
        )
        response = valve.search("query", [])
        assert response.memories[0].score == 100.0


class TestNSourceMinSlots:
    """N-Source: source_min_slots"""

    def test_org_min_slots_guarantee(self):
        """Orgスコアが低くても保証枠で含まれる"""
        local = _make_local_source(memories=[_make_memory("p1")])
        local.search_engine.search.return_value = SearchResponse(
            memories=[
                _make_result("p1", 80.0),
                _make_result("p2", 70.0),
                _make_result("p3", 60.0),
            ],
            total_candidates=3, search_time_ms=1.0,
        )
        cloud = _make_cloud_source(results=[
            _make_result("o1", 10.0, "org"),
            _make_result("o2", 5.0, "org"),
        ])

        valve = IntegratedSearchValve.from_sources(
            sources=[local, cloud],
            cognitive_load=CognitiveLoadManager(7),
            valve_config=ValveConfig(local_bonus=0, source_min_slots={"org": 2}),
        )
        response = valve.search("query", [], max_items=5)

        org_results = [r for r in response.memories if r.source == "org"]
        assert len(org_results) >= 2

    def test_multi_source_min_slots(self):
        """複数ソースにmin_slots設定"""
        local = _make_local_source(memories=[_make_memory("p1")])
        local.search_engine.search.return_value = SearchResponse(
            memories=[_make_result("p1", 90.0), _make_result("p2", 85.0)],
            total_candidates=2, search_time_ms=1.0,
        )
        team = _make_cloud_source("team", [_make_result("t1", 20.0, "team")])
        org = _make_cloud_source("org", [_make_result("o1", 15.0, "org")])

        valve = IntegratedSearchValve.from_sources(
            sources=[local, team, org],
            cognitive_load=CognitiveLoadManager(7),
            valve_config=ValveConfig(
                local_bonus=0,
                source_min_slots={"team": 1, "org": 1},
            ),
        )
        response = valve.search("query", [], max_items=5)

        team_results = [r for r in response.memories if r.source == "team"]
        org_results = [r for r in response.memories if r.source == "org"]
        assert len(team_results) >= 1
        assert len(org_results) >= 1


class TestNSourceOfflineResilience:
    """N-Source: オフライン耐性"""

    def test_cloud_failure_skipped(self):
        """cloudソース障害時はスキップしてローカルのみ返す"""
        local = _make_local_source(memories=[_make_memory("p1")])
        local.search_engine.search.return_value = SearchResponse(
            memories=[_make_result("p1", 60.0)],
            total_candidates=1, search_time_ms=1.0,
        )
        cloud = _make_cloud_source()
        cloud.client.search.side_effect = Exception("Connection refused")

        valve = IntegratedSearchValve.from_sources(
            sources=[local, cloud],
            cognitive_load=CognitiveLoadManager(7),
            valve_config=ValveConfig(),
        )
        response = valve.search("query", [])

        assert len(response.memories) == 1
        assert response.memories[0].source == "personal"

    def test_all_sources_fail(self):
        """全ソース障害時は空結果"""
        cloud1 = _make_cloud_source("org")
        cloud1.client.search.side_effect = Exception("fail")
        cloud2 = _make_cloud_source("team")
        cloud2.client.search.side_effect = Exception("fail")

        valve = IntegratedSearchValve.from_sources(
            sources=[cloud1, cloud2],
            cognitive_load=CognitiveLoadManager(7),
            valve_config=ValveConfig(),
        )
        response = valve.search("query", [])
        assert response.memories == []


class TestNSourceBackwardCompat:
    """旧方式（__init__構築）との後方互換"""

    def test_legacy_still_works(self):
        """旧__init__構築でも動作する"""
        engine = SearchEngine()
        cognitive_load = CognitiveLoadManager(7)
        memories = [_make_memory("p1", "Python実装")]

        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None)
        response = valve.search("Python", memories)
        assert isinstance(response, SearchResponse)

    def test_from_sources_has_legacy_attrs(self):
        """from_sources構築でも旧属性が参照可能"""
        valve = IntegratedSearchValve.from_sources(
            sources=[],
            cognitive_load=CognitiveLoadManager(7),
            valve_config=ValveConfig(local_bonus=15, source_min_slots={"org": 3}),
        )
        assert valve.personal_bonus == 15
        assert valve.org_min_slots == 3


class TestAsyncSearch:
    """async_search（CYCLE7.7.3.1）"""

    async def test_async_single_local(self):
        """async_searchで単一ローカルソース検索"""
        memories = [_make_memory("p1", "Python")]
        source = _make_local_source(memories=memories)
        # async_load_allもモック設定
        source.backend.async_load_all = AsyncMock(return_value=memories)
        source.search_engine.search.return_value = SearchResponse(
            memories=[_make_result("p1", 60.0)],
            total_candidates=1, search_time_ms=1.0,
        )

        valve = IntegratedSearchValve.from_sources(
            sources=[source],
            cognitive_load=CognitiveLoadManager(7),
            valve_config=ValveConfig(local_bonus=20),
        )
        response = await valve.async_search(query="Python")
        assert len(response.memories) == 1
        assert response.memories[0].score == 80.0  # 60 + 20

    async def test_async_mixed_sources(self):
        """async_searchでlocal+cloud混合"""
        memories = [_make_memory("p1")]
        local = _make_local_source(memories=memories)
        local.backend.async_load_all = AsyncMock(return_value=memories)
        local.search_engine.search.return_value = SearchResponse(
            memories=[_make_result("p1", 50.0)],
            total_candidates=1, search_time_ms=1.0,
        )
        cloud = _make_cloud_source(results=[_make_result("o1", 60.0, "org")])

        valve = IntegratedSearchValve.from_sources(
            sources=[local, cloud],
            cognitive_load=CognitiveLoadManager(7),
            valve_config=ValveConfig(local_bonus=20),
        )
        response = await valve.async_search(query="query")
        assert len(response.memories) == 2

    async def test_async_legacy_fallback(self):
        """async_searchでlegacyモードフォールバック"""
        engine = SearchEngine()
        cognitive_load = CognitiveLoadManager(7)
        memories = [_make_memory("p1", "Python実装")]

        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None)
        response = await valve.async_search(query="Python", personal_memories=memories)
        assert isinstance(response, SearchResponse)

    async def test_async_offline_resilience(self):
        """async_searchでcloud障害時スキップ"""
        memories = [_make_memory("p1")]
        local = _make_local_source(memories=memories)
        local.backend.async_load_all = AsyncMock(return_value=memories)
        local.search_engine.search.return_value = SearchResponse(
            memories=[_make_result("p1", 60.0)],
            total_candidates=1, search_time_ms=1.0,
        )
        cloud = _make_cloud_source()
        cloud.client.search.side_effect = Exception("Connection refused")

        valve = IntegratedSearchValve.from_sources(
            sources=[local, cloud],
            cognitive_load=CognitiveLoadManager(7),
            valve_config=ValveConfig(),
        )
        response = await valve.async_search(query="query")
        assert len(response.memories) == 1
        assert response.memories[0].source == "personal"
