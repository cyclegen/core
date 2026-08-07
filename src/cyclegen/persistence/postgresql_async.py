"""persistence/postgresql_async.py — PostgreSQL非同期永続化（Memory対象）

CYCLE7.7.3: SyncPostgreSQLPersistence（psycopg2）の非同期版。
asyncpgベースで、PersistenceAdapterのasyncインターフェースを実装する。

Memoryモデル（Personal Layer）を対象とする。
OrgMemoryを扱う既存のpostgresql.py（Org Layer用）とは別。

テーブル名は設定で変更可能（パターン4準備）。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from cyclegen.models import Coordinates, Memory
from cyclegen.persistence.base import PersistenceAdapter, with_content_hash

try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class AsyncPostgreSQLPersistence(PersistenceAdapter):
    """PostgreSQL非同期永続化（Memory対象）。

    asyncpgコネクションプールを使用。
    同期メソッド（save/load等）はNotImplementedError。
    非同期メソッド（async_save/async_load等）を使用すること。
    """

    def __init__(self, dsn: str, table_name: str | None = None):
        if asyncpg is None:
            raise RuntimeError(
                "asyncpg is required. Install with: pip install asyncpg"
            )
        self.dsn = dsn
        self.table_name = table_name or os.environ.get("CYCLEGEN_TABLE", "memories")
        self._pool: Any = None  # asyncpg.Pool

    async def init_pool(self) -> None:
        """コネクションプールを初期化する。"""
        self._pool = await asyncpg.create_pool(dsn=self.dsn, min_size=2, max_size=10)
        logger.info("asyncpgプール初期化完了: %s", self.table_name)

    async def close_pool(self) -> None:
        """コネクションプールを閉じる。"""
        if self._pool:
            await self._pool.close()

    async def init_tables(self) -> None:
        """テーブルが存在しなければ作成する。"""
        t = self.table_name
        async with self._pool.acquire() as conn:
            await conn.execute(f"""
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
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{t}_layer ON {t}(layer)"
            )
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{t}_priority ON {t}(priority DESC)"
            )
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{t}_context ON {t}(context)"
            )
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{t}_3d ON {t}(layer, priority DESC, context)"
            )
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{t}_not_archived "
                f"ON {t}(archived) WHERE archived = FALSE"
            )
            # 既存テーブルへのカラム追加（マイグレーション互換）
            await conn.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS content_hash TEXT DEFAULT ''"
            )
            await conn.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS score_version INTEGER DEFAULT 1"
            )
            await conn.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS embedding BYTEA DEFAULT NULL"
            )
        logger.info("テーブル %s を初期化しました", t)

    # === Async CRUD（PersistenceAdapter async interface） ===

    async def async_save(self, memory: Memory) -> bool:
        t = self.table_name
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {t}
                    (id, content, layer, priority, context, tags, owner_id, agent_id,
                     content_hash, pinned, archived,
                     created_at, updated_at, last_accessed_at, access_count, score_version, version,
                     embedding)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
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
                memory.id, memory.content,
                memory.coordinates.layer, memory.coordinates.priority,
                memory.coordinates.context,
                ",".join(memory.tags), memory.owner_id, memory.agent_id,
                memory.content_hash,
                memory.pinned, memory.archived,
                memory.created_at, memory.updated_at, memory.last_accessed_at,
                memory.access_count, memory.score_version, memory.version,
                memory.embedding,
            )
        return True

    async def async_load(self, memory_id: str) -> Memory | None:
        t = self.table_name
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {t} WHERE id = $1", memory_id
            )
        if row is None:
            return None
        return self._row_to_memory(row)

    async def async_load_all(self, include_archived: bool = False) -> list[Memory]:
        t = self.table_name
        async with self._pool.acquire() as conn:
            if include_archived:
                rows = await conn.fetch(f"SELECT * FROM {t} ORDER BY priority DESC")
            else:
                rows = await conn.fetch(
                    f"SELECT * FROM {t} WHERE archived = FALSE ORDER BY priority DESC"
                )
        return [self._row_to_memory(row) for row in rows]

    async def async_search(
        self,
        keyword: str | None = None,
        layer: int | None = None,
        priority_min: float = 0.0,
        context: str | None = None,
        limit: int = 100,
    ) -> list[Memory]:
        t = self.table_name
        conditions = ["archived = FALSE", "priority >= $1"]
        params: list[Any] = [priority_min]
        idx = 2

        if keyword:
            conditions.append(f"content ILIKE ${idx}")
            params.append(f"%{keyword}%")
            idx += 1
        if layer is not None:
            conditions.append(f"layer = ${idx}")
            params.append(layer)
            idx += 1
        if context:
            conditions.append(f"context = ${idx}")
            params.append(context)
            idx += 1

        params.append(limit)
        where = " AND ".join(conditions)
        query = f"SELECT * FROM {t} WHERE {where} ORDER BY priority DESC LIMIT ${idx}"

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._row_to_memory(row) for row in rows]

    async def async_update(self, memory_id: str, updates: dict) -> bool:
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
        idx = 1

        for key, value in updates.items():
            col = field_map.get(key)
            if col:
                set_clauses.append(f"{col} = ${idx}")
                params.append(value)
                idx += 1

        if not set_clauses:
            return False

        set_clauses.append(f"updated_at = ${idx}")
        params.append(datetime.now())
        idx += 1
        set_clauses.append("version = version + 1")

        params.append(memory_id)
        sql = f"UPDATE {t} SET {', '.join(set_clauses)} WHERE id = ${idx}"

        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, *params)
        return result != "UPDATE 0"

    async def async_delete(self, memory_id: str) -> bool:
        t = self.table_name
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM {t} WHERE id = $1", memory_id
            )
        return result != "DELETE 0"

    async def async_count(self, include_archived: bool = False) -> int:
        t = self.table_name
        async with self._pool.acquire() as conn:
            if include_archived:
                row = await conn.fetchrow(f"SELECT COUNT(*) as cnt FROM {t}")
            else:
                row = await conn.fetchrow(
                    f"SELECT COUNT(*) as cnt FROM {t} WHERE archived = FALSE"
                )
        return row["cnt"]

    # === Sync interface（NotImplementedError） ===

    def save(self, memory: Memory) -> bool:
        raise NotImplementedError("Use async_save for AsyncPostgreSQLPersistence")

    def load(self, memory_id: str) -> Memory | None:
        raise NotImplementedError("Use async_load for AsyncPostgreSQLPersistence")

    def load_all(self, include_archived: bool = False) -> list[Memory]:
        raise NotImplementedError("Use async_load_all for AsyncPostgreSQLPersistence")

    def search(
        self,
        keyword: str | None = None,
        layer: int | None = None,
        priority_min: float = 0.0,
        context: str | None = None,
        limit: int = 100,
    ) -> list[Memory]:
        raise NotImplementedError("Use async_search for AsyncPostgreSQLPersistence")

    def update(self, memory_id: str, updates: dict) -> bool:
        raise NotImplementedError("Use async_update for AsyncPostgreSQLPersistence")

    def delete(self, memory_id: str) -> bool:
        raise NotImplementedError("Use async_delete for AsyncPostgreSQLPersistence")

    def count(self, include_archived: bool = False) -> int:
        raise NotImplementedError("Use async_count for AsyncPostgreSQLPersistence")

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
            content_hash=row.get("content_hash") or "",
            pinned=row["pinned"],
            archived=row["archived"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_accessed_at=row["last_accessed_at"],
            access_count=row["access_count"],
            embedding=bytes(row["embedding"]) if row.get("embedding") else None,
            score_version=row.get("score_version") or 1,
            version=row["version"],
        )
