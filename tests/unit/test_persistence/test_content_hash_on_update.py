"""test_content_hash_on_update.py — 本文を変えたら指紋も変える（CYCLE20.5 / FR061⓪）

背景:
`store` は content_hash（SHA-256）を計算していたが、`update` は本文を
差し替えるだけで指紋を計算し直していなかった。
そのため **`memory_update` を1回でも通った記憶は、その時点から指紋が古いまま**になる。
母艦の実測（CYCLE19.7）では 2,186件中19件がこの状態で、
しかもその19件は embedding のずれ12件を完全に含んでいた。

これが後から直せない理由:
FR061① は「本文と embedding のずれ」を **content_hash の照合で検知する**予定である。
検知に使う値そのものが更新のたびに古くなる実装のままリリースすると、
利用者のデータは誤検知だらけで溜まり、あとから真偽を判定する手段が無くなる。

★既存のずれは遡って直さない（FR061 受入条件0）。
推測で埋めると「記録がある」と誤認され、次に本当に壊れたとき検知できなくなる
（CYCLE19.2 A8で確立した規律）。ここでもそれを禁じるテストを置く。
"""

from __future__ import annotations

import hashlib

import pytest

from cyclegen.config import DEFAULT_CONTEXTS
from cyclegen.core.classifier import AutoLayerClassifier
from cyclegen.core.context import ContextSelector
from cyclegen.core.layer import LayerHierarchy
from cyclegen.core.memory_system import MemorySystem3D
from cyclegen.core.priority import PriorityManager
from cyclegen.models import ContextDefinition, compute_content_hash
from cyclegen.persistence.base import with_content_hash
from cyclegen.persistence.md_sqlite import MdWithSQLitePersistence
from cyclegen.search.engine import SearchEngine


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def persistence(tmp_path):
    p = MdWithSQLitePersistence(tmp_path)
    yield p
    p.close()


@pytest.fixture
def system(persistence):
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
    )


class TestComputeContentHash:
    """計算箇所は1つにする（保存と更新でずれないため）。"""

    def test_matches_sha256_of_utf8(self):
        assert compute_content_hash("あ") == sha256("あ")

    def test_store_uses_the_same_function(self, system):
        memory = system.store(content="保存時の本文", layer=3)
        assert memory.content_hash == compute_content_hash("保存時の本文")


class TestWithContentHash:
    """updates を通すヘルパ単体の振る舞い。"""

    def test_adds_hash_when_content_changes(self):
        result = with_content_hash({"content": "新しい本文"})
        assert result["content_hash"] == sha256("新しい本文")

    def test_untouched_when_content_absent(self):
        updates = {"coordinates.priority": 0.8}
        assert with_content_hash(updates) == updates

    def test_recomputed_hash_wins_over_caller_supplied(self):
        """本文が正、指紋は従。古い指紋を一緒に渡されても本文から作り直す。"""
        result = with_content_hash({"content": "新しい本文", "content_hash": "古い値"})
        assert result["content_hash"] == sha256("新しい本文")

    def test_does_not_mutate_input(self):
        updates = {"content": "新しい本文"}
        with_content_hash(updates)
        assert "content_hash" not in updates


class TestUpdateRecalculates:
    """FR061⓪の本体。"""

    def test_hash_follows_content(self, persistence, system):
        memory = system.store(content="最初の本文", layer=3)
        persistence.update(memory.id, {"content": "書き直した本文"})

        reloaded = persistence.load(memory.id)
        assert reloaded.content == "書き直した本文"
        assert reloaded.content_hash == sha256("書き直した本文")

    def test_hash_is_written_to_the_md_frontmatter(self, tmp_path, persistence, system):
        """mdが正本なので、SQLiteだけ直っていても意味がない。"""
        memory = system.store(content="最初の本文", layer=3)
        persistence.update(memory.id, {"content": "書き直した本文"})

        md_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in tmp_path.rglob(f"*{memory.id}*.md")
        )
        assert sha256("書き直した本文") in md_text

    def test_other_fields_do_not_touch_the_hash(self, persistence, system):
        memory = system.store(content="最初の本文", layer=3)
        before = persistence.load(memory.id).content_hash

        persistence.update(memory.id, {"coordinates.priority": 0.9, "pinned": True})

        assert persistence.load(memory.id).content_hash == before

    def test_memory_update_path_keeps_them_in_sync(self, persistence, system):
        """利用者が通る経路（memory_update → system.update）で確かめる。"""
        memory = system.store(content="最初の本文", layer=3)
        system.update(memory.id, {"content": "書き直した本文"})

        reloaded = persistence.load(memory.id)
        assert reloaded.content_hash == compute_content_hash(reloaded.content)

    async def test_async_update_path_keeps_them_in_sync(self, persistence, system):
        memory = system.store(content="最初の本文", layer=3)
        await system.async_update(memory.id, {"content": "非同期で書き直した本文"})

        reloaded = await persistence.async_load(memory.id)
        assert reloaded.content_hash == compute_content_hash(reloaded.content)

    def test_repeated_updates_stay_in_sync(self, persistence, system):
        memory = system.store(content="v1", layer=3)
        for text in ("v2", "v3", "v4"):
            persistence.update(memory.id, {"content": text})
            reloaded = persistence.load(memory.id)
            assert reloaded.content_hash == sha256(text)
        assert reloaded.version == 4


class TestDoesNotBackfill:
    """既存のずれは遡って直さない（FR061 受入条件0）。"""

    def test_existing_mismatch_is_left_alone(self, persistence, system):
        """本文に触らない更新は、すでにずれている指紋を直さない。

        直してしまうと「いつからずれていたか」が消え、
        FR061①（検知）が測る対象そのものが無くなる。
        """
        memory = system.store(content="本文", layer=3)
        persistence.update(memory.id, {"content_hash": "ずれたままの古い値"})
        assert persistence.load(memory.id).content_hash == "ずれたままの古い値"

        persistence.update(memory.id, {"coordinates.priority": 0.9})

        assert persistence.load(memory.id).content_hash == "ずれたままの古い値"
