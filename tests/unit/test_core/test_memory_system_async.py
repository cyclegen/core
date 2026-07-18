"""test_memory_system_async.py — MemorySystem3D asyncメソッドのテスト

CYCLE7.7.3: async_store/async_search/async_update/async_delete等の
非同期メソッドが正しく動作することを検証。
永続化は実際のMdWithSQLitePersistence（同期）を使用。
"""

from __future__ import annotations

import pytest

from cyclegen.config import DEFAULT_CONTEXTS
from cyclegen.core.classifier import AutoLayerClassifier
from cyclegen.core.context import ContextSelector
from cyclegen.core.layer import LayerHierarchy
from cyclegen.core.memory_system import MemorySystem3D
from cyclegen.core.priority import PriorityManager
from cyclegen.models import ContextDefinition
from cyclegen.persistence.md_sqlite import MdWithSQLitePersistence
from cyclegen.search.engine import SearchEngine


@pytest.fixture
def system(tmp_path) -> MemorySystem3D:
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


class TestAsyncStore:
    @pytest.mark.asyncio
    async def test_async_store_basic(self, system):
        m = await system.async_store(
            content="非同期テスト記憶",
            layer=3,
            priority=0.7,
            context="implementation",
        )
        assert m.content == "非同期テスト記憶"
        assert m.coordinates.layer == 3
        assert m.coordinates.priority == 0.7

    @pytest.mark.asyncio
    async def test_async_store_auto_classify(self, system):
        m = await system.async_store(content="バグ修正の手順メモ")
        assert m.coordinates.layer is not None
        assert m.coordinates.context is not None

    @pytest.mark.asyncio
    async def test_async_store_invalid_layer(self, system):
        with pytest.raises(ValueError, match="Layer must be 1-5"):
            await system.async_store(content="test", layer=99)

    @pytest.mark.asyncio
    async def test_async_store_content_hash(self, system):
        m = await system.async_store(content="ハッシュテスト")
        assert m.content_hash != ""
        assert len(m.content_hash) == 64  # SHA-256


    @pytest.mark.asyncio
    async def test_async_store_undefined_context_falls_back(self, system):
        """CYCLE12.8.2 FR023: 未定義Contextは自動判定にフォールバック。"""
        m = await system.async_store(
            content="バグを修正する", layer=3, context="nonexistent"
        )
        assert system.context_selector.validate(m.coordinates.context)
        assert m.coordinates.context == "debugging"

    @pytest.mark.asyncio
    async def test_async_store_defined_context_unchanged(self, system):
        """CYCLE12.8.2 FR023: 定義済みContextは変更なし。"""
        m = await system.async_store(
            content="テスト", layer=3, context="planning"
        )
        assert m.coordinates.context == "planning"


class TestAsyncSearch:
    @pytest.mark.asyncio
    async def test_async_search_finds_stored(self, system):
        await system.async_store(
            content="asyncpg非同期化の設計判断",
            layer=4,
            priority=0.9,
            context="planning",
        )
        result = await system.async_search(query="asyncpg 非同期")
        assert result.total_candidates >= 1

    @pytest.mark.asyncio
    async def test_async_search_empty(self, system):
        result = await system.async_search(query="存在しないクエリxyz")
        assert result.total_candidates == 0


class TestAsyncFindByHash:
    @pytest.mark.asyncio
    async def test_find_existing_hash(self, system):
        m = await system.async_store(content="重複検知テスト")
        found = await system.async_find_by_hash(m.content_hash)
        assert found is not None
        assert found.id == m.id

    @pytest.mark.asyncio
    async def test_find_nonexistent_hash(self, system):
        found = await system.async_find_by_hash("nonexistent_hash")
        assert found is None


class TestAsyncUpdate:
    @pytest.mark.asyncio
    async def test_async_update_content(self, system):
        m = await system.async_store(content="更新前")
        updated = await system.async_update(m.id, {"content": "更新後"})
        assert updated is not None
        assert updated.content == "更新後"

    @pytest.mark.asyncio
    async def test_async_update_nonexistent(self, system):
        result = await system.async_update("nonexistent_id", {"content": "x"})
        assert result is None


class TestAsyncDelete:
    @pytest.mark.asyncio
    async def test_async_delete(self, system):
        m = await system.async_store(content="削除テスト")
        deleted = await system.async_delete(m.id)
        assert deleted is True

        loaded = await system.persistence.async_load(m.id)
        assert loaded is None

    @pytest.mark.asyncio
    async def test_async_delete_nonexistent(self, system):
        deleted = await system.async_delete("nonexistent_id")
        assert deleted is False


class TestAsyncPinArchive:
    @pytest.mark.asyncio
    async def test_async_pin(self, system):
        m = await system.async_store(content="ピンテスト")
        pinned = await system.async_pin(m.id)
        assert pinned is not None
        assert pinned.pinned is True

    @pytest.mark.asyncio
    async def test_async_archive_unarchive(self, system):
        m = await system.async_store(content="アーカイブテスト")

        archived = await system.async_archive(m.id)
        assert archived is not None
        assert archived.archived is True

        unarchived = await system.async_unarchive(m.id)
        assert unarchived is not None
        assert unarchived.archived is False


class TestAsyncBoostDismiss:
    @pytest.mark.asyncio
    async def test_async_boost(self, system):
        m = await system.async_store(content="ブーストテスト", priority=0.5)
        boosted = await system.async_boost(m.id)
        assert boosted is not None
        assert boosted.coordinates.priority > 0.5

    @pytest.mark.asyncio
    async def test_async_dismiss(self, system):
        m = await system.async_store(content="ディスミステスト", priority=0.5)
        dismissed = await system.async_dismiss(m.id)
        assert dismissed is not None
        assert dismissed.coordinates.priority < 0.5

    @pytest.mark.asyncio
    async def test_async_boost_nonexistent(self, system):
        result = await system.async_boost("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_async_dismiss_nonexistent(self, system):
        result = await system.async_dismiss("nonexistent")
        assert result is None


class TestAsyncRecordAccess:
    @pytest.mark.asyncio
    async def test_async_record_access(self, system):
        m = await system.async_store(content="アクセス記録テスト")
        assert m.access_count == 0
        assert m.coordinates.priority == 0.5

        await system.async_record_access(m.id)

        loaded = await system.persistence.async_load(m.id)
        assert loaded.access_count == 1
        assert loaded.coordinates.priority == 0.5  # CYCLE12.7.4: Priority変動なし

    @pytest.mark.asyncio
    async def test_async_record_access_priority_unchanged(self, system):
        """CYCLE12.7.4: record_accessではPriority変動なし"""
        m = await system.async_store(content="上限テスト")
        await system.persistence.async_update(m.id, {"coordinates.priority": 0.89})

        await system.async_record_access(m.id)

        loaded = await system.persistence.async_load(m.id)
        assert loaded.coordinates.priority == 0.89  # 変動なし

    @pytest.mark.asyncio
    async def test_async_record_access_nonexistent(self, system):
        # Should not raise
        await system.async_record_access("nonexistent")
