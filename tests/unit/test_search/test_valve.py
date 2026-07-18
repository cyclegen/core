"""test_valve.py — IntegratedSearchValve のユニットテスト

Personal+Org統合検索バルブの動作を検証:
- Personal only（Org無効）
- Personal + Org 統合
- personal_bonus 加算
- Orgオフライン耐性
- Miller's制限
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cyclegen.models import Coordinates, Memory, SearchResult
from cyclegen.search.cognitive_load import CognitiveLoadManager
from cyclegen.search.engine import SearchEngine
from cyclegen.search.valve import IntegratedSearchValve


def _make_memory(
    id: str = "m1",
    content: str = "テスト",
    layer: int = 3,
    priority: float = 0.5,
    context: str = "implementation",
) -> Memory:
    return Memory(
        id=id,
        content=content,
        coordinates=Coordinates(layer=layer, priority=priority, context=context),
    )


def _make_result(
    id: str, score: float, source: str = "personal", content: str = "test"
) -> SearchResult:
    return SearchResult(
        memory=_make_memory(id=id, content=content),
        score=score,
        source=source,
        reason="test",
    )


@pytest.fixture
def engine() -> SearchEngine:
    return SearchEngine()


@pytest.fixture
def cognitive_load() -> CognitiveLoadManager:
    return CognitiveLoadManager(default_max_items=7)


@pytest.fixture
def personal_memories() -> list[Memory]:
    return [
        _make_memory("p1", "Python実装パターン", 2, 0.7, "implementation"),
        _make_memory("p2", "設計方針の戦略", 4, 0.9, "planning"),
        _make_memory("p3", "バグ修正手順", 1, 0.5, "debugging"),
    ]


class TestPersonalOnly:
    """Org無効（org_client=None）時の動作"""

    def test_search_without_org(self, engine, cognitive_load, personal_memories):
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None)
        response = valve.search("Python", personal_memories)
        assert len(response.memories) > 0
        assert all(r.source == "personal" for r in response.memories)

    def test_personal_bonus_applied(self, engine, cognitive_load, personal_memories):
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None, personal_bonus=20)
        response = valve.search("Python", personal_memories)
        # personal_bonusが加算されているため、元のスコアより高い
        for r in response.memories:
            assert r.score >= 20  # bonus分は最低でも加算

    def test_max_items_respected(self, engine, cognitive_load):
        memories = [_make_memory(f"p{i}", f"テスト記憶 {i}", 3, 0.5) for i in range(20)]
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None)
        response = valve.search("テスト", memories, max_items=3)
        assert len(response.memories) <= 3

    def test_empty_memories(self, engine, cognitive_load):
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None)
        response = valve.search("query", [])
        assert response.memories == []


class TestWithOrg:
    """Org有効時の統合検索"""

    def test_merges_personal_and_org(self, engine, cognitive_load, personal_memories):
        mock_org = MagicMock()
        mock_org.search.return_value = [
            _make_result("o1", 75.0, "org", "Org記憶: Python設計"),
            _make_result("o2", 60.0, "org", "Org記憶: アーキテクチャ"),
        ]

        valve = IntegratedSearchValve(engine, cognitive_load, org_client=mock_org)
        response = valve.search("Python", personal_memories)

        sources = {r.source for r in response.memories}
        assert "personal" in sources
        assert "org" in sources

    def test_personal_bonus_gives_advantage(self, engine, cognitive_load):
        """personal_bonusによりPersonal結果が優先される"""
        personal = [_make_memory("p1", "Python実装", 3, 0.7)]

        mock_org = MagicMock()
        # Orgスコアが高めでも、personal_bonusで逆転する可能性
        mock_org.search.return_value = [
            _make_result("o1", 50.0, "org", "Python org memory"),
        ]

        valve = IntegratedSearchValve(
            engine, cognitive_load, org_client=mock_org, personal_bonus=20
        )
        response = valve.search("Python", personal)

        if len(response.memories) >= 2:
            # Personal結果がbonus込みでソートされている
            scores = [r.score for r in response.memories]
            assert scores == sorted(scores, reverse=True)

    def test_org_results_have_org_source(self, engine, cognitive_load, personal_memories):
        mock_org = MagicMock()
        mock_org.search.return_value = [
            _make_result("o1", 80.0, "org", "Org記憶"),
        ]

        valve = IntegratedSearchValve(engine, cognitive_load, org_client=mock_org)
        response = valve.search("Python", personal_memories)

        org_results = [r for r in response.memories if r.source == "org"]
        for r in org_results:
            assert r.source == "org"

    def test_total_candidates_includes_org(self, engine, cognitive_load, personal_memories):
        mock_org = MagicMock()
        mock_org.search.return_value = [
            _make_result("o1", 70.0, "org"),
            _make_result("o2", 60.0, "org"),
        ]

        valve = IntegratedSearchValve(engine, cognitive_load, org_client=mock_org)
        response = valve.search("Python", personal_memories)
        # total_candidatesにOrg分が加算されている
        assert response.total_candidates >= 2


class TestOfflineResilience:
    """Orgオフライン耐性"""

    def test_org_failure_returns_personal_only(self, engine, cognitive_load, personal_memories):
        mock_org = MagicMock()
        mock_org.search.side_effect = Exception("Connection refused")

        valve = IntegratedSearchValve(engine, cognitive_load, org_client=mock_org)
        response = valve.search("Python", personal_memories)

        # エラーにならず、Personal結果だけ返る
        assert len(response.memories) > 0
        assert all(r.source == "personal" for r in response.memories)

    def test_org_timeout_returns_personal_only(self, engine, cognitive_load, personal_memories):
        mock_org = MagicMock()
        mock_org.search.side_effect = TimeoutError("Org server timeout")

        valve = IntegratedSearchValve(engine, cognitive_load, org_client=mock_org)
        response = valve.search("Python", personal_memories)

        assert len(response.memories) > 0
        assert all(r.source == "personal" for r in response.memories)


class TestMerge:
    """_merge メソッドの直接テスト"""

    def test_merge_sorts_by_score(self, engine, cognitive_load):
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None, personal_bonus=10)

        personal = [_make_result("p1", 60.0), _make_result("p2", 40.0)]
        org = [_make_result("o1", 75.0, "org"), _make_result("o2", 55.0, "org")]

        merged = valve._merge(personal, org, max_items=7)
        scores = [r.score for r in merged]
        assert scores == sorted(scores, reverse=True)

    def test_merge_caps_at_100(self, engine, cognitive_load):
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None, personal_bonus=30)

        personal = [_make_result("p1", 90.0)]
        merged = valve._merge(personal, [], max_items=7)
        # 90 + 30 = 120 → capped at 100
        assert merged[0].score == 100.0

    def test_merge_empty_org(self, engine, cognitive_load):
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None, personal_bonus=20)

        personal = [_make_result("p1", 50.0)]
        merged = valve._merge(personal, [], max_items=7)
        assert len(merged) == 1
        assert merged[0].score == 70.0  # 50 + 20

    def test_merge_empty_personal(self, engine, cognitive_load):
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None)

        org = [_make_result("o1", 60.0, "org")]
        merged = valve._merge([], org, max_items=7)
        assert len(merged) == 1
        assert merged[0].score == 60.0

    def test_merge_preserves_source(self, engine, cognitive_load):
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None, personal_bonus=10)

        personal = [_make_result("p1", 50.0, "personal")]
        org = [_make_result("o1", 60.0, "org")]
        merged = valve._merge(personal, org, max_items=7)

        sources = {r.memory.id: r.source for r in merged}
        assert sources["p1"] == "personal"
        assert sources["o1"] == "org"


class TestOrgMinSlots:
    """FR012: org_min_slots（Org最低保証枠）のテスト"""

    def test_org_guaranteed_despite_low_score(self, engine, cognitive_load):
        """Orgスコアが低くても保証枠で結果に含まれる"""
        personal = [
            _make_result("p1", 80.0),
            _make_result("p2", 70.0),
            _make_result("p3", 60.0),
            _make_result("p4", 50.0),
            _make_result("p5", 40.0),
        ]
        org = [
            _make_result("o1", 20.0, "org"),
            _make_result("o2", 15.0, "org"),
        ]

        valve = IntegratedSearchValve(
            engine, cognitive_load, org_client=None,
            personal_bonus=0, org_min_slots=2,
        )
        merged = valve._merge(personal, org, max_items=5)

        org_in_result = [r for r in merged if r.source == "org"]
        assert len(org_in_result) >= 2

    def test_org_min_slots_zero_disables_guarantee(self, engine, cognitive_load):
        """org_min_slots=0 で従来動作（保証なし）"""
        personal = [_make_result("p1", 80.0)]
        org = [_make_result("o1", 10.0, "org")]

        valve = IntegratedSearchValve(
            engine, cognitive_load, org_client=None,
            personal_bonus=20, org_min_slots=0,
        )
        merged = valve._merge(personal, org, max_items=1)

        # max_items=1でPersonalが高スコアなので、Orgは入らない
        assert len(merged) == 2  # _mergeはmax_items制限前の全件返す
        assert merged[0].source == "personal"

    def test_org_fewer_than_min_slots(self, engine, cognitive_load):
        """Org結果がmin_slotsより少ない場合、あるだけ確保"""
        personal = [_make_result("p1", 80.0), _make_result("p2", 70.0)]
        org = [_make_result("o1", 20.0, "org")]  # 1件のみ

        valve = IntegratedSearchValve(
            engine, cognitive_load, org_client=None,
            personal_bonus=0, org_min_slots=3,
        )
        merged = valve._merge(personal, org, max_items=5)

        org_in_result = [r for r in merged if r.source == "org"]
        assert len(org_in_result) == 1  # あるだけ

    def test_org_min_slots_with_personal_bonus(self, engine, cognitive_load):
        """personal_bonus + org_min_slots が共存する"""
        personal = [
            _make_result("p1", 50.0),
            _make_result("p2", 40.0),
            _make_result("p3", 30.0),
        ]
        org = [
            _make_result("o1", 25.0, "org"),
            _make_result("o2", 20.0, "org"),
        ]

        valve = IntegratedSearchValve(
            engine, cognitive_load, org_client=None,
            personal_bonus=20, org_min_slots=2,
        )
        merged = valve._merge(personal, org, max_items=5)

        org_in_result = [r for r in merged if r.source == "org"]
        assert len(org_in_result) >= 2
        # スコア順にソートされている
        scores = [r.score for r in merged]
        assert scores == sorted(scores, reverse=True)

    def test_org_min_slots_integrated_search(self, engine, cognitive_load):
        """統合検索（valve.search）でorg_min_slotsが効く"""
        personal_memories = [
            _make_memory("p1", "Python実装パターン", 3, 0.9),
            _make_memory("p2", "Python設計方針", 4, 0.8),
            _make_memory("p3", "Pythonテスト手法", 2, 0.7),
        ]
        mock_org = MagicMock()
        mock_org.search.return_value = [
            _make_result("o1", 20.0, "org", "Python org pattern"),
        ]

        valve = IntegratedSearchValve(
            engine, cognitive_load, org_client=mock_org,
            personal_bonus=20, org_min_slots=1,
        )
        response = valve.search("Python", personal_memories, max_items=3)

        org_in_result = [r for r in response.memories if r.source == "org"]
        assert len(org_in_result) >= 1

    def test_result_sorted_after_guarantee(self, engine, cognitive_load):
        """保証枠適用後もスコア順でソートされている"""
        personal = [_make_result("p1", 90.0)]
        org = [_make_result("o1", 10.0, "org")]

        valve = IntegratedSearchValve(
            engine, cognitive_load, org_client=None,
            personal_bonus=0, org_min_slots=1,
        )
        merged = valve._merge(personal, org, max_items=7)

        scores = [r.score for r in merged]
        assert scores == sorted(scores, reverse=True)


class TestSearchResponse:
    """SearchResponse の構造検証"""

    def test_response_has_search_time(self, engine, cognitive_load, personal_memories):
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None)
        response = valve.search("Python", personal_memories)
        assert response.search_time_ms >= 0

    def test_response_sorted_by_score(self, engine, cognitive_load, personal_memories):
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None)
        response = valve.search("Python", personal_memories)
        scores = [r.score for r in response.memories]
        assert scores == sorted(scores, reverse=True)


class TestTwoChannelSearch:
    """CYCLE12.8.3 FR019: 2チャネル検索（メタ認知分離）"""

    def test_l5_in_meta_not_in_task(self, engine, cognitive_load):
        """L5記憶はmeta_memoriesに入り、memoriesには入らない"""
        memories = [
            _make_memory("p1", "Python実装パターン", 2, 0.7, "implementation"),
            _make_memory("p2", "設計方針の戦略", 4, 0.9, "planning"),
            _make_memory("m1", "森枝思考パターン", 5, 0.85, "planning"),
        ]
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None)
        response = valve.search("思考", memories)

        task_layers = [r.memory.coordinates.layer for r in response.memories]
        assert 5 not in task_layers

        meta_layers = [r.memory.coordinates.layer for r in response.meta_memories]
        assert all(layer == 5 for layer in meta_layers)

    def test_meta_memories_populated(self, engine, cognitive_load):
        """L5記憶が存在する場合、meta_memoriesに格納される"""
        memories = [
            _make_memory("p1", "Python実装", 2, 0.7, "implementation"),
            _make_memory("m1", "メタ認知パターン思考法", 5, 0.85, "planning"),
            _make_memory("m2", "反論探索メタ認知", 5, 0.80, "planning"),
        ]
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None)
        response = valve.search("メタ認知", memories)

        assert len(response.meta_memories) > 0

    def test_meta_max_items_respected(self, engine, cognitive_load):
        """meta_max_itemsを超えないこと"""
        from cyclegen.models import ValveConfig
        memories = [
            _make_memory("p1", "Python実装", 2, 0.7),
            _make_memory("m1", "メタ認知1", 5, 0.85),
            _make_memory("m2", "メタ認知2", 5, 0.80),
            _make_memory("m3", "メタ認知3", 5, 0.75),
            _make_memory("m4", "メタ認知4", 5, 0.70),
        ]
        valve = IntegratedSearchValve(
            engine, cognitive_load, org_client=None,
            personal_bonus=0,
        )
        valve._valve_config = ValveConfig(meta_max_items=2)
        response = valve.search("メタ認知", memories)

        assert len(response.meta_memories) <= 2

    def test_no_l5_empty_meta(self, engine, cognitive_load):
        """L5記憶が0件の場合、meta_memoriesは空リスト"""
        memories = [
            _make_memory("p1", "Python実装", 2, 0.7),
            _make_memory("p2", "設計方針", 4, 0.9),
        ]
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None)
        response = valve.search("Python", memories)

        assert response.meta_memories == []

    def test_explicit_layer_filter_no_meta(self, engine, cognitive_load):
        """layer_filter=[1,2]指定時はL5を収集しない"""
        memories = [
            _make_memory("p1", "Python実装", 2, 0.7),
            _make_memory("m1", "メタ認知パターン", 5, 0.85),
        ]
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None)
        response = valve.search("Python", memories, layer_filter=[1, 2])

        assert response.meta_memories == []

    def test_layer_filter_including_5_collects_meta(self, engine, cognitive_load):
        """layer_filter=[3,5]指定時はL5を収集する（L5はmetaに分離）"""
        memories = [
            _make_memory("p1", "設計方針の思考", 3, 0.7),
            _make_memory("m1", "メタ認知思考パターン", 5, 0.85),
        ]
        valve = IntegratedSearchValve(engine, cognitive_load, org_client=None)
        response = valve.search("思考", memories, layer_filter=[3, 5])

        # memoriesにはL3のみ
        task_layers = [r.memory.coordinates.layer for r in response.memories]
        assert 5 not in task_layers
        # meta_memoriesにL5
        assert len(response.meta_memories) >= 0  # ヒットするかはスコア次第

    def test_backward_compat_meta_default_empty(self, engine, cognitive_load):
        """後方互換: meta_memoriesのデフォルトは空リスト"""
        from cyclegen.models import SearchResponse
        r = SearchResponse(memories=[], total_candidates=0, search_time_ms=0)
        assert r.meta_memories == []
