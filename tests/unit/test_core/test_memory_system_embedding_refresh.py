"""test_memory_system_embedding_refresh.py — update()時のembedding再生成（CYCLE19.1 / A7）

背景:
store() はembeddingを生成するのに update() は生成しておらず、
内容を書き換えるとembeddingだけ古い内容のまま残っていた。
母艦2,067件のうち6件（0.3%）で実際にズレを確認（自己類似度 最低0.67）。

この不具合は例外を出さない。その記憶が検索で当たらなくなるだけなので、
利用者からは「壊れた」ように見えない。だからテストで固定する。
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


class StubEmbeddingManager:
    """内容から決定的にbytesを作るだけのスタブ。

    fastembedを入れずにembeddingの配線だけを検証するために使う。
    embed(x) == embed(y) ⟺ x == y が成り立てばよい。

    CYCLE19.2(A8)で `model_id` が必須になった。embeddingを作れるのに
    出所を名乗れない実装は、A8が消そうとしている状態そのものなので、
    ここでも省略しない。
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def model_id(self) -> str:
        return "stub-model@fastembed0.0.0"

    def embed(self, text: str) -> bytes:
        self.calls.append(text)
        return text.encode("utf-8")


@pytest.fixture
def embedder() -> StubEmbeddingManager:
    return StubEmbeddingManager()


@pytest.fixture
def system(tmp_path, embedder) -> MemorySystem3D:
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
        embedding_manager=embedder,
    )
    yield sys
    persistence.close()


class TestUpdateRefreshesEmbedding:
    def test_content_update_regenerates_embedding(self, system, embedder):
        """contentを書き換えたら、embeddingも新しい内容のものになる。"""
        m = system.store(content="最初の内容", layer=3, context="implementation")
        assert m.embedding == "最初の内容".encode("utf-8")

        updated = system.update(m.id, {"content": "書き換えた内容"})

        assert updated is not None
        assert updated.content == "書き換えた内容"
        # ここが A7 の本体: 古い内容のembeddingが残っていない
        assert updated.embedding == "書き換えた内容".encode("utf-8")
        assert updated.embedding != "最初の内容".encode("utf-8")

    def test_non_content_update_does_not_reembed(self, system, embedder):
        """contentを触らない更新では、embedするコストを払わない。"""
        m = system.store(content="動かさない内容", layer=3, context="implementation")
        calls_after_store = len(embedder.calls)

        updated = system.update(m.id, {"coordinates.priority": 0.7})

        assert updated is not None
        assert updated.coordinates.priority == 0.7
        assert updated.embedding == "動かさない内容".encode("utf-8")
        assert len(embedder.calls) == calls_after_store

    def test_explicit_embedding_wins(self, system, embedder):
        """呼び出し側がembeddingを明示したら、それを尊重して上書きしない。

        memory_reembed のように「内容とembeddingを揃えて渡す」経路を壊さないため。
        """
        m = system.store(content="元の内容", layer=3, context="implementation")

        updated = system.update(
            m.id, {"content": "新しい内容", "embedding": b"explicit"}
        )

        assert updated is not None
        assert updated.embedding == b"explicit"

    def test_no_embedding_manager_is_safe(self, tmp_path):
        """embedding_managerが無い構成（fastembed未インストール）でも例外にならない。"""
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
        try:
            m = sys.store(content="embeddingなし", layer=3, context="implementation")
            updated = sys.update(m.id, {"content": "書き換え"})
            assert updated is not None
            assert updated.content == "書き換え"
            assert updated.embedding is None
        finally:
            persistence.close()


class TestAsyncUpdateRefreshesEmbedding:
    @pytest.mark.asyncio
    async def test_async_content_update_regenerates_embedding(self, system):
        """非同期版でも同じこと。sync側だけ直すと片肺になる。"""
        m = await system.async_store(
            content="非同期の最初", layer=3, context="implementation"
        )

        updated = await system.async_update(m.id, {"content": "非同期の書き換え"})

        assert updated is not None
        assert updated.embedding == "非同期の書き換え".encode("utf-8")
