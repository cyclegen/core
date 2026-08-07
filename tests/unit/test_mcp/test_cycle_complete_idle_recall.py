"""test_cycle_complete_idle_recall.py — 空振り常連の候補提示（CYCLE19.5 / A5-2）

Coreの `cycle_complete` は、これまで記録と案内文しか出していなかった
（昇格候補は `org_server_enabled: true` のときだけ）。
つまり **Core利用者には「捨てるべきものを見つける経路」が1つも無かった。**

ここで入れるのは昇格（上げる）と対称の、降格（下げる）のHITLゲート。
提示するだけで何も書き換えない。決めるのは人間。

このテストが守るのは「候補が出ること」ではなく
**「出してはいけない場面で出さないこと」**である。
新規利用者に剪定を勧めるのは、機能しているのではなく壊れている。
"""

from __future__ import annotations

import pytest

import cyclegen.mcp.server as server_module
from cyclegen.config import DEFAULT_CONTEXTS
from cyclegen.core.classifier import AutoLayerClassifier
from cyclegen.core.context import ContextSelector
from cyclegen.core.layer import LayerHierarchy
from cyclegen.core.memory_system import MemorySystem3D
from cyclegen.core.priority import PriorityManager
from cyclegen.models import ContextDefinition, CycleGenConfig, EventType
from cyclegen.monitoring.event_log import EventLogger
from cyclegen.persistence.md_sqlite import MdWithSQLitePersistence
from cyclegen.search.cognitive_load import CognitiveLoadManager
from cyclegen.search.engine import SearchEngine
from cyclegen.search.valve import IntegratedSearchValve


@pytest.fixture
def env(tmp_path):
    """Core構成（Org無効）でMCPツールを実システムのまま動かす。"""
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
    yield system, event_logger
    reset_session_id()
    persistence.close()
    server_module._system = None
    server_module._valve = None
    server_module._event_logger = None
    server_module._config = None


def _seed_idle(system, event_logger, *, recalls: int = 60, used_times: int = 30):
    """空振り常連が1件できる状態を作る。捕捉率は used_times / (recalls*2)。"""
    idle = system.store(content="繰り返し返るのに使われない記憶", layer=3)
    used = system.store(content="実際に使われる記憶", layer=3)
    for _ in range(recalls):
        event_logger.log(EventType.SEARCH, details={"recalled_ids": [idle.id, used.id]})
    for _ in range(used_times):
        event_logger.log(EventType.RECALL_USED, used.id, {"session_id": "s1"})
    return idle, used


class TestPresentedInCore:
    """Coreの cycle_complete が初めて「判断の材料」を持つ。"""

    async def test_candidate_is_presented(self, env):
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        idle, used = _seed_idle(system, logger)
        result = await cycle_complete(summary="要約", cycle_id="T1")

        assert "空振り常連" in result
        assert idle.id in result
        assert used.id not in result

    async def test_threshold_and_capture_rate_are_shown(self, env):
        """受入条件4: 閾値が捕捉率から導出され、その値が提示文に出る。

        「なぜこの記憶が候補なのか」を利用者が検算できないと、
        判断を委ねたことにならない。
        """
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        _seed_idle(system, logger)  # 捕捉率 30/120 = 25% → N=11
        result = await cycle_complete(summary="要約", cycle_id="T1")

        assert "閾値: 返却11回以上" in result
        assert "25.0%" in result

    async def test_shows_occupied_ratio(self, env):
        """効果（返却スロットをどれだけ占めているか）を添える。"""
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        _seed_idle(system, logger)
        result = await cycle_complete(summary="要約", cycle_id="T1")
        assert "返却スロットの50.0%" in result

    async def test_offers_three_choices(self, env):
        """dismiss一択にしない（良い記憶を沈めるため）。"""
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        _seed_idle(system, logger)
        result = await cycle_complete(summary="要約", cycle_id="T1")

        assert "memory_dismiss" in result
        assert "分割" in result
        assert "保留" in result


class TestSilentWhenNothingToSay:
    async def test_empty_store_is_silent(self, env):
        """受入条件2: 記憶0件の環境で1件も出さず、エラーにもならない。"""
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        result = await cycle_complete(summary="要約", cycle_id="T1")
        assert "空振り常連" not in result
        assert "CYCLE完了記録" in result

    async def test_new_user_is_silent(self, env):
        """使い始めは返却スロットが薄い。空の庭に剪定の提案をしない。"""
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        _seed_idle(system, logger, recalls=10, used_times=5)
        result = await cycle_complete(summary="要約", cycle_id="T1")
        assert "空振り常連" not in result

    async def test_silent_when_no_mark_used(self, env):
        """mark_usedが0件なら全件が容疑者になる。判定に使わない。"""
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        _seed_idle(system, logger, recalls=100, used_times=0)
        result = await cycle_complete(summary="要約", cycle_id="T1")
        assert "空振り常連" not in result


class TestChangesNothing:
    """受入条件3: 提示だけで何も書き換えない。"""

    async def test_candidate_is_untouched(self, env):
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        idle, _ = _seed_idle(system, logger)
        before = system.persistence.load(idle.id)
        await cycle_complete(summary="要約", cycle_id="T1")
        after = system.persistence.load(idle.id)

        assert after.coordinates.priority == before.coordinates.priority
        assert after.tags == before.tags
        assert after.archived == before.archived

    async def test_no_dismiss_event_is_logged(self, env):
        """提示は記録でもない。dismissは人が呼ぶまで発生しない。"""
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        _seed_idle(system, logger)
        await cycle_complete(summary="要約", cycle_id="T1")

        dismissed = logger.get_events(event_type=EventType.DISMISS, since_days=1)
        assert dismissed == []


class TestCoexistsWithUsedMemoryIds:
    """19.3で入れた used_memory_ids と同じ呼び出しで両立する。"""

    async def test_used_ids_shrink_the_candidate_list(self, env):
        """このCYCLEで使ったと申告した記憶は、その場で候補から外れる。

        A6（捕捉改善）とA5-2（候補提示）が独立の施策ではないことの実地確認。
        """
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        idle, _ = _seed_idle(system, logger)

        before = await cycle_complete(summary="要約", cycle_id="T1")
        assert idle.id in before

        after = await cycle_complete(
            summary="要約", cycle_id="T2", used_memory_ids=[idle.id]
        )
        assert idle.id not in after
