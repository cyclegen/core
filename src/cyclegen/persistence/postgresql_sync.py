"""persistence/postgresql_sync.py — PostgreSQL同期永続化（Phase2用）

設計書v2 §3.2 / §4.2:
psycopg2ベースの同期版。PersistenceAdapter ABCに準拠。
MemorySystem3Dが同期で動作するため、Phase2ではこの同期版を使用する。

Phase3でMemorySystem3D全体を非同期化した後、本ファイルは廃止し
既存のasyncpg版（postgresql.py）に統一する。

テーブル名は環境変数 CYCLEGEN_TABLE で設定可能（パターン4準備）。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from cyclegen.models import Coordinates, Memory
from cyclegen.persistence.base import PersistenceAdapter, with_content_hash

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class SyncPostgreSQLPersistence(PersistenceAdapter):
    """PostgreSQL同期永続化（Phase2用、非推奨）。

    PersistenceAdapter ABCの同期インターフェースをpsycopg2で実装する。
    テーブル名は環境変数 CYCLEGEN_TABLE で設定可能（デフォルト: org_memories）。

    .. deprecated:: CYCLE7.7.3
        Phase3以降は AsyncPostgreSQLPersistence（asyncpg）を使用すること。
        本クラスは後方互換のために維持するが、新規利用は非推奨。
    """

    def __init__(self, dsn: str, table_name: str | None = None):
        if psycopg2 is None:
            raise RuntimeError(
                "psycopg2 is required. Install with: pip install psycopg2-binary"
            )
        self.dsn = dsn
        self.table_name = table_name or os.environ.get("CYCLEGEN_TABLE", "org_memories")
        self._conn: Any = None

    @property
    def conn(self) -> Any:
        """psycopg2コネクションを返す（遅延初期化）。"""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.dsn)
            self._conn.autocommit = False
        # abortedトランザクションをリセット
        if self._conn.status != 0:  # 0 = STATUS_READY
            try:
                self._conn.rollback()
            except Exception:
                pass
        return self._conn

    def init_tables(self) -> None:
        """テーブルが存在しなければ作成する。"""
        t = self.table_name
        cur = self.conn.cursor()
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {t} (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                layer INTEGER NOT NULL,
                priority REAL NOT NULL,
                context TEXT NOT NULL,
                tags TEXT DEFAULT '',
                owner_id TEXT DEFAULT '',
                agent_id TEXT,
                content_hash TEXT DEFAULT '',
                pinned BOOLEAN DEFAULT FALSE,
                archived BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                access_count INTEGER DEFAULT 0,
                score_version INTEGER DEFAULT 1,
                version INTEGER DEFAULT 1,
                embedding BYTEA DEFAULT NULL
            )
        """)
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_layer ON {t}(layer)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_priority ON {t}(priority DESC)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_context ON {t}(context)")
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{t}_3d ON {t}(layer, priority DESC, context)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{t}_not_archived "
            f"ON {t}(archived) WHERE archived = FALSE"
        )
        # 既存テーブルへのカラム追加（マイグレーション互換）
        cur.execute(
            f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS content_hash TEXT DEFAULT ''"
        )
        cur.execute(
            f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS score_version INTEGER DEFAULT 1"
        )
        cur.execute(
            f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS embedding BYTEA DEFAULT NULL"
        )
        self.conn.commit()
        logger.info("テーブル %s を初期化しました", t)

    def close(self) -> None:
        """コネクションを閉じる。"""
        if self._conn and not self._conn.closed:
            self._conn.close()

    # === PersistenceAdapter ABC ===

    def save(self, memory: Memory) -> bool:
        t = self.table_name
        cur = self.conn.cursor()
        cur.execute(
            f"""
            INSERT INTO {t}
                (id, content, layer, priority, context, tags, owner_id, agent_id,
                 content_hash, pinned, archived,
                 created_at, updated_at, last_accessed_at, access_count, score_version, version,
                 embedding)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(id) DO UPDATE SET
                content=EXCLUDED.content, layer=EXCLUDED.layer, priority=EXCLUDED.priority,
                context=EXCLUDED.context, tags=EXCLUDED.tags, agent_id=EXCLUDED.agent_id,
                content_hash=EXCLUDED.content_hash,
                pinned=EXCLUDED.pinned, archived=EXCLUDED.archived,
                updated_at=EXCLUDED.updated_at,
                last_accessed_at=EXCLUDED.last_accessed_at,
                access_count=EXCLUDED.access_count,
                score_version=EXCLUDED.score_version, version=EXCLUDED.version,
                embedding=EXCLUDED.embedding
            """,
            (
                memory.id, memory.content,
                memory.coordinates.layer, memory.coordinates.priority,
                memory.coordinates.context,
                ",".join(memory.tags), memory.owner_id, memory.agent_id,
                memory.content_hash,
                memory.pinned, memory.archived,
                memory.created_at, memory.updated_at, memory.last_accessed_at,
                memory.access_count, memory.score_version, memory.version,
                memory.embedding,
            ),
        )
        self.conn.commit()
        return True

    def load(self, memory_id: str) -> Memory | None:
        t = self.table_name
        cur = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(f"SELECT * FROM {t} WHERE id = %s", (memory_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_memory(row)

    def load_all(self, include_archived: bool = False) -> list[Memory]:
        t = self.table_name
        cur = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if include_archived:
            cur.execute(f"SELECT * FROM {t} ORDER BY priority DESC")
        else:
            cur.execute(
                f"SELECT * FROM {t} WHERE archived = FALSE ORDER BY priority DESC"
            )
        return [self._row_to_memory(row) for row in cur.fetchall()]

    def search(
        self,
        keyword: str | None = None,
        layer: int | None = None,
        priority_min: float = 0.0,
        context: str | None = None,
        limit: int = 100,
    ) -> list[Memory]:
        t = self.table_name
        conditions = ["archived = FALSE", "priority >= %s"]
        params: list[Any] = [priority_min]

        if keyword:
            conditions.append("content ILIKE %s")
            params.append(f"%{keyword}%")
        if layer is not None:
            conditions.append("layer = %s")
            params.append(layer)
        if context:
            conditions.append("context = %s")
            params.append(context)

        params.append(limit)
        where = " AND ".join(conditions)
        query = f"SELECT * FROM {t} WHERE {where} ORDER BY priority DESC LIMIT %s"

        cur = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(query, params)
        return [self._row_to_memory(row) for row in cur.fetchall()]

    def update(self, memory_id: str, updates: dict) -> bool:
        t = self.table_name
        # CYCLE20.5（FR061⓪）: content が変わるなら content_hash も一緒に変える
        updates = with_content_hash(updates)
        field_map = {
            "content": "content",
            "coordinates.layer": "layer",
            "coordinates.priority": "priority",
            "coordinates.context": "context",
            "pinned": "pinned",
            "archived": "archived",
            "access_count": "access_count",
            "last_accessed_at": "last_accessed_at",
            "content_hash": "content_hash",
            "score_version": "score_version",
            "embedding": "embedding",
        }

        set_clauses = []
        params: list[Any] = []

        for key, value in updates.items():
            col = field_map.get(key)
            if col:
                set_clauses.append(f"{col} = %s")
                params.append(value)

        if not set_clauses:
            return False

        set_clauses.append("updated_at = %s")
        params.append(datetime.now())
        set_clauses.append("version = version + 1")

        params.append(memory_id)
        sql = f"UPDATE {t} SET {', '.join(set_clauses)} WHERE id = %s"

        cur = self.conn.cursor()
        cur.execute(sql, params)
        affected = cur.rowcount
        self.conn.commit()
        return affected > 0

    def delete(self, memory_id: str) -> bool:
        t = self.table_name
        cur = self.conn.cursor()
        cur.execute(f"DELETE FROM {t} WHERE id = %s", (memory_id,))
        affected = cur.rowcount
        self.conn.commit()
        return affected > 0

    def count(self, include_archived: bool = False) -> int:
        t = self.table_name
        cur = self.conn.cursor()
        if include_archived:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
        else:
            cur.execute(f"SELECT COUNT(*) FROM {t} WHERE archived = FALSE")
        return cur.fetchone()[0]

    # === Helper ===

    @staticmethod
    def _row_to_memory(row) -> Memory:
        """DBレコード → Memory 変換。"""
        tags = row["tags"].split(",") if row["tags"] else []
        return Memory(
            id=row["id"],
            content=row["content"],
            coordinates=Coordinates(
                layer=row["layer"],
                priority=row["priority"],
                context=row["context"],
            ),
            tags=tags,
            owner_id=row["owner_id"] or "",
            agent_id=row.get("agent_id"),
            content_hash=row.get("content_hash", ""),
            pinned=row["pinned"],
            archived=row["archived"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_accessed_at=row["last_accessed_at"],
            access_count=row["access_count"],
            embedding=bytes(row["embedding"]) if row.get("embedding") else None,
            score_version=row.get("score_version", 1),
            version=row["version"],
        )
