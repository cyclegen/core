"""test_memory_system.py — MemorySystem3D のユニットテスト

MemorySystem3Dファサードを通じて store → search → update → delete の
一連のフローが動作することを検証する。永続化は実際のMdWithSQLitePersistenceを使用。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cyclegen.config import DEFAULT_CONTEXTS, load_contexts
from cyclegen.core.classifier import AutoLayerClassifier
from cyclegen.core.context import ContextSelector
from cyclegen.core.layer import LayerHierarchy
from cyclegen.core.memory_system import MemorySystem3D
from cyclegen.core.priority import PriorityManager
from cyclegen.models import ContextDefinition, CycleGenConfig
from cyclegen.persistence.md_sqlite import MdWithSQLitePersistence
from cyclegen.search.engine import SearchEngine


@pytest.fixture
def system(tmp_path) -> MemorySystem3D:
    """全コンポーネントを統合したMemorySystem3Dを構築。"""
    persistence = MdWithSQLitePersistence(tmp_path)
    contexts = {
        name: ContextDefinition(**defn) for name, defn in DEFAULT_CONTEXTS.items()
    }
    sys = MemorySystem3D(
        persistence=persistence,
        layer_hierarchy=LayerHierarchy(),
        priority_manager=PriorityManager(),
        context_selector=ContextSelector(contexts),
        classifier=AutoLayerClassifier(),
        search_engine=SearchEngine(),
    )
    yield sys
    persistence.close()


class TestStore:
    def test_store_with_all_params(self, system):
        m = system.store(
            content="テスト記憶",
            layer=3,
            priority=0.7,
            context="implementation",
            tags=["test"],
            owner_id="user1",
        )
        assert m.id.startswith("mem_")
        assert m.content == "テスト記憶"
        assert m.coordinates.layer == 3
        assert m.coordinates.priority == 0.7
        assert m.coordinates.context == "implementation"
        assert m.tags == ["test"]

    def test_store_auto_layer(self, system):
        """layer省略時にAutoLayerClassifierが判定"""
        m = system.store(content="アーキテクチャの戦略を設計する")
        assert m.coordinates.layer == 4  # strategy

    def test_store_auto_priority(self, system):
        """priority省略時にPriorityManagerが推定"""
        m = system.store(content="これで確定した")
        assert m.coordinates.priority == 0.5  # CYCLE12.7: 全件0.5固定

    def test_store_sets_score_version_3(self, system):
        """CYCLE12.7.4: 新規store時はscore_version=3"""
        m = system.store(content="score_versionテスト")
        assert m.score_version == 3

    def test_store_auto_context(self, system):
        """context省略時にContextSelectorが検出"""
        m = system.store(content="バグを修正する")
        assert m.coordinates.context == "debugging"

    def test_store_all_auto(self, system):
        """全パラメータ省略時の自動判定"""
        m = system.store(content="普通のメモ")
        assert 1 <= m.coordinates.layer <= 5
        assert 0.0 <= m.coordinates.priority <= 1.0
        assert m.coordinates.context != ""

    def test_store_invalid_layer_raises(self, system):
        with pytest.raises(ValueError):
            system.store(content="test", layer=0)

    def test_store_persists(self, system):
        m = system.store(content="永続化テスト")
        loaded = system.persistence.load(m.id)
        assert loaded is not None
        assert loaded.content == "永続化テスト"

    def test_store_with_agent_id(self, system):
        """agent_id付きで保存・読込できる"""
        m = system.store(content="エージェントの記憶", agent_id="agent-alpha")
        assert m.agent_id == "agent-alpha"
        loaded = system.persistence.load(m.id)
        assert loaded.agent_id == "agent-alpha"

    def test_store_without_agent_id(self, system):
        """agent_id省略時はNone（後方互換）"""
        m = system.store(content="通常の記憶")
        assert m.agent_id is None

    def test_store_context_auto_detect_with_detector(self, tmp_path):
        """CYCLE12.7.8: context_detectorが設定されている場合、
        context未指定時にembedding類似度ベースで自動判定される。"""
        from unittest.mock import MagicMock

        persistence = MdWithSQLitePersistence(tmp_path)
        contexts = {
            name: ContextDefinition(**defn) for name, defn in DEFAULT_CONTEXTS.items()
        }
        mock_detector = MagicMock()
        mock_detector.detect.return_value = "research"

        sys = MemorySystem3D(
            persistence=persistence,
            layer_hierarchy=LayerHierarchy(),
            priority_manager=PriorityManager(),
            context_selector=ContextSelector(contexts),
            classifier=AutoLayerClassifier(),
            context_detector=mock_detector,
        )
        m = sys.store(content="市場調査レポート")
        assert m.coordinates.context == "research"
        mock_detector.detect.assert_called_once_with("市場調査レポート")
        persistence.close()

    def test_store_context_detector_fallback_to_keyword(self, tmp_path):
        """CYCLE12.7.8: context_detectorがNoneを返した場合、
        キーワードベースのContextSelectorにフォールバックする。"""
        from unittest.mock import MagicMock

        persistence = MdWithSQLitePersistence(tmp_path)
        contexts = {
            name: ContextDefinition(**defn) for name, defn in DEFAULT_CONTEXTS.items()
        }
        mock_detector = MagicMock()
        mock_detector.detect.return_value = None  # 判定不能

        sys = MemorySystem3D(
            persistence=persistence,
            layer_hierarchy=LayerHierarchy(),
            priority_manager=PriorityManager(),
            context_selector=ContextSelector(contexts),
            classifier=AutoLayerClassifier(),
            context_detector=mock_detector,
        )
        m = sys.store(content="バグを修正する")
        assert m.coordinates.context == "debugging"  # キーワード判定
        persistence.close()

    def test_store_context_explicit_skips_detector(self, tmp_path):
        """CYCLE12.7.8: contextが明示指定された場合、detectorは呼ばれない。"""
        from unittest.mock import MagicMock

        persistence = MdWithSQLitePersistence(tmp_path)
        contexts = {
            name: ContextDefinition(**defn) for name, defn in DEFAULT_CONTEXTS.items()
        }
        mock_detector = MagicMock()

        sys = MemorySystem3D(
            persistence=persistence,
            layer_hierarchy=LayerHierarchy(),
            priority_manager=PriorityManager(),
            context_selector=ContextSelector(contexts),
            classifier=AutoLayerClassifier(),
            context_detector=mock_detector,
        )
        m = sys.store(content="テスト", context="planning")
        assert m.coordinates.context == "planning"
        mock_detector.detect.assert_not_called()
        persistence.close()

    def test_store_without_detector_uses_keyword(self, system):
        """CYCLE12.7.8: context_detector=Noneの場合、従来通りキーワード判定。"""
        assert system._context_detector is None
        m = system.store(content="バグを修正する")
        assert m.coordinates.context == "debugging"

    def test_store_undefined_context_falls_back_to_auto(self, system):
        """CYCLE12.8.2 FR023: 未定義Contextを指定した場合、自動判定にフォールバック。"""
        m = system.store(content="バグを修正する", layer=3, context="nonexistent_context")
        # 未定義Contextは自動判定に切り替わるので、定義済みContextになる
        assert system.context_selector.validate(m.coordinates.context)
        # "バグを修正する"はキーワードでdebuggingに判定される
        assert m.coordinates.context == "debugging"

    def test_store_defined_context_unchanged(self, system):
        """CYCLE12.8.2 FR023: 定義済みContextを指定した場合は変更なし。"""
        m = system.store(content="テスト", layer=3, context="planning")
        assert m.coordinates.context == "planning"

    def test_store_undefined_context_with_detector(self, tmp_path):
        """CYCLE12.8.2 FR023: 未定義Context + detector有りの場合、detectorで判定。"""
        from unittest.mock import MagicMock

        persistence = MdWithSQLitePersistence(tmp_path)
        contexts = {
            name: ContextDefinition(**defn) for name, defn in DEFAULT_CONTEXTS.items()
        }
        mock_detector = MagicMock()
        mock_detector.detect.return_value = "research"

        sys = MemorySystem3D(
            persistence=persistence,
            layer_hierarchy=LayerHierarchy(),
            priority_manager=PriorityManager(),
            context_selector=ContextSelector(contexts),
            classifier=AutoLayerClassifier(),
            context_detector=mock_detector,
        )
        m = sys.store(content="テスト", layer=3, context="invalid_ctx")
        assert m.coordinates.context == "research"
        mock_detector.detect.assert_called_once()
        persistence.close()


class TestSearch:
    def test_search_finds_stored(self, system):
        system.store(content="Pythonでデータモデルを実装", layer=3, priority=0.7, context="implementation")
        system.store(content="設計方針の策定", layer=4, priority=0.9, context="planning")

        response = system.search("Python")
        assert len(response.memories) > 0
        contents = [r.memory.content for r in response.memories]
        assert any("Python" in c for c in contents)

    def test_search_with_context(self, system):
        system.store(content="実装のコード", layer=2, context="implementation")
        system.store(content="設計の計画", layer=4, context="planning")

        response = system.search("設計", context="planning")
        assert all(r.memory.coordinates.context == "planning" for r in response.memories)

    def test_search_with_layer_filter(self, system):
        system.store(content="Layer2の記憶", layer=2)
        system.store(content="Layer4の記憶", layer=4)

        response = system.search("記憶", layer_filter=[4])
        assert all(r.memory.coordinates.layer == 4 for r in response.memories)

    def test_search_max_items(self, system):
        for i in range(10):
            system.store(content=f"テスト記憶 {i}", layer=3, priority=0.5)

        response = system.search("テスト", max_items=3)
        assert len(response.memories) <= 3

    def test_search_empty_db(self, system):
        response = system.search("何でも")
        assert response.memories == []


class TestUpdate:
    def test_update_content(self, system):
        m = system.store(content="元の内容")
        updated = system.update(m.id, {"content": "新しい内容"})
        assert updated is not None
        assert updated.content == "新しい内容"

    def test_update_coordinates(self, system):
        m = system.store(content="test", layer=3, priority=0.5, context="implementation")
        updated = system.update(m.id, {"coordinates.layer": 4, "coordinates.priority": 0.9})
        assert updated.coordinates.layer == 4
        assert updated.coordinates.priority == 0.9

    def test_update_nonexistent(self, system):
        result = system.update("nonexistent", {"content": "x"})
        assert result is None

    def test_update_increments_version(self, system):
        m = system.store(content="v1")
        updated = system.update(m.id, {"content": "v2"})
        assert updated.version == 2


class TestDelete:
    def test_delete_existing(self, system):
        m = system.store(content="削除対象")
        assert system.delete(m.id) is True
        assert system.persistence.load(m.id) is None

    def test_delete_nonexistent(self, system):
        assert system.delete("nonexistent") is False


class TestPin:
    def test_pin(self, system):
        m = system.store(content="ピン留め対象")
        result = system.pin(m.id)
        assert result is not None
        assert result.pinned is True

    def test_pin_nonexistent(self, system):
        assert system.pin("nonexistent") is None


class TestArchive:
    def test_archive(self, system):
        m = system.store(content="アーカイブ対象")
        result = system.archive(m.id)
        assert result is not None
        assert result.archived is True

    def test_archive_excludes_from_search(self, system):
        m = system.store(content="Python記憶", layer=3, priority=0.7, context="implementation")
        system.archive(m.id)
        response = system.search("Python")
        ids = [r.memory.id for r in response.memories]
        assert m.id not in ids


class TestUnarchive:
    def test_unarchive(self, system):
        m = system.store(content="アーカイブ→復帰")
        system.archive(m.id)
        result = system.unarchive(m.id)
        assert result is not None
        assert result.archived is False

    def test_unarchive_restores_search(self, system):
        m = system.store(content="Python復帰テスト", layer=3, priority=0.7, context="implementation")
        system.archive(m.id)
        system.unarchive(m.id)
        response = system.search("Python")
        ids = [r.memory.id for r in response.memories]
        assert m.id in ids

    def test_unarchive_nonexistent(self, system):
        assert system.unarchive("nonexistent") is None


class TestBoost:
    def test_boost_increases_priority(self, system):
        m = system.store(content="boost対象")
        result = system.boost(m.id)
        assert result is not None
        assert result.coordinates.priority == 0.6  # 0.5 + 0.10

    def test_boost_increments_access_count(self, system):
        m = system.store(content="boost対象")
        result = system.boost(m.id)
        assert result.access_count == 1

    def test_boost_ceiling(self, system):
        m = system.store(content="ceiling", priority=0.95)
        result = system.boost(m.id)
        assert result.coordinates.priority == 1.0

    def test_boost_nonexistent(self, system):
        assert system.boost("nonexistent") is None


class TestDismiss:
    def test_dismiss_decreases_priority(self, system):
        m = system.store(content="dismiss対象", priority=0.5)
        result = system.dismiss(m.id)
        assert result is not None
        assert result.coordinates.priority == 0.4  # 0.5 - 0.10

    def test_dismiss_floor(self, system):
        m = system.store(content="floor", priority=0.05)
        result = system.dismiss(m.id)
        assert result.coordinates.priority == 0.0

    def test_dismiss_nonexistent(self, system):
        assert system.dismiss("nonexistent") is None


class TestRecordAccess:
    def test_record_access(self, system):
        m = system.store(content="アクセス記録対象")
        assert m.access_count == 0
        system.record_access(m.id)
        loaded = system.persistence.load(m.id)
        assert loaded.access_count == 1

    def test_record_access_nonexistent(self, system):
        # 例外を投げないことを確認
        system.record_access("nonexistent")


class TestEndToEnd:
    def test_store_search_boost_search(self, system):
        """store → search → boost → search の一連フロー"""
        m1 = system.store(content="Python実装パターン", layer=2, priority=0.5, context="implementation")
        m2 = system.store(content="Python設計方針", layer=4, priority=0.6, context="planning")

        # 初回検索
        res1 = system.search("Python")
        assert len(res1.memories) == 2

        # boost
        system.boost(m1.id)

        # boost後に再検索（m1のスコアが上がる）
        res2 = system.search("Python")
        assert len(res2.memories) == 2

    def test_store_archive_search(self, system):
        """store → archive → search で除外確認"""
        m = system.store(content="アーカイブテスト", layer=3, priority=0.7, context="implementation")
        system.archive(m.id)
        response = system.search("アーカイブ")
        ids = [r.memory.id for r in response.memories]
        assert m.id not in ids
