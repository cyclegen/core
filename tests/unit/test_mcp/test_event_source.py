"""test_event_source.py — 操作の出所を記録する（CYCLE20.5 / FR062①-a）

背景（CYCLE19.7の実測）:
CYCLE19.4のarchive候補提示は母艦の実データでは1件も発火しない設計だったので、
検証用の記憶を1件作り dismiss を5回呼んで実発火を確認した。
その5回だけで母艦の **dismiss率が 0.00%🔴 → 1.56%🟡 と判定色まで動いた**。
記憶を消してもイベントログは残るので（これ自体は正しい設計）、
もう存在しない記憶へのdismissが健康状態に永久に混ざっている。

> 機能を確かめる操作と、機能が測っている操作が、同じ器を共有している。

**掃除は利用ではない。** 空振り常連12件をまとめてdismissすると、
それだけで「利用者が活発にフィードバックしている」ように見える。

★ここで入れるのは**記録だけ**（①-a）。表示の分割（①-b）と計測ウィンドウ（②）はMS2。
記録は遡って付けられないので先に出す。表示はいつでも変えられる。
"""

from __future__ import annotations

import json

import pytest

import cyclegen.mcp.server as server_module
from cyclegen.config import DEFAULT_CONTEXTS
from cyclegen.core.classifier import AutoLayerClassifier
from cyclegen.core.context import ContextSelector
from cyclegen.core.layer import LayerHierarchy
from cyclegen.core.memory_system import MemorySystem3D
from cyclegen.core.priority import PriorityManager
from cyclegen.mcp import event_source
from cyclegen.models import ContextDefinition, CycleGenConfig, EventType
from cyclegen.monitoring.event_log import EventLogger
from cyclegen.persistence.md_sqlite import MdWithSQLitePersistence
from cyclegen.search.cognitive_load import CognitiveLoadManager
from cyclegen.search.engine import SearchEngine
from cyclegen.search.valve import IntegratedSearchValve


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Core構成（Org無効）でMCPツールを実システムのまま動かす。"""
    monkeypatch.delenv(event_source.ENV_VAR, raising=False)
    event_source.reset()

    persistence = MdWithSQLitePersistence(tmp_path)
    contexts = {
        name: ContextDefinition(**defn) for name, defn in DEFAULT_CONTEXTS.items()
    }
    system = MemorySystem3D(
        persistence=persistence,
        layer_hierarchy=LayerHierarchy(),
        priority_manager=PriorityManager(),
        context_selector=ContextSelector(contexts),
        classifier=AutoLayerClassifier(),
        search_engine=SearchEngine(),
    )
    valve = IntegratedSearchValve(
        search_engine=SearchEngine(),
        cognitive_load=CognitiveLoadManager(7),
        org_client=None,
        personal_bonus=20,
    )
    event_logger = EventLogger(persistence.conn)

    server_module._system = system
    server_module._valve = valve
    server_module._event_logger = event_logger
    server_module._config = CycleGenConfig(org_server_enabled=False)

    from cyclegen.mcp.session import reset_session_id

    reset_session_id()
    yield system, event_logger, persistence
    reset_session_id()
    event_source.reset()
    persistence.close()
    server_module._system = None
    server_module._valve = None
    server_module._event_logger = None
    server_module._config = None


def last_details(persistence, event_type: EventType) -> dict:
    """その種別の最後のイベントの details を返す。"""
    row = persistence.conn.execute(
        "SELECT details FROM event_log WHERE event_type = ? ORDER BY id DESC LIMIT 1",
        (event_type.value,),
    ).fetchone()
    assert row is not None, f"{event_type.value} のイベントが記録されていない"
    return json.loads(row["details"]) if row["details"] else {}


class TestResolve:
    """判定そのもの（優先順位）。"""

    def test_default_is_explicit(self):
        assert event_source.resolve("mem_x") == (event_source.SOURCE_EXPLICIT, None)

    def test_requested_value_is_honored(self):
        assert event_source.resolve("mem_x", "maintenance")[0] == "maintenance"

    def test_unknown_value_falls_back_and_warns(self):
        """黙って捨てない。呼び出し側の取り違えに気づけるようにする。"""
        resolved, warning = event_source.resolve("mem_x", "そうじ")
        assert resolved == event_source.SOURCE_EXPLICIT
        assert warning is not None and "そうじ" in warning

    def test_env_declaration_wins(self, monkeypatch):
        """プロセス全体を検証中と宣言したら、そのプロセスがやることは全部検証。"""
        monkeypatch.setenv(event_source.ENV_VAR, "verification")
        assert event_source.resolve("mem_x", "explicit")[0] == "verification"

    def test_invalid_env_is_ignored(self, monkeypatch):
        monkeypatch.setenv(event_source.ENV_VAR, "yes")
        assert event_source.resolve("mem_x")[0] == event_source.SOURCE_EXPLICIT

    def test_suggested_id_becomes_maintenance(self):
        event_source.reset()
        event_source.note_suggested(["mem_a"])
        assert event_source.resolve("mem_a")[0] == event_source.SOURCE_MAINTENANCE
        assert event_source.resolve("mem_b")[0] == event_source.SOURCE_EXPLICIT
        event_source.reset()

    def test_explicit_argument_overrides_suggestion(self):
        """提示を見て「これは確かに要らない」と判断した場合は言い切れる。"""
        event_source.reset()
        event_source.note_suggested(["mem_a"])
        assert event_source.resolve("mem_a", "explicit")[0] == "explicit"
        event_source.reset()


class TestToolsRecordSource:
    """dismiss / boost / archive の3本（受入条件1）。"""

    async def test_dismiss_records_explicit_by_default(self, env):
        system, _, persistence = env
        from cyclegen.mcp.tools.memory import memory_dismiss

        memory = system.store(content="対象の記憶", layer=3)
        await memory_dismiss(memory.id)

        assert last_details(persistence, EventType.DISMISS)["source"] == "explicit"

    async def test_boost_records_source(self, env):
        system, _, persistence = env
        from cyclegen.mcp.tools.memory import memory_boost

        memory = system.store(content="対象の記憶", layer=3)
        await memory_boost(memory.id, source="verification")

        details = last_details(persistence, EventType.BOOST)
        assert details["source"] == "verification"
        # 既存の記録（new_priority）を落としていない
        assert "new_priority" in details

    async def test_archive_records_source(self, env):
        system, _, persistence = env
        from cyclegen.mcp.tools.memory import memory_archive

        memory = system.store(content="対象の記憶", layer=3)
        await memory_archive(memory.id, source="maintenance")

        assert last_details(persistence, EventType.ARCHIVE)["source"] == "maintenance"

    async def test_unknown_source_is_reported_to_the_caller(self, env):
        system, _, persistence = env
        from cyclegen.mcp.tools.memory import memory_dismiss

        memory = system.store(content="対象の記憶", layer=3)
        result = await memory_dismiss(memory.id, source="そうじ")

        assert "未知のsource" in result
        assert last_details(persistence, EventType.DISMISS)["source"] == "explicit"

    async def test_verification_run_marks_everything(self, env, monkeypatch):
        """CYCLE19.7の実発火確認は、この宣言があれば指標に混ざらなかった。"""
        system, _, persistence = env
        from cyclegen.mcp.tools.memory import memory_dismiss

        monkeypatch.setenv(event_source.ENV_VAR, "verification")
        memory = system.store(content="検証用の記憶", layer=3)
        for _ in range(5):
            await memory_dismiss(memory.id)

        assert last_details(persistence, EventType.DISMISS)["source"] == "verification"


class TestCycleCompleteSuggestionBecomesMaintenance:
    """受入条件4: 提示から実行した掃除は自動で maintenance になる。"""

    def _seed_idle(self, system, event_logger, *, recalls=60, used_times=30):
        idle = system.store(content="繰り返し返るのに使われない記憶", layer=3)
        used = system.store(content="実際に使われる記憶", layer=3)
        for _ in range(recalls):
            event_logger.log(
                EventType.SEARCH, details={"recalled_ids": [idle.id, used.id]}
            )
        for _ in range(used_times):
            event_logger.log(EventType.RECALL_USED, used.id, {"session_id": "s1"})
        return idle, used

    async def test_dismiss_after_presentation_is_maintenance(self, env):
        system, logger, persistence = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete
        from cyclegen.mcp.tools.memory import memory_dismiss

        idle, _used = self._seed_idle(system, logger)
        presented = await cycle_complete(summary="要約", cycle_id="T1")
        assert idle.id in presented

        await memory_dismiss(idle.id)

        assert last_details(persistence, EventType.DISMISS)["source"] == "maintenance"

    async def test_dismiss_of_other_memory_stays_explicit(self, env):
        """提示に載っていない記憶へのdismissは、いつもどおり利用者の判断。"""
        system, logger, persistence = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete
        from cyclegen.mcp.tools.memory import memory_dismiss

        _idle, used = self._seed_idle(system, logger)
        await cycle_complete(summary="要約", cycle_id="T1")

        await memory_dismiss(used.id)

        assert last_details(persistence, EventType.DISMISS)["source"] == "explicit"

    async def test_archive_after_presentation_is_maintenance(self, env):
        """3択のうち「片付ける」を選んだ場合も掃除である。"""
        system, logger, persistence = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete
        from cyclegen.mcp.tools.memory import memory_archive

        idle, _used = self._seed_idle(system, logger)
        await cycle_complete(summary="要約", cycle_id="T1")

        await memory_archive(idle.id)

        assert last_details(persistence, EventType.ARCHIVE)["source"] == "maintenance"

    async def test_nothing_presented_means_nothing_marked(self, env):
        """候補が出ていないのに maintenance が付いたら、それは推測である。"""
        system, logger, persistence = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete
        from cyclegen.mcp.tools.memory import memory_dismiss

        memory = system.store(content="ふつうの記憶", layer=3)
        result = await cycle_complete(summary="要約", cycle_id="T1")
        assert "空振り常連" not in result

        await memory_dismiss(memory.id)

        assert last_details(persistence, EventType.DISMISS)["source"] == "explicit"


class TestDoesNotBackfill:
    """受入条件3: source の無い既存イベントを explicit として扱わない。"""

    async def test_old_events_keep_no_source(self, env):
        """CYCLE20.5より前のイベントは source を持たない。書き換えない。

        埋めると「記録がある」と誤認され、
        本当に壊れたときに検知できなくなる（CYCLE19.2 A8の規律）。
        """
        system, logger, persistence = env
        from cyclegen.mcp.tools.memory import memory_dismiss

        memory = system.store(content="対象の記憶", layer=3)
        # 旧版が書いたイベント（sourceが無い）
        logger.log(EventType.DISMISS, memory.id, {"new_priority": 0.4})
        old_id = persistence.conn.execute(
            "SELECT MAX(id) AS id FROM event_log"
        ).fetchone()["id"]

        await memory_dismiss(memory.id)

        old = persistence.conn.execute(
            "SELECT details FROM event_log WHERE id = ?", (old_id,)
        ).fetchone()
        assert "source" not in json.loads(old["details"])

    def test_module_has_no_migration(self):
        """出所の判定は、これから発行するイベントにしか関与しない。"""
        import inspect

        source_code = inspect.getsource(event_source)
        for forbidden in ("UPDATE event_log", "INSERT INTO event_log"):
            assert forbidden not in source_code
