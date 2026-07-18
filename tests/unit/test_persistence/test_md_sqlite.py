"""test_md_sqlite.py — MdWithSQLitePersistence のユニットテスト

CRUD全操作 + sync_from_md双方向同期 + mdファイル形式検証 + 境界条件テスト。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from cyclegen.models import Coordinates, Memory
from cyclegen.persistence.md_sqlite import MdWithSQLitePersistence


@pytest.fixture
def persistence(tmp_path) -> MdWithSQLitePersistence:
    """テスト用の一時ディレクトリで永続化インスタンスを作成。"""
    p = MdWithSQLitePersistence(tmp_path)
    yield p
    p.close()


@pytest.fixture
def sample_memory() -> Memory:
    return Memory(
        id="mem_test_001",
        content="Pythonでデータモデルを実装した",
        coordinates=Coordinates(layer=3, priority=0.7, context="implementation"),
        tags=["python", "model"],
        owner_id="user1",
    )


@pytest.fixture
def sample_memory_2() -> Memory:
    return Memory(
        id="mem_test_002",
        content="アーキテクチャの設計方針を決定",
        coordinates=Coordinates(layer=4, priority=0.9, context="planning"),
        tags=["architecture"],
        owner_id="user1",
    )


# === Save & Load ===


class TestSave:
    def test_save_creates_md_file(self, persistence, sample_memory, tmp_path):
        persistence.save(sample_memory)
        md_path = tmp_path / "memories" / "mem_test_001.md"
        assert md_path.exists()

    def test_save_creates_sqlite_index(self, persistence, sample_memory):
        persistence.save(sample_memory)
        row = persistence.conn.execute(
            "SELECT * FROM memory_index WHERE id = ?", ("mem_test_001",)
        ).fetchone()
        assert row is not None
        assert row["layer"] == 3
        assert row["priority"] == 0.7
        assert row["context"] == "implementation"

    def test_save_returns_true(self, persistence, sample_memory):
        assert persistence.save(sample_memory) is True

    def test_save_overwrites_existing(self, persistence, sample_memory):
        persistence.save(sample_memory)
        sample_memory.content = "更新された内容"
        persistence.save(sample_memory)
        loaded = persistence.load("mem_test_001")
        assert loaded.content == "更新された内容"


class TestLoad:
    def test_load_existing(self, persistence, sample_memory):
        persistence.save(sample_memory)
        loaded = persistence.load("mem_test_001")
        assert loaded is not None
        assert loaded.id == "mem_test_001"
        assert loaded.content == "Pythonでデータモデルを実装した"
        assert loaded.coordinates.layer == 3
        assert loaded.coordinates.priority == 0.7
        assert loaded.coordinates.context == "implementation"
        assert loaded.tags == ["python", "model"]
        assert loaded.owner_id == "user1"

    def test_load_nonexistent(self, persistence):
        assert persistence.load("nonexistent") is None

    def test_load_preserves_all_fields(self, persistence):
        now = datetime(2026, 4, 11, 10, 0, 0)
        m = Memory(
            id="mem_full",
            content="全フィールドテスト",
            coordinates=Coordinates(layer=5, priority=1.0, context="planning"),
            tags=["a", "b", "c"],
            owner_id="admin",
            pinned=True,
            archived=False,
            created_at=now,
            updated_at=now,
            last_accessed_at=now,
            access_count=42,
            version=3,
        )
        persistence.save(m)
        loaded = persistence.load("mem_full")
        assert loaded.pinned is True
        assert loaded.access_count == 42
        assert loaded.version == 3
        assert loaded.tags == ["a", "b", "c"]


# === Load All ===


class TestLoadAll:
    def test_load_all_empty(self, persistence):
        assert persistence.load_all() == []

    def test_load_all_multiple(self, persistence, sample_memory, sample_memory_2):
        persistence.save(sample_memory)
        persistence.save(sample_memory_2)
        all_memories = persistence.load_all()
        assert len(all_memories) == 2

    def test_load_all_excludes_archived(self, persistence, sample_memory, sample_memory_2):
        sample_memory.archived = True
        persistence.save(sample_memory)
        persistence.save(sample_memory_2)
        result = persistence.load_all(include_archived=False)
        assert len(result) == 1
        assert result[0].id == "mem_test_002"

    def test_load_all_includes_archived(self, persistence, sample_memory, sample_memory_2):
        sample_memory.archived = True
        persistence.save(sample_memory)
        persistence.save(sample_memory_2)
        result = persistence.load_all(include_archived=True)
        assert len(result) == 2

    def test_load_all_ordered_by_priority(self, persistence, sample_memory, sample_memory_2):
        persistence.save(sample_memory)  # priority=0.7
        persistence.save(sample_memory_2)  # priority=0.9
        result = persistence.load_all()
        assert result[0].coordinates.priority >= result[1].coordinates.priority


# === Search ===


class TestSearch:
    def test_search_by_keyword(self, persistence, sample_memory, sample_memory_2):
        persistence.save(sample_memory)
        persistence.save(sample_memory_2)
        result = persistence.search(keyword="Python")
        assert len(result) == 1
        assert result[0].id == "mem_test_001"

    def test_search_by_layer(self, persistence, sample_memory, sample_memory_2):
        persistence.save(sample_memory)
        persistence.save(sample_memory_2)
        result = persistence.search(layer=4)
        assert len(result) == 1
        assert result[0].id == "mem_test_002"

    def test_search_by_context(self, persistence, sample_memory, sample_memory_2):
        persistence.save(sample_memory)
        persistence.save(sample_memory_2)
        result = persistence.search(context="planning")
        assert len(result) == 1
        assert result[0].id == "mem_test_002"

    def test_search_by_priority_min(self, persistence, sample_memory, sample_memory_2):
        persistence.save(sample_memory)
        persistence.save(sample_memory_2)
        result = persistence.search(priority_min=0.8)
        assert len(result) == 1
        assert result[0].id == "mem_test_002"

    def test_search_excludes_archived(self, persistence, sample_memory):
        sample_memory.archived = True
        persistence.save(sample_memory)
        result = persistence.search(keyword="Python")
        assert len(result) == 0

    def test_search_with_limit(self, persistence):
        for i in range(10):
            m = Memory(
                id=f"mem_bulk_{i:03d}",
                content=f"テスト記憶 {i}",
                coordinates=Coordinates(layer=3, priority=0.5, context="impl"),
            )
            persistence.save(m)
        result = persistence.search(limit=3)
        assert len(result) == 3

    def test_search_combined_filters(self, persistence, sample_memory, sample_memory_2):
        persistence.save(sample_memory)
        persistence.save(sample_memory_2)
        result = persistence.search(layer=3, context="implementation")
        assert len(result) == 1
        assert result[0].id == "mem_test_001"


# === Update ===


class TestUpdate:
    def test_update_content(self, persistence, sample_memory):
        persistence.save(sample_memory)
        result = persistence.update("mem_test_001", {"content": "新しい内容"})
        assert result is True
        loaded = persistence.load("mem_test_001")
        assert loaded.content == "新しい内容"

    def test_update_coordinates(self, persistence, sample_memory):
        persistence.save(sample_memory)
        persistence.update("mem_test_001", {
            "coordinates.layer": 4,
            "coordinates.priority": 0.9,
        })
        loaded = persistence.load("mem_test_001")
        assert loaded.coordinates.layer == 4
        assert loaded.coordinates.priority == 0.9

    def test_update_increments_version(self, persistence, sample_memory):
        persistence.save(sample_memory)
        persistence.update("mem_test_001", {"content": "v2"})
        loaded = persistence.load("mem_test_001")
        assert loaded.version == 2

    def test_update_sets_updated_at(self, persistence, sample_memory):
        persistence.save(sample_memory)
        before = persistence.load("mem_test_001").updated_at
        persistence.update("mem_test_001", {"content": "v2"})
        after = persistence.load("mem_test_001").updated_at
        assert after >= before

    def test_update_nonexistent(self, persistence):
        assert persistence.update("nonexistent", {"content": "x"}) is False

    def test_update_pinned(self, persistence, sample_memory):
        persistence.save(sample_memory)
        persistence.update("mem_test_001", {"pinned": True})
        loaded = persistence.load("mem_test_001")
        assert loaded.pinned is True

    def test_update_archived(self, persistence, sample_memory):
        persistence.save(sample_memory)
        persistence.update("mem_test_001", {"archived": True})
        loaded = persistence.load("mem_test_001")
        assert loaded.archived is True


# === Delete ===


class TestDelete:
    def test_delete_existing(self, persistence, sample_memory, tmp_path):
        persistence.save(sample_memory)
        result = persistence.delete("mem_test_001")
        assert result is True
        assert persistence.load("mem_test_001") is None
        assert not (tmp_path / "memories" / "mem_test_001.md").exists()

    def test_delete_nonexistent(self, persistence):
        assert persistence.delete("nonexistent") is False

    def test_delete_removes_from_index(self, persistence, sample_memory):
        persistence.save(sample_memory)
        persistence.delete("mem_test_001")
        row = persistence.conn.execute(
            "SELECT * FROM memory_index WHERE id = ?", ("mem_test_001",)
        ).fetchone()
        assert row is None


# === Count ===


class TestCount:
    def test_count_empty(self, persistence):
        assert persistence.count() == 0

    def test_count_multiple(self, persistence, sample_memory, sample_memory_2):
        persistence.save(sample_memory)
        persistence.save(sample_memory_2)
        assert persistence.count() == 2

    def test_count_excludes_archived(self, persistence, sample_memory, sample_memory_2):
        sample_memory.archived = True
        persistence.save(sample_memory)
        persistence.save(sample_memory_2)
        assert persistence.count(include_archived=False) == 1
        assert persistence.count(include_archived=True) == 2


# === Md File Format ===


class TestMdFormat:
    def test_md_has_yaml_frontmatter(self, persistence, sample_memory, tmp_path):
        persistence.save(sample_memory)
        md_path = tmp_path / "memories" / "mem_test_001.md"
        text = md_path.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert text.count("---\n") >= 2

    def test_md_frontmatter_parseable(self, persistence, sample_memory, tmp_path):
        persistence.save(sample_memory)
        md_path = tmp_path / "memories" / "mem_test_001.md"
        text = md_path.read_text(encoding="utf-8")
        parts = text.split("---\n", 2)
        fm = yaml.safe_load(parts[1])
        assert fm["id"] == "mem_test_001"
        assert fm["layer"] == 3
        assert fm["priority"] == 0.7
        assert fm["context"] == "implementation"

    def test_md_body_is_content(self, persistence, sample_memory, tmp_path):
        persistence.save(sample_memory)
        md_path = tmp_path / "memories" / "mem_test_001.md"
        text = md_path.read_text(encoding="utf-8")
        parts = text.split("---\n", 2)
        assert parts[2] == "Pythonでデータモデルを実装した"

    def test_md_roundtrip(self, persistence, sample_memory):
        """save → load のラウンドトリップで内容が保持される"""
        persistence.save(sample_memory)
        loaded = persistence.load("mem_test_001")
        assert loaded.id == sample_memory.id
        assert loaded.content == sample_memory.content
        assert loaded.coordinates.layer == sample_memory.coordinates.layer
        assert loaded.coordinates.priority == sample_memory.coordinates.priority
        assert loaded.coordinates.context == sample_memory.coordinates.context
        assert loaded.tags == sample_memory.tags


# === Sync From Md ===


class TestSyncFromMd:
    def test_sync_new_md_file(self, persistence, tmp_path):
        """手動で追加されたmdファイルがインデックスに追加される"""
        # mdファイルを手動作成
        md_path = tmp_path / "memories" / "mem_manual_001.md"
        fm = {
            "id": "mem_manual_001",
            "layer": 2,
            "priority": 0.6,
            "context": "debugging",
            "tags": [],
            "owner_id": "",
            "pinned": False,
            "archived": False,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "last_accessed_at": datetime.now().isoformat(),
            "access_count": 0,
            "version": 1,
        }
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("---\n")
            yaml.dump(fm, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            f.write("---\n")
            f.write("手動追加された記憶")

        synced = persistence.sync_from_md()
        assert synced == 1

        loaded = persistence.load("mem_manual_001")
        assert loaded is not None
        assert loaded.content == "手動追加された記憶"

    def test_sync_deleted_md_file(self, persistence, sample_memory, tmp_path):
        """mdファイルが削除されたらインデックスも削除される"""
        persistence.save(sample_memory)
        # mdファイルを手動削除
        md_path = tmp_path / "memories" / "mem_test_001.md"
        md_path.unlink()

        synced = persistence.sync_from_md()
        assert synced == 1
        assert persistence.load("mem_test_001") is None

    def test_sync_updated_md_file(self, persistence, sample_memory, tmp_path):
        """mdファイルが更新されたらインデックスも更新される"""
        persistence.save(sample_memory)

        # mdを手動編集（updated_atを変更）
        md_path = tmp_path / "memories" / "mem_test_001.md"
        text = md_path.read_text(encoding="utf-8")
        parts = text.split("---\n", 2)
        fm = yaml.safe_load(parts[1])
        fm["priority"] = 0.95
        fm["updated_at"] = (datetime.now() + timedelta(hours=1)).isoformat()

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("---\n")
            yaml.dump(fm, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            f.write("---\n")
            f.write(parts[2])

        synced = persistence.sync_from_md()
        assert synced == 1
        loaded = persistence.load("mem_test_001")
        assert loaded.coordinates.priority == 0.95

    def test_sync_no_changes(self, persistence, sample_memory):
        """変更なしの場合は0件"""
        persistence.save(sample_memory)
        synced = persistence.sync_from_md()
        assert synced == 0

    def test_sync_multiple_operations(self, persistence, sample_memory, sample_memory_2, tmp_path):
        """追加・更新・削除が同時に起きるケース"""
        persistence.save(sample_memory)
        persistence.save(sample_memory_2)

        # mem_test_001のmdを削除
        (tmp_path / "memories" / "mem_test_001.md").unlink()

        # 新しいmdを手動追加
        fm = {
            "id": "mem_new",
            "layer": 1,
            "priority": 0.3,
            "context": "operations",
            "tags": [],
            "owner_id": "",
            "pinned": False,
            "archived": False,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "last_accessed_at": datetime.now().isoformat(),
            "access_count": 0,
            "version": 1,
        }
        md_path = tmp_path / "memories" / "mem_new.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("---\n")
            yaml.dump(fm, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            f.write("---\n")
            f.write("新規記憶")

        synced = persistence.sync_from_md()
        assert synced == 2  # 1 delete + 1 insert
        assert persistence.load("mem_test_001") is None
        assert persistence.load("mem_new") is not None
        assert persistence.count() == 2


# === Edge Cases ===


class TestEdgeCases:
    def test_unicode_content(self, persistence):
        m = Memory(
            id="mem_unicode",
            content="日本語テスト: 記憶の庭師 🌱",
            coordinates=Coordinates(layer=3, priority=0.5, context="learning"),
        )
        persistence.save(m)
        loaded = persistence.load("mem_unicode")
        assert loaded.content == "日本語テスト: 記憶の庭師 🌱"

    def test_multiline_content(self, persistence):
        content = "行1\n行2\n行3\n\n空行あり"
        m = Memory(
            id="mem_multiline",
            content=content,
            coordinates=Coordinates(layer=2, priority=0.6, context="documentation"),
        )
        persistence.save(m)
        loaded = persistence.load("mem_multiline")
        assert loaded.content == content

    def test_empty_tags(self, persistence):
        m = Memory(
            id="mem_no_tags",
            content="タグなし",
            coordinates=Coordinates(layer=1, priority=0.4, context="debugging"),
            tags=[],
        )
        persistence.save(m)
        loaded = persistence.load("mem_no_tags")
        assert loaded.tags == []

    def test_many_tags(self, persistence):
        tags = [f"tag_{i}" for i in range(20)]
        m = Memory(
            id="mem_many_tags",
            content="大量タグ",
            coordinates=Coordinates(layer=3, priority=0.5, context="impl"),
            tags=tags,
        )
        persistence.save(m)
        loaded = persistence.load("mem_many_tags")
        assert loaded.tags == tags

    def test_agent_id_roundtrip(self, persistence):
        """agent_id付き記憶のsave→load ラウンドトリップ"""
        m = Memory(
            id="mem_agent_001",
            content="エージェントの記憶",
            coordinates=Coordinates(layer=3, priority=0.6, context="implementation"),
            agent_id="agent-alpha",
        )
        persistence.save(m)
        loaded = persistence.load("mem_agent_001")
        assert loaded.agent_id == "agent-alpha"

    def test_agent_id_none_default(self, persistence):
        """agent_id省略時はNone"""
        m = Memory(
            id="mem_no_agent",
            content="通常の記憶",
            coordinates=Coordinates(layer=2, priority=0.5, context="debugging"),
        )
        persistence.save(m)
        loaded = persistence.load("mem_no_agent")
        assert loaded.agent_id is None

    def test_agent_id_in_md_frontmatter(self, persistence, tmp_path):
        """agent_idがmdフロントマターに出力される"""
        m = Memory(
            id="mem_agent_md",
            content="md確認",
            coordinates=Coordinates(layer=3, priority=0.5, context="implementation"),
            agent_id="agent-beta",
        )
        persistence.save(m)
        md_path = tmp_path / "memories" / "mem_agent_md.md"
        text = md_path.read_text(encoding="utf-8")
        parts = text.split("---\n", 2)
        fm = yaml.safe_load(parts[1])
        assert fm["agent_id"] == "agent-beta"

    def test_agent_id_absent_in_md_when_none(self, persistence, tmp_path):
        """agent_idがNoneの場合はmdフロントマターに出力されない"""
        m = Memory(
            id="mem_no_agent_md",
            content="mdなし確認",
            coordinates=Coordinates(layer=3, priority=0.5, context="implementation"),
        )
        persistence.save(m)
        md_path = tmp_path / "memories" / "mem_no_agent_md.md"
        text = md_path.read_text(encoding="utf-8")
        parts = text.split("---\n", 2)
        fm = yaml.safe_load(parts[1])
        assert "agent_id" not in fm

    def test_score_version_roundtrip(self, persistence):
        """CYCLE12.6: score_versionのsave→loadラウンドトリップ"""
        m = Memory(
            id="mem_sv_test",
            content="score_versionテスト",
            coordinates=Coordinates(layer=3, priority=0.3, context="planning"),
            score_version=2,
        )
        persistence.save(m)
        loaded = persistence.load("mem_sv_test")
        assert loaded.score_version == 2

    def test_score_version_default_1(self, persistence):
        """CYCLE12.6: score_version未指定時はデフォルト1"""
        m = Memory(
            id="mem_sv_default",
            content="デフォルトテスト",
            coordinates=Coordinates(layer=2, priority=0.5, context="debugging"),
        )
        persistence.save(m)
        loaded = persistence.load("mem_sv_default")
        assert loaded.score_version == 1

    def test_score_version_update(self, persistence):
        """CYCLE12.6: score_versionをupdateで変更できる"""
        m = Memory(
            id="mem_sv_update",
            content="updateテスト",
            coordinates=Coordinates(layer=3, priority=0.9, context="planning"),
            score_version=1,
        )
        persistence.save(m)
        persistence.update("mem_sv_update", {"score_version": 2, "coordinates.priority": 0.3})
        loaded = persistence.load("mem_sv_update")
        assert loaded.score_version == 2
        assert loaded.coordinates.priority == 0.3

    def test_score_version_backward_compat_md(self, persistence, tmp_path):
        """CYCLE12.6: score_version未存在のmdファイルはv1としてロード"""
        fm = {
            "id": "mem_old_format",
            "layer": 3,
            "priority": 0.9,
            "context": "planning",
            "tags": [],
            "owner_id": "",
            "pinned": False,
            "archived": False,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "last_accessed_at": datetime.now().isoformat(),
            "access_count": 5,
            "version": 1,
            # score_version は意図的に省略
        }
        md_path = tmp_path / "memories" / "mem_old_format.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("---\n")
            yaml.dump(fm, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            f.write("---\n")
            f.write("旧形式の記憶")
        persistence.sync_from_md()
        loaded = persistence.load("mem_old_format")
        assert loaded is not None
        assert loaded.score_version == 1

    def test_agent_id_in_sqlite(self, persistence):
        """agent_idがSQLiteインデックスに保存される"""
        m = Memory(
            id="mem_agent_db",
            content="DB確認",
            coordinates=Coordinates(layer=3, priority=0.5, context="implementation"),
            agent_id="agent-gamma",
        )
        persistence.save(m)
        row = persistence.conn.execute(
            "SELECT agent_id FROM memory_index WHERE id = ?", ("mem_agent_db",)
        ).fetchone()
        assert row["agent_id"] == "agent-gamma"

    def test_memories_dir_created(self, tmp_path):
        home = tmp_path / "new_home"
        p = MdWithSQLitePersistence(home)
        assert (home / "memories").exists()
        p.close()


# === Embedding (CYCLE12.7.1) ===


class TestEmbedding:
    def test_embedding_save_and_load(self, persistence):
        """embedding付き記憶のsave→loadラウンドトリップ"""
        import numpy as np

        vec = np.random.randn(384).astype("float32").tobytes()
        m = Memory(
            id="mem_emb_001",
            content="embedding付き記憶",
            coordinates=Coordinates(layer=3, priority=0.5, context="implementation"),
            embedding=vec,
        )
        persistence.save(m)
        loaded = persistence.load("mem_emb_001")
        assert loaded.embedding is not None
        assert loaded.embedding == vec

    def test_embedding_none_default(self, persistence):
        """embedding省略時はNone"""
        m = Memory(
            id="mem_emb_none",
            content="embeddingなし",
            coordinates=Coordinates(layer=2, priority=0.5, context="debugging"),
        )
        persistence.save(m)
        loaded = persistence.load("mem_emb_none")
        assert loaded.embedding is None

    def test_embedding_in_load_all(self, persistence):
        """load_allでembeddingが復元される"""
        import numpy as np

        vec = np.random.randn(384).astype("float32").tobytes()
        m = Memory(
            id="mem_emb_all",
            content="load_allテスト",
            coordinates=Coordinates(layer=3, priority=0.5, context="planning"),
            embedding=vec,
        )
        persistence.save(m)
        all_mems = persistence.load_all()
        assert len(all_mems) == 1
        assert all_mems[0].embedding == vec

    def test_embedding_update(self, persistence):
        """updateでembeddingを更新できる"""
        import numpy as np

        m = Memory(
            id="mem_emb_upd",
            content="embedding更新テスト",
            coordinates=Coordinates(layer=3, priority=0.5, context="implementation"),
        )
        persistence.save(m)
        assert persistence.load("mem_emb_upd").embedding is None

        new_vec = np.random.randn(384).astype("float32").tobytes()
        persistence.update("mem_emb_upd", {"embedding": new_vec})
        loaded = persistence.load("mem_emb_upd")
        assert loaded.embedding == new_vec

    def test_embedding_not_in_md_frontmatter(self, persistence, tmp_path):
        """embeddingはmdフロントマターに出力されない（バイナリなのでSQLiteのみ）"""
        import numpy as np

        vec = np.random.randn(384).astype("float32").tobytes()
        m = Memory(
            id="mem_emb_md",
            content="mdに含まれないこと確認",
            coordinates=Coordinates(layer=3, priority=0.5, context="implementation"),
            embedding=vec,
        )
        persistence.save(m)
        md_path = tmp_path / "memories" / "mem_emb_md.md"
        text = md_path.read_text(encoding="utf-8")
        parts = text.split("---\n", 2)
        fm = yaml.safe_load(parts[1])
        assert "embedding" not in fm

    def test_embedding_preserved_after_sqlite_roundtrip(self, persistence):
        """SQLite BLOB経由でembeddingの精度が保持される"""
        import numpy as np

        original = np.array([0.1, 0.2, 0.3, -0.5, 1.0], dtype="float32")
        vec = original.tobytes()
        m = Memory(
            id="mem_emb_precision",
            content="精度テスト",
            coordinates=Coordinates(layer=2, priority=0.5, context="research"),
            embedding=vec,
        )
        persistence.save(m)
        loaded = persistence.load("mem_emb_precision")
        restored = np.frombuffer(loaded.embedding, dtype="float32")
        np.testing.assert_array_equal(original, restored)

    def test_embedding_migration_existing_db(self, tmp_path):
        """既存DB（embedding列なし）にマイグレーションが適用される"""
        import sqlite3

        # embedding列なしのDBを手動作成
        db_path = tmp_path / "index.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_index (
                id TEXT PRIMARY KEY,
                md_path TEXT NOT NULL,
                layer INTEGER NOT NULL,
                priority REAL NOT NULL,
                context TEXT NOT NULL,
                pinned BOOLEAN DEFAULT FALSE,
                archived BOOLEAN DEFAULT FALSE,
                content_preview TEXT,
                tags TEXT DEFAULT '',
                owner_id TEXT DEFAULT '',
                agent_id TEXT,
                created_at TEXT,
                updated_at TEXT,
                last_accessed_at TEXT,
                access_count INTEGER DEFAULT 0,
                score_version INTEGER DEFAULT 1,
                version INTEGER DEFAULT 1
            );
        """)
        conn.close()

        # MdWithSQLitePersistenceを初期化（マイグレーション発動）
        p = MdWithSQLitePersistence(tmp_path)
        # embedding列が追加されていることを確認
        row = p.conn.execute("PRAGMA table_info(memory_index)").fetchall()
        col_names = [r["name"] for r in row]
        assert "embedding" in col_names
        p.close()
