"""test_engine.py — SearchEngine のユニットテスト"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from cyclegen.models import Coordinates, Memory, ScoringWeights
from cyclegen.search.context_affinity import ContextAffinityResolver
from cyclegen.search.engine import SearchEngine


def _make_memory(
    id: str = "mem_001",
    content: str = "テスト記憶",
    layer: int = 3,
    priority: float = 0.5,
    context: str = "implementation",
    access_count: int = 0,
    archived: bool = False,
    pinned: bool = False,
) -> Memory:
    return Memory(
        id=id,
        content=content,
        coordinates=Coordinates(layer=layer, priority=priority, context=context),
        access_count=access_count,
        archived=archived,
        pinned=pinned,
    )


@pytest.fixture
def engine() -> SearchEngine:
    return SearchEngine()


@pytest.fixture
def memories() -> list[Memory]:
    return [
        _make_memory("m1", "Pythonでデータモデルを実装した", 2, 0.7, "implementation", 5),
        _make_memory("m2", "アーキテクチャの設計方針を戦略的に決定", 4, 0.9, "planning"),
        _make_memory("m3", "バグ修正: SQLiteのインデックス問題", 1, 0.5, "debugging", 2),
        _make_memory("m4", "Pythonのベストプラクティスまとめ", 3, 0.8, "learning"),
        _make_memory("m5", "アーカイブされた古い記憶", 3, 0.3, "implementation", archived=True),
        _make_memory("m6", "低優先度のメモ", 2, 0.1, "implementation"),
        _make_memory("m7", "Python実装パターンのコード手順", 2, 0.6, "implementation", 3),
    ]


class TestFilterCandidates:
    def test_excludes_archived(self, engine, memories):
        result = engine._filter_candidates(memories, None, None, 0.0)
        ids = [m.id for m in result]
        assert "m5" not in ids

    def test_filter_by_context(self, engine, memories):
        result = engine._filter_candidates(memories, "planning", None, 0.0)
        assert len(result) == 1
        assert result[0].id == "m2"

    def test_filter_by_layer(self, engine, memories):
        result = engine._filter_candidates(memories, None, [2], 0.0)
        ids = [m.id for m in result]
        assert "m1" in ids
        assert "m7" in ids
        assert "m2" not in ids

    def test_filter_by_priority_threshold(self, engine, memories):
        result = engine._filter_candidates(memories, None, None, 0.6)
        assert all(m.coordinates.priority >= 0.6 for m in result)

    def test_combined_filters(self, engine, memories):
        result = engine._filter_candidates(memories, "implementation", [2], 0.5)
        assert len(result) == 2
        ids = [m.id for m in result]
        assert "m1" in ids
        assert "m7" in ids


class TestScore:
    def test_keyword_match(self, engine):
        m = _make_memory(content="Pythonでモデルを実装した")
        keywords = ["python"]
        score, reason = engine._score("Python", keywords, m)
        assert score > 0
        assert "キーワード" in reason

    def test_exact_match_bonus(self, engine):
        m = _make_memory(content="Pythonでデータモデルを実装した")
        keywords = ["python"]
        score_partial, _ = engine._score("Python", keywords, m)
        score_exact, reason = engine._score("Pythonでデータモデルを実装した", ["python", "データモデル", "実装"], m)
        assert score_exact > score_partial
        assert "完全一致" in reason

    def test_priority_affects_score(self, engine):
        m_high = _make_memory(content="テスト", priority=0.9)
        m_low = _make_memory(content="テスト", priority=0.1)
        keywords = ["テスト"]
        score_high, _ = engine._score("テスト", keywords, m_high)
        score_low, _ = engine._score("テスト", keywords, m_low)
        assert score_high > score_low

    def test_access_count_affects_score(self, engine):
        m_accessed = _make_memory(content="テスト", access_count=10)
        m_fresh = _make_memory(content="テスト", access_count=0)
        keywords = ["テスト"]
        score_a, _ = engine._score("テスト", keywords, m_accessed)
        score_f, _ = engine._score("テスト", keywords, m_fresh)
        assert score_a > score_f

    def test_no_match_returns_zero_or_low(self, engine):
        m = _make_memory(content="全く関係ないテキスト")
        keywords = ["python", "データモデル"]
        score, _ = engine._score("Pythonデータモデル", keywords, m)
        # キーワード不一致でも priority × weight 分がある可能性
        assert score < 30

    def test_score_capped_at_100(self, engine):
        m = _make_memory(content="Pythonでデータモデルを実装した", priority=1.0, access_count=100)
        keywords = engine._extract_keywords("Pythonでデータモデルを実装した")
        score, _ = engine._score("Pythonでデータモデルを実装した", keywords, m)
        assert score <= 100


class TestExtractKeywords:
    def test_basic_split(self, engine):
        kw = engine._extract_keywords("Python データモデル")
        assert "python" in kw
        assert "データモデル" in kw

    def test_removes_stop_words(self, engine):
        kw = engine._extract_keywords("これは Python の テスト です")
        assert "python" in kw
        assert "テスト" in kw
        assert "これ" not in kw
        assert "は" not in kw
        assert "の" not in kw
        assert "です" not in kw

    def test_punctuation_split(self, engine):
        kw = engine._extract_keywords("Python、データモデル。実装")
        assert "python" in kw
        assert "データモデル" in kw
        assert "実装" in kw

    def test_empty_query(self, engine):
        assert engine._extract_keywords("") == []


class TestSearchIntegration:
    def test_basic_search(self, engine, memories):
        response = engine.search("Python", memories)
        assert len(response.memories) > 0
        assert all(r.source == "personal" for r in response.memories)

    def test_search_respects_max_items(self, engine, memories):
        response = engine.search("Python 実装", memories, max_items=2)
        assert len(response.memories) <= 2

    def test_search_returns_sorted_by_score(self, engine, memories):
        response = engine.search("Python", memories)
        scores = [r.score for r in response.memories]
        assert scores == sorted(scores, reverse=True)

    def test_search_with_context_filter(self, engine, memories):
        response = engine.search("設計", memories, context="planning")
        assert all(r.memory.coordinates.context == "planning" for r in response.memories)

    def test_search_with_layer_filter(self, engine, memories):
        response = engine.search("Python", memories, layer_filter=[2])
        assert all(r.memory.coordinates.layer == 2 for r in response.memories)

    def test_search_total_candidates(self, engine, memories):
        response = engine.search("Python", memories)
        # total_candidatesはフィルタ後の候補数（archived除外）
        assert response.total_candidates >= len(response.memories)

    def test_search_time_ms(self, engine, memories):
        response = engine.search("Python", memories)
        assert response.search_time_ms >= 0

    def test_empty_query(self, engine, memories):
        response = engine.search("", memories)
        # 空クエリでもpriority等でスコアが付く記憶がある
        assert response.total_candidates > 0

    def test_no_results(self, engine):
        response = engine.search("xyz", [])
        assert response.memories == []
        assert response.total_candidates == 0

    def test_custom_weights(self):
        weights = ScoringWeights(keyword_frequency=60, exact_match=20, priority=10, access_count=10)
        engine = SearchEngine(weights)
        memories = [_make_memory(content="Pythonで開発")]
        response = engine.search("Python", memories)
        assert len(response.memories) > 0


# ================================================================
# 新モード: 2段構造（CYCLE12.7.3）
# ================================================================


def _make_mock_embedding_manager():
    """EmbeddingManagerモック。テキストのハッシュベースで再現性のあるembeddingを返す。"""
    mgr = MagicMock()

    def mock_embed(text):
        seed = sum(ord(c) for c in text) % (2**31)
        rng = np.random.RandomState(seed)
        return rng.randn(384).astype("float32").tobytes()

    mgr.embed = mock_embed
    return mgr


def _make_memory_with_embedding(
    id: str = "mem_001",
    content: str = "テスト記憶",
    layer: int = 3,
    priority: float = 0.5,
    context: str = "implementation",
    embedding_mgr=None,
    **kwargs,
) -> Memory:
    emb = embedding_mgr.embed(content) if embedding_mgr else None
    return Memory(
        id=id,
        content=content,
        coordinates=Coordinates(layer=layer, priority=priority, context=context),
        embedding=emb,
        **kwargs,
    )


SAMPLE_AFFINITY = {
    "planning": {"implementation": 0.7, "design": 0.9, "research": 0.6},
    "implementation": {"planning": 0.65, "design": 0.8, "debugging": 0.8},
}

SAMPLE_LAYER_WEIGHT = {
    "implementation": {"L1": 0.6, "L2": 1.0, "L3": 0.85, "L4": 0.6, "L5": 0.5},
    "planning": {"L1": 0.5, "L2": 0.6, "L3": 0.85, "L4": 1.0, "L5": 0.7},
}


@pytest.fixture
def affinity_resolver() -> ContextAffinityResolver:
    return ContextAffinityResolver(
        affinity_map=SAMPLE_AFFINITY,
        layer_weight_map=SAMPLE_LAYER_WEIGHT,
    )


@pytest.fixture
def emb_mgr():
    return _make_mock_embedding_manager()


class TestNewModeBasic:
    def test_is_new_mode_with_embedding(self, emb_mgr):
        engine = SearchEngine(embedding_manager=emb_mgr)
        assert engine.is_new_mode is True

    def test_is_new_mode_with_affinity(self, affinity_resolver):
        engine = SearchEngine(affinity_resolver=affinity_resolver)
        assert engine.is_new_mode is True

    def test_is_legacy_mode_default(self):
        engine = SearchEngine()
        assert engine.is_new_mode is False

    def test_new_mode_returns_results(self, emb_mgr, affinity_resolver):
        engine = SearchEngine(embedding_manager=emb_mgr, affinity_resolver=affinity_resolver)
        m = _make_memory_with_embedding(
            content="Pythonでデータモデルを実装した", priority=0.7, embedding_mgr=emb_mgr,
        )
        response = engine.search("Python データモデル", [m])
        assert len(response.memories) > 0

    def test_new_mode_score_is_text_times_3d(self, emb_mgr, affinity_resolver):
        """最終スコア = テキスト関連度 × context_affinity × layer_weight × priority"""
        engine = SearchEngine(embedding_manager=emb_mgr, affinity_resolver=affinity_resolver)
        m = _make_memory_with_embedding(
            content="SaaS認証ミドルウェアの実装手順",
            layer=2, priority=0.5, context="implementation",
            embedding_mgr=emb_mgr,
        )
        response = engine.search("SaaS認証", [m], context="implementation")
        assert len(response.memories) == 1
        result = response.memories[0]
        # context=implementation, memory=implementation → affinity=1.0
        # layer=2, context=implementation → weight=1.0
        # priority=0.5
        # 3D = 1.0 * 1.0 * 0.5 = 0.5
        # score should be text_relevance * 0.5
        assert result.score > 0


class TestContextAffinityInSearch:
    def test_same_context_higher_score(self, emb_mgr, affinity_resolver):
        """同一contextの記憶がより高いスコアを得る"""
        engine = SearchEngine(embedding_manager=emb_mgr, affinity_resolver=affinity_resolver)
        m_same = _make_memory_with_embedding(
            id="same", content="設計方針の決定", layer=4, priority=0.7,
            context="planning", embedding_mgr=emb_mgr,
        )
        m_diff = _make_memory_with_embedding(
            id="diff", content="設計方針の決定", layer=4, priority=0.7,
            context="implementation", embedding_mgr=emb_mgr,
        )
        response = engine.search("設計方針", [m_same, m_diff], context="planning")
        assert len(response.memories) == 2
        assert response.memories[0].memory.id == "same"

    def test_related_context_not_excluded(self, emb_mgr, affinity_resolver):
        """関連contextの記憶は除外されず、低い乗数で含まれる"""
        engine = SearchEngine(embedding_manager=emb_mgr, affinity_resolver=affinity_resolver)
        m = _make_memory_with_embedding(
            content="実装のパターン", layer=2, priority=0.7,
            context="implementation", embedding_mgr=emb_mgr,
        )
        # planning→implementation affinity=0.7
        response = engine.search("実装パターン", [m], context="planning")
        assert len(response.memories) == 1

    def test_no_context_filter_all_equal(self, emb_mgr, affinity_resolver):
        """context未指定時は全contextの記憶が均等（affinity=1.0）"""
        engine = SearchEngine(embedding_manager=emb_mgr, affinity_resolver=affinity_resolver)
        m1 = _make_memory_with_embedding(
            id="m1", content="テスト内容", layer=3, priority=0.7,
            context="planning", embedding_mgr=emb_mgr,
        )
        m2 = _make_memory_with_embedding(
            id="m2", content="テスト内容", layer=3, priority=0.7,
            context="implementation", embedding_mgr=emb_mgr,
        )
        response = engine.search("テスト内容", [m1, m2], context=None)
        assert len(response.memories) == 2
        # 同じ内容・同じpriority・同じlayer → 同じスコア
        assert response.memories[0].score == response.memories[1].score


class TestLayerWeightInSearch:
    def test_matching_layer_higher_score(self, emb_mgr, affinity_resolver):
        """implementation時にL2がL5より高スコア"""
        engine = SearchEngine(embedding_manager=emb_mgr, affinity_resolver=affinity_resolver)
        m_l2 = _make_memory_with_embedding(
            id="l2", content="実装の手順メモ", layer=2, priority=0.7,
            context="implementation", embedding_mgr=emb_mgr,
        )
        m_l5 = _make_memory_with_embedding(
            id="l5", content="実装の手順メモ", layer=5, priority=0.7,
            context="implementation", embedding_mgr=emb_mgr,
        )
        response = engine.search("実装 手順", [m_l2, m_l5], context="implementation")
        assert len(response.memories) == 2
        assert response.memories[0].memory.id == "l2"

    def test_planning_prefers_l4(self, emb_mgr, affinity_resolver):
        """planning時にL4がL1より高スコア"""
        engine = SearchEngine(embedding_manager=emb_mgr, affinity_resolver=affinity_resolver)
        m_l4 = _make_memory_with_embedding(
            id="l4", content="設計の方針", layer=4, priority=0.7,
            context="planning", embedding_mgr=emb_mgr,
        )
        m_l1 = _make_memory_with_embedding(
            id="l1", content="設計の方針", layer=1, priority=0.7,
            context="planning", embedding_mgr=emb_mgr,
        )
        response = engine.search("設計方針", [m_l4, m_l1], context="planning")
        assert response.memories[0].memory.id == "l4"


class TestPriorityAsMultiplier:
    def test_high_priority_boosts_score(self, emb_mgr, affinity_resolver):
        """高Priorityは乗数としてスコアを上げる"""
        engine = SearchEngine(embedding_manager=emb_mgr, affinity_resolver=affinity_resolver)
        m_high = _make_memory_with_embedding(
            id="high", content="認証設計", layer=4, priority=0.9,
            context="planning", embedding_mgr=emb_mgr,
        )
        m_low = _make_memory_with_embedding(
            id="low", content="認証設計", layer=4, priority=0.3,
            context="planning", embedding_mgr=emb_mgr,
        )
        response = engine.search("認証設計", [m_high, m_low], context="planning")
        assert response.memories[0].memory.id == "high"
        # スコア比 ≈ 0.9/0.3 = 3倍
        ratio = response.memories[0].score / response.memories[1].score
        assert ratio > 2.5

    def test_zero_priority_excluded(self, emb_mgr, affinity_resolver):
        """Priority=0の記憶は3次元評価=0で結果に含まれない"""
        engine = SearchEngine(embedding_manager=emb_mgr, affinity_resolver=affinity_resolver)
        m = _make_memory_with_embedding(
            content="dismiss済み記憶", priority=0.0,
            context="implementation", embedding_mgr=emb_mgr,
        )
        response = engine.search("dismiss済み", [m])
        assert len(response.memories) == 0


class TestReasonText:
    def test_reason_contains_3d_info(self, emb_mgr, affinity_resolver):
        """reasonに3軸の情報が含まれる"""
        engine = SearchEngine(embedding_manager=emb_mgr, affinity_resolver=affinity_resolver)
        m = _make_memory_with_embedding(
            content="テスト記憶", layer=3, priority=0.7,
            context="implementation", embedding_mgr=emb_mgr,
        )
        response = engine.search("テスト", [m], context="implementation")
        assert len(response.memories) == 1
        reason = response.memories[0].reason
        assert "C:implementation" in reason
        assert "L3" in reason
        assert "P:0.70" in reason


class TestFallback:
    def test_legacy_mode_unchanged(self):
        """embedding_manager=None, affinity_resolver=None → 旧方式"""
        engine = SearchEngine()
        m = _make_memory(content="Pythonで開発", priority=0.8, access_count=5)
        response = engine.search("Python", [m])
        assert len(response.memories) == 1
        # 旧方式: 高Priority表記
        assert "高Priority" in response.memories[0].reason

    def test_affinity_only_mode(self, affinity_resolver):
        """affinity_resolverのみ（embeddingなし）でも新モード動作"""
        engine = SearchEngine(affinity_resolver=affinity_resolver)
        m = _make_memory(content="テスト記憶", priority=0.7, context="implementation")
        response = engine.search("テスト", [m], context="implementation")
        assert len(response.memories) == 1
        assert "C:implementation" in response.memories[0].reason

    def test_embedding_only_mode(self, emb_mgr):
        """embedding_managerのみ（affinityなし）でも新モード動作"""
        engine = SearchEngine(embedding_manager=emb_mgr)
        m = _make_memory_with_embedding(
            content="テスト記憶", priority=0.7, embedding_mgr=emb_mgr,
        )
        response = engine.search("テスト", [m])
        assert len(response.memories) == 1

    def test_no_embedding_on_memory_uses_keywords_only(self, emb_mgr, affinity_resolver):
        """記憶にembeddingがない場合はキーワードのみでテキスト関連度を計算"""
        engine = SearchEngine(embedding_manager=emb_mgr, affinity_resolver=affinity_resolver)
        m = _make_memory(content="Pythonで開発", priority=0.7)
        response = engine.search("Python", [m])
        assert len(response.memories) == 1
        assert "キーワード" in response.memories[0].reason
