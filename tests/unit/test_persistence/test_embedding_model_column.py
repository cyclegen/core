"""test_embedding_model_column.py — embeddingの出所を記録する（CYCLE19.2 / A8）

背景:
embeddingがどのモデル・どの版で作られたかが、どこにも記録されていなかった。
そのためシステムはモデルの変更を検知できず、確かめるには全件を再生成して
照合するしか手段が無かった（母艦2,067件で実施＝利用者には現実的でない）。

この列がいちばん効くのは「値が入っているとき」ではなく「NULLのとき」である。
NULLは壊れているのではなく『出所が不明』を意味する。
不明と一致を区別できることが、この列を入れる理由そのものなので、
既存行を現在のモデル名で埋める（＝嘘の記録を作る）ことを禁じるテストを置く。
"""

from __future__ import annotations

import sqlite3

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
    """内容から決定的にbytesを作り、固定のmodel_idを名乗るスタブ。"""

    def __init__(self, model_id: str = "stub-model@fastembed0.0.0") -> None:
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def embed(self, text: str) -> bytes:
        return text.encode("utf-8")

    def embed_batch(self, texts: list[str]) -> list[bytes]:
        return [t.encode("utf-8") for t in texts]


def _build(tmp_path, embedder=None) -> MemorySystem3D:
    persistence = MdWithSQLitePersistence(tmp_path)
    contexts = {
        name: ContextDefinition(**defn) for name, defn in DEFAULT_CONTEXTS.items()
    }
    return MemorySystem3D(
        persistence=persistence,
        layer_hierarchy=LayerHierarchy(),
        priority_manager=PriorityManager(),
        context_selector=ContextSelector(contexts),
        classifier=AutoLayerClassifier(),
        search_engine=SearchEngine(),
        embedding_manager=embedder,
    )


class TestStoreRecordsProvenance:
    def test_store_records_embedding_model(self, tmp_path):
        """embeddingを作ったなら、出所も一緒に記録される。"""
        system = _build(tmp_path, StubEmbeddingManager())
        try:
            m = system.store(content="出所つき", layer=3, context="implementation")
            assert m.embedding_model == "stub-model@fastembed0.0.0"

            reloaded = system.persistence.load(m.id)
            assert reloaded.embedding_model == "stub-model@fastembed0.0.0"
        finally:
            system.persistence.close()

    def test_no_embedding_means_no_provenance(self, tmp_path):
        """embeddingが無いなら出所もNULL。片方だけ入るとNULLの意味が濁る。"""
        system = _build(tmp_path, embedder=None)
        try:
            m = system.store(content="embeddingなし", layer=3, context="implementation")
            assert m.embedding is None
            assert m.embedding_model is None

            reloaded = system.persistence.load(m.id)
            assert reloaded.embedding is None
            assert reloaded.embedding_model is None
        finally:
            system.persistence.close()

    def test_content_update_refreshes_provenance_too(self, tmp_path):
        """A7で作り直したembeddingには、作り直した側の出所が付く。

        ここを落とすと「新しいembedding × 古いモデル名」という、
        記録があるのに嘘という最悪の状態ができる。
        """
        system = _build(tmp_path, StubEmbeddingManager("old@fastembed1"))
        try:
            m = system.store(content="最初", layer=3, context="implementation")
            assert m.embedding_model == "old@fastembed1"

            # モデルが差し替わった状況を作る
            system._embedding_manager = StubEmbeddingManager("new@fastembed2")
            updated = system.update(m.id, {"content": "書き換え"})

            assert updated.embedding == "書き換え".encode("utf-8")
            assert updated.embedding_model == "new@fastembed2"
        finally:
            system.persistence.close()


class TestMigration:
    def test_existing_db_gets_column_without_inventing_values(self, tmp_path):
        """A8導入前のDBを開いても壊れず、既存行はNULLのままであること。

        これがこのCYCLEの中心。現在のモデル名で埋めると
        「記録がある」ように見え、次にモデルが変わったとき
        その行だけ検知をすり抜ける。NULLのままが正しい。
        """
        # A8導入前の状態を作る: 列を落としたDBを手で用意する
        system = _build(tmp_path, StubEmbeddingManager())
        old = system.store(content="A8より前の記憶", layer=3, context="implementation")
        old_id = old.id
        system.persistence.close()

        conn = sqlite3.connect(str(tmp_path / "index.db"))
        conn.execute("ALTER TABLE memory_index DROP COLUMN embedding_model")
        conn.commit()
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("SELECT embedding_model FROM memory_index LIMIT 1")
        conn.close()

        # 再オープン＝マイグレーションが走る
        system2 = _build(tmp_path, StubEmbeddingManager())
        try:
            reloaded = system2.persistence.load(old_id)
            assert reloaded is not None
            assert reloaded.embedding is not None, "既存のembeddingは残っていること"
            assert reloaded.embedding_model is None, (
                "既存行の出所は不明のままであること。"
                "現在のモデル名で埋めてはならない（嘘の記録になる）"
            )

            # 新規保存には出所が付く＝以後の記憶は区別できる
            new = system2.store(content="A8より後の記憶", layer=3, context="implementation")
            assert new.embedding_model == "stub-model@fastembed0.0.0"
        finally:
            system2.persistence.close()


class TestRealEmbeddingManagerModelId:
    def test_model_id_contains_model_name_and_version(self):
        """実物のmodel_idが `<model>@fastembed<version>` の形であること。

        model_nameだけでは足りない。fastembedは同じmodel_nameのまま
        プーリング方式を変えたことがあり、版を含めないと区別できない。
        """
        from cyclegen.search.embedding import EmbeddingManager

        mgr = EmbeddingManager(model_name="some/model")
        model_id = mgr.model_id

        assert model_id.startswith("some/model@fastembed")
        # 版が取れない環境でも例外にせず "unknown" を名乗る
        assert "@fastembed" in model_id
