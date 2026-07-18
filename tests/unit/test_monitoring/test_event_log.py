"""test_event_log.py — EventLogger / AsyncEventLogger のユニットテスト"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from cyclegen.models import EventType
from cyclegen.monitoring.event_log import AsyncEventLogger, EventLogger


@pytest.fixture
def logger(tmp_path) -> EventLogger:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return EventLogger(conn)


class TestLog:
    def test_log_basic(self, logger):
        logger.log(EventType.STORE, "mem_001")
        events = logger.get_events()
        assert len(events) == 1
        assert events[0].event_type == EventType.STORE
        assert events[0].memory_id == "mem_001"

    def test_log_with_details(self, logger):
        logger.log(EventType.SEARCH, details={"query": "Python", "results_count": 3})
        events = logger.get_events()
        assert events[0].details["query"] == "Python"

    def test_log_without_memory_id(self, logger):
        logger.log(EventType.SEARCH, details={"query": "test"})
        events = logger.get_events()
        assert events[0].memory_id is None

    def test_log_multiple(self, logger):
        logger.log(EventType.STORE, "m1")
        logger.log(EventType.SEARCH, details={"query": "x"})
        logger.log(EventType.BOOST, "m1")
        events = logger.get_events()
        assert len(events) == 3


class TestGetEvents:
    def test_filter_by_type(self, logger):
        logger.log(EventType.STORE, "m1")
        logger.log(EventType.SEARCH)
        logger.log(EventType.BOOST, "m1")
        events = logger.get_events(event_type=EventType.STORE)
        assert len(events) == 1
        assert events[0].event_type == EventType.STORE

    def test_empty(self, logger):
        events = logger.get_events()
        assert events == []

    def test_all_event_types(self, logger):
        for et in EventType:
            logger.log(et, "m1")
        events = logger.get_events()
        assert len(events) == len(EventType)


class TestAsyncEventLoggerStructure:
    """AsyncEventLoggerの構造テスト（DB接続不要）"""

    def test_class_exists(self):
        """AsyncEventLoggerクラスが存在する"""
        assert AsyncEventLogger is not None

    def test_has_async_methods(self):
        """非同期メソッドが定義されている"""
        assert asyncio.iscoroutinefunction(AsyncEventLogger.init_table)
        assert asyncio.iscoroutinefunction(AsyncEventLogger.log)
        assert asyncio.iscoroutinefunction(AsyncEventLogger.get_events)

    def test_init_with_pool(self):
        """poolを受け取って初期化できる"""
        mock_pool = object()
        logger = AsyncEventLogger(pool=mock_pool)
        assert logger.pool is mock_pool
