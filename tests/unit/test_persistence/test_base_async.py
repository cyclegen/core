"""test_base_async.py — PersistenceAdapter asyncインターフェースのテスト

CYCLE7.7.3: デフォルト実装（同期版をそのまま呼ぶ）の動作を検証。
MdWithSQLitePersistenceでasyncメソッドが使えることを確認する。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cyclegen.models import Coordinates, Memory
from cyclegen.persistence.md_sqlite import MdWithSQLitePersistence


@pytest.fixture
def persistence(tmp_path) -> MdWithSQLitePersistence:
    p = MdWithSQLitePersistence(tmp_path)
    yield p
    p.close()


@pytest.fixture
def sample_memory() -> Memory:
    return Memory(
        content="テスト記憶",
        coordinates=Coordinates(layer=3, priority=0.7, context="implementation"),
        tags=["test"],
    )


class TestAsyncDefaultImpl:
    """PersistenceAdapterのasyncデフォルト実装が同期版を呼ぶことを確認。"""

    @pytest.mark.asyncio
    async def test_async_save_and_load(self, persistence, sample_memory):
        result = await persistence.async_save(sample_memory)
        assert result is True

        loaded = await persistence.async_load(sample_memory.id)
        assert loaded is not None
        assert loaded.content == "テスト記憶"

    @pytest.mark.asyncio
    async def test_async_load_all(self, persistence, sample_memory):
        await persistence.async_save(sample_memory)
        memories = await persistence.async_load_all()
        assert len(memories) >= 1
        assert any(m.id == sample_memory.id for m in memories)

    @pytest.mark.asyncio
    async def test_async_search(self, persistence, sample_memory):
        await persistence.async_save(sample_memory)
        results = await persistence.async_search(keyword="テスト")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_async_update(self, persistence, sample_memory):
        await persistence.async_save(sample_memory)
        success = await persistence.async_update(
            sample_memory.id, {"content": "更新済み"}
        )
        assert success is True

        loaded = await persistence.async_load(sample_memory.id)
        assert loaded.content == "更新済み"

    @pytest.mark.asyncio
    async def test_async_delete(self, persistence, sample_memory):
        await persistence.async_save(sample_memory)
        success = await persistence.async_delete(sample_memory.id)
        assert success is True

        loaded = await persistence.async_load(sample_memory.id)
        assert loaded is None

    @pytest.mark.asyncio
    async def test_async_count(self, persistence, sample_memory):
        count_before = await persistence.async_count()
        await persistence.async_save(sample_memory)
        count_after = await persistence.async_count()
        assert count_after == count_before + 1

    @pytest.mark.asyncio
    async def test_async_count_with_archived(self, persistence, sample_memory):
        await persistence.async_save(sample_memory)
        await persistence.async_update(sample_memory.id, {"archived": True})

        count_active = await persistence.async_count(include_archived=False)
        count_all = await persistence.async_count(include_archived=True)
        assert count_all > count_active
