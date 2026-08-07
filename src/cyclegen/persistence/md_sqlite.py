"""persistence/md_sqlite.py — md正本 + SQLiteインデックスのハイブリッド永続化

実装計画書§6.2 / 設計書§4.1:
- 正本: ~/.cyclegen/memories/*.md（YAMLフロントマター + Markdown本文）
- インデックス: ~/.cyclegen/index.db（SQLite、検索高速化用）

save時: mdファイル書出 + SQLiteインデックス更新
起動時: md→SQLite差分同期（sync_from_md）
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import yaml

from cyclegen.models import Coordinates, Memory
from cyclegen.persistence.base import PersistenceAdapter, with_content_hash


class MdWithSQLitePersistence(PersistenceAdapter):
    """md正本 + SQLiteインデックスのハイブリッド永続化。

    利用者がmdファイルを直接編集可能（Git管理・手編集対応）。
    SQLiteは検索インデックスであり、正本はmd側。
    """

    def __init__(self, home: Path):
        self.home = home
        self.memories_dir = home / "memories"
        self.memories_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = home / "index.db"
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        """SQLiteテーブルとインデックスを作成する。

        設計書§4.1のCREATE TABLE準拠。
        event_logテーブルはCYCLE4.8（monitoring）で追加。
        """
        self.conn.executescript("""
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
                version INTEGER DEFAULT 1,
                embedding BLOB DEFAULT NULL,
                embedding_model TEXT DEFAULT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_layer ON memory_index(layer);
            CREATE INDEX IF NOT EXISTS idx_priority ON memory_index(priority DESC);
            CREATE INDEX IF NOT EXISTS idx_context ON memory_index(context);
            CREATE INDEX IF NOT EXISTS idx_3d ON memory_index(layer, priority DESC, context);
            CREATE INDEX IF NOT EXISTS idx_not_archived ON memory_index(archived) WHERE archived = FALSE;
        """)
        # マイグレーション: agent_idカラム追加（CYCLE6.1、既存DBとの互換性確保）
        try:
            self.conn.execute("SELECT agent_id FROM memory_index LIMIT 1")
        except sqlite3.OperationalError:
            self.conn.execute("ALTER TABLE memory_index ADD COLUMN agent_id TEXT")
            self.conn.commit()
        # マイグレーション: score_versionカラム追加（CYCLE12.6、Ankiモデル）
        try:
            self.conn.execute("SELECT score_version FROM memory_index LIMIT 1")
        except sqlite3.OperationalError:
            self.conn.execute("ALTER TABLE memory_index ADD COLUMN score_version INTEGER DEFAULT 1")
            self.conn.commit()
        # マイグレーション: embeddingカラム追加（CYCLE12.7.1、セマンティック検索）
        try:
            self.conn.execute("SELECT embedding FROM memory_index LIMIT 1")
        except sqlite3.OperationalError:
            self.conn.execute("ALTER TABLE memory_index ADD COLUMN embedding BLOB DEFAULT NULL")
            self.conn.commit()
        # マイグレーション: embedding_modelカラム追加（CYCLE19.2 / A8）
        #
        # 既存行は NULL のままにする。値を埋めない理由:
        # 既存embeddingが実際どのモデル・どの版で作られたかは記録が無く、分からない。
        # 現在のモデル名で埋めると「記録がある」ように見えてしまい、
        # 次に本当にモデルが変わったとき、その行だけ検知をすり抜ける。
        # NULL は「壊れている」ではなく「出所が不明」を意味する正しい状態。
        try:
            self.conn.execute("SELECT embedding_model FROM memory_index LIMIT 1")
        except sqlite3.OperationalError:
            self.conn.execute("ALTER TABLE memory_index ADD COLUMN embedding_model TEXT DEFAULT NULL")
            self.conn.commit()

    def save(self, memory: Memory) -> bool:
        """mdファイル書出 + SQLiteインデックス更新。"""
        md_path = self._write_md(memory)
        self._upsert_index(memory, md_path)
        return True

    def load(self, memory_id: str) -> Memory | None:
        """SQLiteからmd_pathを取得 → mdファイルを読み込んでMemoryに変換。"""
        row = self.conn.execute(
            "SELECT md_path, embedding, embedding_model FROM memory_index WHERE id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            return None

        md_path = Path(row["md_path"])
        if not md_path.exists():
            return None

        memory = self._read_md(md_path)
        # embeddingはバイナリなのでSQLiteからのみ復元
        if row["embedding"] is not None:
            memory.embedding = bytes(row["embedding"])
        memory.embedding_model = row["embedding_model"]
        return memory

    def load_all(self, include_archived: bool = False) -> list[Memory]:
        """SQLiteから全件のmd_pathを取得し、各mdを読み込む。"""
        if include_archived:
            rows = self.conn.execute(
                "SELECT md_path, embedding, embedding_model FROM memory_index ORDER BY priority DESC"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT md_path, embedding, embedding_model FROM memory_index WHERE archived = FALSE ORDER BY priority DESC"
            ).fetchall()

        memories = []
        for row in rows:
            md_path = Path(row["md_path"])
            if md_path.exists():
                memory = self._read_md(md_path)
                if row["embedding"] is not None:
                    memory.embedding = bytes(row["embedding"])
                memory.embedding_model = row["embedding_model"]
                memories.append(memory)
        return memories

    def search(
        self,
        keyword: str | None = None,
        layer: int | None = None,
        priority_min: float = 0.0,
        context: str | None = None,
        limit: int = 100,
    ) -> list[Memory]:
        """SQLiteインデックスで候補を絞り込み、mdから読み込む。"""
        conditions = ["archived = FALSE", "priority >= ?"]
        params: list = [priority_min]

        if keyword:
            conditions.append("content_preview LIKE ?")
            params.append(f"%{keyword}%")
        if layer is not None:
            conditions.append("layer = ?")
            params.append(layer)
        if context:
            conditions.append("context = ?")
            params.append(context)

        where = " AND ".join(conditions)
        params.append(limit)

        rows = self.conn.execute(
            f"SELECT md_path FROM memory_index WHERE {where} ORDER BY priority DESC LIMIT ?",
            params,
        ).fetchall()

        memories = []
        for row in rows:
            md_path = Path(row["md_path"])
            if md_path.exists():
                memories.append(self._read_md(md_path))
        return memories

    def update(self, memory_id: str, updates: dict) -> bool:
        """記憶のフィールドを更新する（md正本 + SQLiteインデックス両方）。

        version楽観的ロック: 現在のversionを+1する。
        """
        memory = self.load(memory_id)
        if memory is None:
            return False

        # CYCLE20.5（FR061⓪）: content が変わるなら content_hash も一緒に変える
        updates = with_content_hash(updates)

        # updatesを適用
        for key, value in updates.items():
            if key == "content":
                memory.content = value
            elif key == "content_hash":
                memory.content_hash = value
            elif key == "coordinates.layer":
                memory.coordinates.layer = value
            elif key == "coordinates.priority":
                memory.coordinates.priority = value
            elif key == "coordinates.context":
                memory.coordinates.context = value
            elif key == "tags":
                memory.tags = value
            elif key == "pinned":
                memory.pinned = value
            elif key == "archived":
                memory.archived = value
            elif key == "access_count":
                memory.access_count = value
            elif key == "last_accessed_at":
                memory.last_accessed_at = value
            elif key == "agent_id":
                memory.agent_id = value
            elif key == "score_version":
                memory.score_version = value
            elif key == "embedding":
                memory.embedding = value
            elif key == "embedding_model":
                memory.embedding_model = value

        memory.updated_at = datetime.now()
        memory.version += 1

        # md正本を上書き + インデックス更新
        md_path = self._write_md(memory)
        self._upsert_index(memory, md_path)
        return True

    def delete(self, memory_id: str) -> bool:
        """mdファイル削除 + SQLiteインデックスから削除。"""
        row = self.conn.execute(
            "SELECT md_path FROM memory_index WHERE id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            return False

        # mdファイル削除
        md_path = Path(row["md_path"])
        if md_path.exists():
            md_path.unlink()

        # SQLiteインデックス削除
        self.conn.execute("DELETE FROM memory_index WHERE id = ?", (memory_id,))
        self.conn.commit()
        return True

    def count(self, include_archived: bool = False) -> int:
        """記憶の件数を返す。"""
        if include_archived:
            row = self.conn.execute("SELECT COUNT(*) as cnt FROM memory_index").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM memory_index WHERE archived = FALSE"
            ).fetchone()
        return row["cnt"]

    def sync_from_md(self) -> int:
        """mdファイル群とSQLiteインデックスの差分同期。

        AIエディタ起動時に呼ばれる。
        1. memories_dir の *.md を走査
        2. 各mdのYAMLフロントマターを解析
        3. SQLiteに存在しない → INSERT、存在する＋更新あり → UPDATE
        4. SQLiteにあるがmdがない → DELETE

        Returns: 同期した件数
        """
        synced = 0
        md_files = {p.stem: p for p in self.memories_dir.glob("*.md")}

        # 既存インデックスのID一覧
        indexed_rows = self.conn.execute(
            "SELECT id, md_path, updated_at FROM memory_index"
        ).fetchall()
        indexed = {row["id"]: row for row in indexed_rows}

        # md → SQLite 同期（INSERT / UPDATE）
        for memory_id, md_path in md_files.items():
            memory = self._read_md(md_path)

            if memory_id not in indexed:
                # 新規: INSERT
                self._upsert_index(memory, md_path)
                synced += 1
            else:
                # 既存: 更新チェック（updated_atで判定）
                db_updated = indexed[memory_id]["updated_at"]
                md_updated = memory.updated_at.isoformat()
                if db_updated != md_updated:
                    self._upsert_index(memory, md_path)
                    synced += 1

        # SQLite → md 削除同期（mdが消えたらインデックスも消す）
        for memory_id in indexed:
            if memory_id not in md_files:
                self.conn.execute(
                    "DELETE FROM memory_index WHERE id = ?", (memory_id,)
                )
                self.conn.commit()
                synced += 1

        return synced

    def _write_md(self, memory: Memory) -> Path:
        """Memory → YAMLフロントマター付きmdファイルに書き出す。

        ファイル名: {memory.id}.md
        形式:
        ---
        id: mem_xxx
        layer: 3
        priority: 0.7
        context: implementation
        ...
        ---
        本文テキスト
        """
        md_path = self.memories_dir / f"{memory.id}.md"

        frontmatter = {
            "id": memory.id,
            "layer": memory.coordinates.layer,
            "priority": memory.coordinates.priority,
            "context": memory.coordinates.context,
            "tags": memory.tags,
            "owner_id": memory.owner_id,
            "pinned": memory.pinned,
            "archived": memory.archived,
            "created_at": memory.created_at.isoformat(),
            "updated_at": memory.updated_at.isoformat(),
            "last_accessed_at": memory.last_accessed_at.isoformat(),
            "access_count": memory.access_count,
            "score_version": memory.score_version,
            "version": memory.version,
        }
        if memory.agent_id is not None:
            frontmatter["agent_id"] = memory.agent_id
        if memory.content_hash:
            frontmatter["content_hash"] = memory.content_hash

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("---\n")
            yaml.dump(
                frontmatter,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
            f.write("---\n")
            f.write(memory.content)

        return md_path

    def _read_md(self, md_path: Path) -> Memory:
        """mdファイル → Memory に変換する。"""
        with open(md_path, encoding="utf-8") as f:
            text = f.read()

        # YAMLフロントマター解析
        if not text.startswith("---\n"):
            raise ValueError(f"Invalid md format (no frontmatter): {md_path}")

        parts = text.split("---\n", 2)
        if len(parts) < 3:
            raise ValueError(f"Invalid md format (incomplete frontmatter): {md_path}")

        frontmatter = yaml.safe_load(parts[1])
        content = parts[2]

        return Memory(
            id=frontmatter["id"],
            content=content,
            coordinates=Coordinates(
                layer=frontmatter["layer"],
                priority=frontmatter["priority"],
                context=frontmatter["context"],
            ),
            tags=frontmatter.get("tags", []),
            owner_id=frontmatter.get("owner_id", ""),
            agent_id=frontmatter.get("agent_id"),
            content_hash=frontmatter.get("content_hash", ""),
            pinned=frontmatter.get("pinned", False),
            archived=frontmatter.get("archived", False),
            created_at=datetime.fromisoformat(frontmatter["created_at"]),
            updated_at=datetime.fromisoformat(frontmatter["updated_at"]),
            last_accessed_at=datetime.fromisoformat(frontmatter["last_accessed_at"]),
            access_count=frontmatter.get("access_count", 0),
            score_version=frontmatter.get("score_version", 1),
            version=frontmatter.get("version", 1),
        )

    def _upsert_index(self, memory: Memory, md_path: Path) -> None:
        """SQLiteにUPSERT。"""
        self.conn.execute(
            """
            INSERT INTO memory_index
                (id, md_path, layer, priority, context, pinned, archived,
                 content_preview, tags, owner_id, agent_id,
                 created_at, updated_at, last_accessed_at, access_count, score_version, version,
                 embedding, embedding_model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                md_path = excluded.md_path,
                layer = excluded.layer,
                priority = excluded.priority,
                context = excluded.context,
                pinned = excluded.pinned,
                archived = excluded.archived,
                content_preview = excluded.content_preview,
                tags = excluded.tags,
                owner_id = excluded.owner_id,
                agent_id = excluded.agent_id,
                updated_at = excluded.updated_at,
                last_accessed_at = excluded.last_accessed_at,
                access_count = excluded.access_count,
                score_version = excluded.score_version,
                version = excluded.version,
                embedding = excluded.embedding,
                embedding_model = excluded.embedding_model
            """,
            (
                memory.id,
                str(md_path),
                memory.coordinates.layer,
                memory.coordinates.priority,
                memory.coordinates.context,
                memory.pinned,
                memory.archived,
                memory.content,
                ",".join(memory.tags),
                memory.owner_id,
                memory.agent_id,
                memory.created_at.isoformat(),
                memory.updated_at.isoformat(),
                memory.last_accessed_at.isoformat(),
                memory.access_count,
                memory.score_version,
                memory.version,
                memory.embedding,
                memory.embedding_model,
            ),
        )
        self.conn.commit()

    def close(self) -> None:
        """DB接続を閉じる。"""
        if self._conn:
            self._conn.close()
            self._conn = None
