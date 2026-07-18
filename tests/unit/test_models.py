"""test_models.py — データモデルのユニットテスト"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from cyclegen.models import (
    Coordinates,
    CycleGenConfig,
    DiagnosticsReport,
    EventLogEntry,
    EventType,
    LayerKey,
    Memory,
    OrgMemory,
    PromotionCriteria,
    PromotionLog,
    PromotionReason,
    PromotionRequest,
    PrecisionStats,
    PromotionStats,
    SearchResponse,
    SearchResult,
    SearchStats,
    ScoringWeights,
    StorageTier,
    ContextDefinition,
)


class TestLayerKey:
    def test_values(self):
        assert LayerKey.METACOGNITION == "metacognition"
        assert LayerKey.STRATEGY == "strategy"
        assert LayerKey.EXPERTISE == "expertise"
        assert LayerKey.IMPLEMENTATION == "implementation"
        assert LayerKey.FOUNDATION == "foundation"

    def test_count(self):
        assert len(LayerKey) == 5


class TestStorageTier:
    def test_values(self):
        assert StorageTier.HOT == "hot"
        assert StorageTier.WARM == "warm"
        assert StorageTier.COLD == "cold"


class TestCoordinates:
    def test_valid(self):
        c = Coordinates(layer=3, priority=0.7, context="planning")
        assert c.layer == 3
        assert c.priority == 0.7
        assert c.context == "planning"

    def test_layer_min_boundary(self):
        c = Coordinates(layer=1, priority=0.0, context="x")
        assert c.layer == 1

    def test_layer_max_boundary(self):
        c = Coordinates(layer=5, priority=1.0, context="x")
        assert c.layer == 5

    def test_layer_below_min(self):
        with pytest.raises(ValidationError):
            Coordinates(layer=0, priority=0.5, context="x")

    def test_layer_above_max(self):
        with pytest.raises(ValidationError):
            Coordinates(layer=6, priority=0.5, context="x")

    def test_priority_below_min(self):
        with pytest.raises(ValidationError):
            Coordinates(layer=3, priority=-0.1, context="x")

    def test_priority_above_max(self):
        with pytest.raises(ValidationError):
            Coordinates(layer=3, priority=1.1, context="x")


class TestMemory:
    def test_defaults(self):
        m = Memory(
            content="test",
            coordinates=Coordinates(layer=3, priority=0.5, context="impl"),
        )
        assert m.id.startswith("mem_")
        assert m.tags == []
        assert m.owner_id == ""
        assert m.pinned is False
        assert m.archived is False
        assert m.access_count == 0
        assert m.version == 1
        assert isinstance(m.created_at, datetime)

    def test_with_all_fields(self):
        m = Memory(
            id="mem_custom",
            content="full test",
            coordinates=Coordinates(layer=5, priority=0.9, context="planning"),
            tags=["a", "b"],
            owner_id="user1",
            pinned=True,
            archived=False,
            access_count=5,
            version=3,
        )
        assert m.id == "mem_custom"
        assert m.tags == ["a", "b"]
        assert m.pinned is True


class TestOrgMemory:
    def test_inherits_memory(self):
        om = OrgMemory(
            content="org test",
            coordinates=Coordinates(layer=4, priority=0.8, context="review"),
            promoted_by="admin",
            promotion_reason=PromotionReason.MANUAL,
        )
        assert om.content == "org test"
        assert om.storage_tier == StorageTier.HOT
        assert om.promoted_by == "admin"
        assert om.metadata == {}

    def test_storage_tier_override(self):
        om = OrgMemory(
            content="cold",
            coordinates=Coordinates(layer=1, priority=0.1, context="ops"),
            storage_tier=StorageTier.COLD,
        )
        assert om.storage_tier == StorageTier.COLD


class TestSearchResult:
    def test_valid(self, sample_memory):
        sr = SearchResult(
            memory=sample_memory,
            score=85.5,
            source="personal",
            reason="keyword match",
        )
        assert sr.score == 85.5
        assert sr.source == "personal"

    def test_score_boundary(self, sample_memory):
        sr = SearchResult(memory=sample_memory, score=0, source="org", reason="x")
        assert sr.score == 0
        sr = SearchResult(memory=sample_memory, score=100, source="org", reason="x")
        assert sr.score == 100

    def test_score_out_of_range(self, sample_memory):
        with pytest.raises(ValidationError):
            SearchResult(memory=sample_memory, score=101, source="org", reason="x")


class TestSearchResponse:
    def test_valid(self):
        sr = SearchResponse(memories=[], total_candidates=0, search_time_ms=1.5)
        assert sr.memories == []
        assert sr.search_time_ms == 1.5


class TestPromotionRequest:
    def test_valid(self):
        pr = PromotionRequest(
            memory_id="mem_001",
            content="promoted content",
            coordinates=Coordinates(layer=4, priority=0.8, context="planning"),
            owner_id="user1",
            reason=PromotionReason.CYCLE_COMPLETE,
        )
        assert pr.reason == PromotionReason.CYCLE_COMPLETE


class TestPromotionLog:
    def test_auto_id(self):
        pl = PromotionLog(
            memory_id="mem_001",
            promoted_by="admin",
            reason=PromotionReason.MANUAL,
        )
        assert len(pl.id) == 32  # uuid hex


class TestEventLogEntry:
    def test_valid(self):
        e = EventLogEntry(event_type=EventType.STORE, memory_id="mem_001")
        assert e.event_type == EventType.STORE
        assert isinstance(e.timestamp, datetime)

    def test_all_event_types(self):
        assert len(EventType) == 13  # CYCLE10.2: +PROMOTION_SUGGESTED, +PROMOTION_REJECTED


class TestDiagnosticsReport:
    def test_valid(self):
        report = DiagnosticsReport(
            total_memories=50,
            layer_distribution={1: 5, 2: 10, 3: 20, 4: 10, 5: 5},
            priority_distribution={"high": 8, "medium": 15, "low": 20, "archive": 7},
            context_distribution={"planning": 5, "implementation": 30},
            pinned_count=3,
            archived_count=7,
            search_stats=SearchStats(
                total_searches=100,
                avg_score=65.5,
                boost_count=20,
                dismiss_count=5,
                boost_rate=0.8,
            ),
            promotion_stats=PromotionStats(
                total_promotions=10,
                avg_post_promotion_access=4.5,
            ),
            precision_stats=PrecisionStats(
                total_recalled=50,
                used_count=30,
                precision_rate=0.6,
            ),
        )
        assert report.total_memories == 50
        assert report.search_stats.boost_rate == 0.8
        assert report.precision_stats.precision_rate == 0.6


class TestCycleGenConfig:
    def test_defaults(self):
        c = CycleGenConfig()
        assert c.home == "~/.cyclegen"
        assert c.personal_db == "index.db"
        assert c.org_server_enabled is False
        assert c.default_max_items == 7
        assert c.personal_bonus == 20

    def test_scoring_weights_default(self):
        c = CycleGenConfig()
        w = c.scoring_weights
        assert w.keyword_frequency + w.exact_match + w.priority + w.access_count == 100


class TestPromotionCriteria:
    """CYCLE7.3.2: 昇格基準の判定ロジックテスト"""

    def _make_memory(self, **kwargs) -> Memory:
        layer = kwargs.pop("layer", 3)
        priority = kwargs.pop("priority", 0.7)
        context = kwargs.pop("context", "planning")
        defaults = dict(
            content="test",
            coordinates=Coordinates(layer=layer, priority=priority, context=context),
            tags=[],
            access_count=2,
            pinned=False,
        )
        defaults.update(kwargs)
        return Memory(**defaults)

    def test_defaults(self):
        c = PromotionCriteria()
        assert c.priority_min == 0.7
        assert c.layer_min == 3
        assert c.access_count_min == 2
        assert c.pinned_auto_promote is True
        assert c.exclude_tag_prefixes == ["import:"]

    def test_promotable_meets_all_criteria(self):
        c = PromotionCriteria()
        m = self._make_memory(layer=4, priority=0.9, access_count=3)
        assert c.is_promotable(m) is True

    def test_not_promotable_low_priority(self):
        c = PromotionCriteria()
        m = self._make_memory(priority=0.5, access_count=5)
        assert c.is_promotable(m) is False

    def test_not_promotable_low_layer(self):
        c = PromotionCriteria()
        m = self._make_memory(layer=2, priority=0.9, access_count=5)
        assert c.is_promotable(m) is False

    def test_not_promotable_low_access_count(self):
        c = PromotionCriteria()
        m = self._make_memory(layer=4, priority=0.9, access_count=1)
        assert c.is_promotable(m) is False

    def test_pinned_auto_promote(self):
        """pinned=trueなら他の基準を満たさなくても昇格"""
        c = PromotionCriteria()
        m = self._make_memory(layer=1, priority=0.1, access_count=0, pinned=True)
        assert c.is_promotable(m) is True

    def test_pinned_auto_promote_disabled(self):
        c = PromotionCriteria(pinned_auto_promote=False)
        m = self._make_memory(layer=1, priority=0.1, access_count=0, pinned=True)
        assert c.is_promotable(m) is False

    def test_exclude_import_tag(self):
        """import:*タグがあると昇格しない"""
        c = PromotionCriteria()
        m = self._make_memory(
            layer=5, priority=1.0, access_count=10,
            tags=["import:ref"],
        )
        assert c.is_promotable(m) is False

    def test_exclude_import_tag_pinned(self):
        """import:*タグはpinnedよりも優先（昇格しない）"""
        c = PromotionCriteria()
        m = self._make_memory(
            layer=5, priority=1.0, access_count=10,
            tags=["import:ref"], pinned=True,
        )
        assert c.is_promotable(m) is False

    def test_boundary_exact_threshold(self):
        """閾値ちょうどは昇格する"""
        c = PromotionCriteria()
        m = self._make_memory(layer=3, priority=0.7, access_count=2)
        assert c.is_promotable(m) is True

    def test_custom_criteria(self):
        c = PromotionCriteria(priority_min=0.5, layer_min=2, access_count_min=1)
        m = self._make_memory(layer=2, priority=0.5, access_count=1)
        assert c.is_promotable(m) is True


class TestContextDefinition:
    def test_valid(self):
        cd = ContextDefinition(
            weight=1.0,
            keywords=["計画", "plan"],
            layer_priority=[4, 5, 3, 2, 1],
        )
        assert cd.weight == 1.0
        assert len(cd.keywords) == 2
        assert cd.layer_priority[0] == 4
