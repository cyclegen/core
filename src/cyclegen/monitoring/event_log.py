"""monitoring/event_log.py — イベントログ

実装計画書§9 / 設計書§8 #8:
各操作時にevent_logテーブルに1行追記する。
SQLite（Personal Layer）またはPostgreSQL（Cloud）のevent_logテーブルを使用。

CYCLE7.7.3: asyncpg対応。AsyncEventLoggerクラス追加。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from cyclegen.models import EventLogEntry, EventType


class EventLogger:
    """各操作時にevent_logテーブルに1行追記する。

    sqlite3.Connection と psycopg2.connection の両方に対応。
    プレースホルダ: SQLite='?'、PostgreSQL='%s'。
    """

    def __init__(self, conn: Any):
        self.conn = conn
        self._is_pg = not isinstance(conn, sqlite3.Connection)
        self._ph = "%s" if self._is_pg else "?"
        self._init_table()

    def _init_table(self) -> None:
        if self._is_pg:
            cur = self.conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS event_log (
                    id SERIAL PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    memory_id TEXT,
                    details TEXT,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_type ON event_log(event_type)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_timestamp ON event_log(timestamp)"
            )
            self.conn.commit()
        else:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    memory_id TEXT,
                    details TEXT,
                    timestamp TEXT NOT NULL
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_type ON event_log(event_type)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_timestamp ON event_log(timestamp)"
            )
            self.conn.commit()

    def _execute(self, sql: str, params: tuple) -> Any:
        """DB種別に応じてexecuteする。"""
        if self._is_pg:
            cur = self.conn.cursor()
            cur.execute(sql, params)
            self.conn.commit()
            return cur
        else:
            result = self.conn.execute(sql, params)
            self.conn.commit()
            return result

    def log(
        self,
        event_type: EventType,
        memory_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        """イベントを記録する。"""
        entry = EventLogEntry(
            event_type=event_type,
            memory_id=memory_id,
            details=details or {},
        )
        ph = self._ph
        self._execute(
            f"INSERT INTO event_log (event_type, memory_id, details, timestamp) "
            f"VALUES ({ph}, {ph}, {ph}, {ph})",
            (
                entry.event_type.value,
                entry.memory_id,
                json.dumps(entry.details, ensure_ascii=False),
                entry.timestamp.isoformat(),
            ),
        )

    def get_events(
        self,
        event_type: EventType | None = None,
        since_days: int = 30,
    ) -> list[EventLogEntry]:
        """指定期間のイベントを取得する。"""
        since = (datetime.now() - timedelta(days=since_days)).isoformat()
        ph = self._ph

        if event_type:
            sql = (
                "SELECT event_type, memory_id, details, timestamp FROM event_log "
                f"WHERE event_type = {ph} AND timestamp >= {ph} ORDER BY timestamp DESC"
            )
            params = (event_type.value, since)
        else:
            sql = (
                "SELECT event_type, memory_id, details, timestamp FROM event_log "
                f"WHERE timestamp >= {ph} ORDER BY timestamp DESC"
            )
            params = (since,)

        if self._is_pg:
            cur = self.conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        else:
            rows = self.conn.execute(sql, params).fetchall()

        return [
            EventLogEntry(
                event_type=EventType(row[0]),
                memory_id=row[1],
                details=json.loads(row[2]) if row[2] else {},
                timestamp=datetime.fromisoformat(str(row[3])),
            )
            for row in rows
        ]


    # === Async wrappers（CYCLE7.7.3.1追加） ===
    # MCPツールが統一的にawaitできるように、同期版をラップする。

    async def async_log(
        self,
        event_type: EventType,
        memory_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        """イベントを記録する（asyncラッパー）。"""
        self.log(event_type, memory_id, details)

    async def async_get_events(
        self,
        event_type: EventType | None = None,
        since_days: int = 30,
    ) -> list[EventLogEntry]:
        """指定期間のイベントを取得する（asyncラッパー）。"""
        return self.get_events(event_type, since_days)


class AsyncEventLogger:
    """asyncpgプール対応のイベントロガー（CYCLE7.7.3）。

    asyncpg.Pool を受け取り、非同期でevent_logテーブルに書き込む。
    プレースホルダ: asyncpg='$N'。
    """

    def __init__(self, pool: Any):
        self.pool = pool

    async def init_table(self) -> None:
        """event_logテーブルを作成する。"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS event_log (
                    id SERIAL PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    memory_id TEXT,
                    details TEXT,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_type ON event_log(event_type)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_timestamp ON event_log(timestamp)"
            )

    async def log(
        self,
        event_type: EventType,
        memory_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        """イベントを記録する（非同期版）。"""
        entry = EventLogEntry(
            event_type=event_type,
            memory_id=memory_id,
            details=details or {},
        )
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO event_log (event_type, memory_id, details, timestamp) "
                "VALUES ($1, $2, $3, $4)",
                entry.event_type.value,
                entry.memory_id,
                json.dumps(entry.details, ensure_ascii=False),
                entry.timestamp,
            )

    async def get_events(
        self,
        event_type: EventType | None = None,
        since_days: int = 30,
    ) -> list[EventLogEntry]:
        """指定期間のイベントを取得する（非同期版）。"""
        since = datetime.now() - timedelta(days=since_days)

        async with self.pool.acquire() as conn:
            if event_type:
                rows = await conn.fetch(
                    "SELECT event_type, memory_id, details, timestamp FROM event_log "
                    "WHERE event_type = $1 AND timestamp >= $2 ORDER BY timestamp DESC",
                    event_type.value, since,
                )
            else:
                rows = await conn.fetch(
                    "SELECT event_type, memory_id, details, timestamp FROM event_log "
                    "WHERE timestamp >= $1 ORDER BY timestamp DESC",
                    since,
                )

        return [
            EventLogEntry(
                event_type=EventType(row["event_type"]),
                memory_id=row["memory_id"],
                details=json.loads(row["details"]) if row["details"] else {},
                timestamp=row["timestamp"],
            )
            for row in rows
        ]
