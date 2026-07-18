"""persistence/postgresql.py — PostgreSQL永続化（Org Layer）

実装計画書§6.3 / 設計書§4.2:
asyncpg接続プール + org_memories / promotion_log / event_log テーブル。
リモートMCPサーバー（remote/app.py）から利用される。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from cyclegen.models import (
    Coordinates,
    OrgMemory,
    PromotionLog,
    PromotionReason,
    StorageTier,
)
from cyclegen.persistence.base import PersistenceAdapter

# asyncpgはオプション依存（[remote]インストール時のみ利用可能）
try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore[assignment]


class PostgreSQLPersistence(PersistenceAdapter):
    """PostgreSQL永続化（Org Layer用、非同期）。

    PersistenceAdapter ABCは同期インターフェースだが、
    Org Layer（リモートサーバー）は非同期で動作するため、
    async版メソッドを提供する。同期メソッドは NotImplementedError。
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool: Any = None  # asyncpg.Pool

    async def init_pool(self) -> None:
        """コネクションプールを初期化する。"""
        if asyncpg is None:
            raise RuntimeError("asyncpg is required. Install with: pip install cyclegen[remote]")
        self._pool = await asyncpg.create_pool(dsn=self.dsn, min_size=2, max_size=10)

    async def close_pool(self) -> None:
        """コネクションプールを閉じる。"""
        if self._pool:
            await self._pool.close()

    async def init_tables(self) -> None:
        """テーブルが存在しなければ作成する。"""
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS org_memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    layer INTEGER NOT NULL,
                    priority REAL NOT NULL,
                    context TEXT NOT NULL,
                    tags TEXT DEFAULT '',
                    owner_id TEXT DEFAULT '',
                    agent_id TEXT,
                    pinned BOOLEAN DEFAULT FALSE,
                    archived BOOLEAN DEFAULT FALSE,
                    promoted_at TIMESTAMPTZ,
                    promoted_by TEXT,
                    promotion_reason TEXT,
                    storage_tier TEXT DEFAULT 'hot',
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    access_count INTEGER DEFAULT 0,
                    version INTEGER DEFAULT 1
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS promotion_log (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    promoted_by TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    promoted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS event_log (
                    id SERIAL PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    memory_id TEXT,
                    details JSONB DEFAULT '{}',
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # インデックス
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_org_layer ON org_memories(layer)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_org_priority ON org_memories(priority DESC)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_org_context ON org_memories(context)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_org_3d ON org_memories(layer, priority DESC, context)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_org_not_archived ON org_memories(archived) WHERE archived = FALSE")

    # === Async CRUD ===

    async def async_save(self, memory: OrgMemory) -> bool:
        """OrgMemoryを保存する。"""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO org_memories
                    (id, content, layer, priority, context, tags, owner_id, agent_id,
                     pinned, archived, promoted_at, promoted_by, promotion_reason,
                     storage_tier, metadata, created_at, updated_at, last_accessed_at,
                     access_count, version)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
                ON CONFLICT(id) DO UPDATE SET
                    content=EXCLUDED.content, layer=EXCLUDED.layer, priority=EXCLUDED.priority,
                    context=EXCLUDED.context, tags=EXCLUDED.tags, agent_id=EXCLUDED.agent_id,
                    pinned=EXCLUDED.pinned, archived=EXCLUDED.archived,
                    storage_tier=EXCLUDED.storage_tier,
                    metadata=EXCLUDED.metadata, updated_at=EXCLUDED.updated_at,
                    last_accessed_at=EXCLUDED.last_accessed_at,
                    access_count=EXCLUDED.access_count, version=EXCLUDED.version
                """,
                memory.id, memory.content,
                memory.coordinates.layer, memory.coordinates.priority, memory.coordinates.context,
                ",".join(memory.tags), memory.owner_id, memory.agent_id,
                memory.pinned, memory.archived,
                memory.promoted_at, memory.promoted_by,
                memory.promotion_reason if memory.promotion_reason else None,
                memory.storage_tier.value,
                str(memory.metadata) if memory.metadata else "{}",
                memory.created_at, memory.updated_at, memory.last_accessed_at,
                memory.access_count, memory.version,
            )
        return True

    async def async_load(self, memory_id: str) -> OrgMemory | None:
        """IDでOrgMemoryを読み込む。"""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM org_memories WHERE id = $1", memory_id
            )
        if row is None:
            return None
        return self._row_to_org_memory(row)

    async def async_load_all(self, include_archived: bool = False) -> list[OrgMemory]:
        """全OrgMemoryを読み込む。"""
        async with self._pool.acquire() as conn:
            if include_archived:
                rows = await conn.fetch("SELECT * FROM org_memories ORDER BY priority DESC")
            else:
                rows = await conn.fetch(
                    "SELECT * FROM org_memories WHERE archived = FALSE ORDER BY priority DESC"
                )
        return [self._row_to_org_memory(row) for row in rows]

    async def async_search(
        self,
        keyword: str | None = None,
        layer: int | None = None,
        priority_min: float = 0.0,
        context: str | None = None,
        limit: int = 100,
    ) -> list[OrgMemory]:
        """条件に合致するOrgMemoryを検索する。"""
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
        query = f"SELECT * FROM org_memories WHERE {where} ORDER BY priority DESC LIMIT ${idx}"

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._row_to_org_memory(row) for row in rows]

    async def async_update(self, memory_id: str, updates: dict) -> bool:
        """フィールドを更新する。"""
        set_clauses = []
        params: list[Any] = []
        idx = 1

        field_map = {
            "content": "content",
            "coordinates.layer": "layer",
            "coordinates.priority": "priority",
            "coordinates.context": "context",
            "pinned": "pinned",
            "archived": "archived",
            "storage_tier": "storage_tier",
            "access_count": "access_count",
            "last_accessed_at": "last_accessed_at",
        }

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

        set_clauses.append(f"version = version + 1")

        params.append(memory_id)
        sql = f"UPDATE org_memories SET {', '.join(set_clauses)} WHERE id = ${idx}"

        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, *params)
        return result != "UPDATE 0"

    async def async_delete(self, memory_id: str) -> bool:
        """OrgMemoryを削除する。"""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM org_memories WHERE id = $1", memory_id
            )
        return result != "DELETE 0"

    async def async_count(self, include_archived: bool = False) -> int:
        """件数を返す。"""
        async with self._pool.acquire() as conn:
            if include_archived:
                row = await conn.fetchrow("SELECT COUNT(*) as cnt FROM org_memories")
            else:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) as cnt FROM org_memories WHERE archived = FALSE"
                )
        return row["cnt"]

    # === Promotion Log ===

    async def save_promotion_log(self, log: PromotionLog) -> bool:
        """昇格ログを保存する。"""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO promotion_log (id, memory_id, promoted_by, reason, promoted_at)
                   VALUES ($1, $2, $3, $4, $5)""",
                log.id, log.memory_id, log.promoted_by, log.reason, log.promoted_at,
            )
        return True

    async def get_promotion_logs(self, limit: int = 50) -> list[PromotionLog]:
        """昇格ログを取得する。"""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM promotion_log ORDER BY promoted_at DESC LIMIT $1", limit
            )
        return [
            PromotionLog(
                id=row["id"],
                memory_id=row["memory_id"],
                promoted_by=row["promoted_by"],
                reason=PromotionReason(row["reason"]),
                promoted_at=row["promoted_at"],
            )
            for row in rows
        ]

    async def get_memories_for_decay(self) -> list[OrgMemory]:
        """pinned=False かつ archived=False の全記憶を返す（日次バッチ用）。"""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM org_memories WHERE pinned = FALSE AND archived = FALSE"
            )
        return [self._row_to_org_memory(row) for row in rows]

    # === Sync interface (PersistenceAdapter ABC — raise for async-only) ===

    def save(self, memory):
        raise NotImplementedError("Use async_save for PostgreSQLPersistence")

    def load(self, memory_id):
        raise NotImplementedError("Use async_load for PostgreSQLPersistence")

    def load_all(self, include_archived=False):
        raise NotImplementedError("Use async_load_all for PostgreSQLPersistence")

    def search(self, keyword=None, layer=None, priority_min=0.0, context=None, limit=100):
        raise NotImplementedError("Use async_search for PostgreSQLPersistence")

    def update(self, memory_id, updates):
        raise NotImplementedError("Use async_update for PostgreSQLPersistence")

    def delete(self, memory_id):
        raise NotImplementedError("Use async_delete for PostgreSQLPersistence")

    def count(self, include_archived=False):
        raise NotImplementedError("Use async_count for PostgreSQLPersistence")

    # === Helper ===

    @staticmethod
    def _row_to_org_memory(row) -> OrgMemory:
        """DBレコード → OrgMemory 変換。"""
        tags = row["tags"].split(",") if row["tags"] else []
        return OrgMemory(
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
            pinned=row["pinned"],
            archived=row["archived"],
            promoted_at=row["promoted_at"],
            promoted_by=row["promoted_by"],
            promotion_reason=PromotionReason(row["promotion_reason"]) if row["promotion_reason"] else None,
            storage_tier=StorageTier(row["storage_tier"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_accessed_at=row["last_accessed_at"],
            access_count=row["access_count"],
            version=row["version"],
        )
