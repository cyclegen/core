"""test_cycle_complete_used_ids.py — mark_used捕捉改善（CYCLE19.3 / A6 / FR035 方向3）

背景（CYCLE19の実測）:
mark_usedの捕捉率は 736/7,411 = 9.9% しかなかった。
うち既存のFR008自動推定（summary本文のID文字列をregex検出）は
736件中1件（0.14%）しか発火していない——ダイジェストにIDを書く運用が無いため。

捕捉率が低いと何が困るか:
「返却されたのにmark_usedされなかった＝的外れ」という判定が使えなくなる。
捕捉率9.9%のまま素朴に判定すると返却の90%が的外れ扱いになり、
使えていた記憶まで沈む（CYCLE19 知見1-C）。
捕捉率が上がるほど空振り常連の閾値Nが下がり、届く記憶が増える。

このテストが守るのは「捕捉が増えること」ではなく
「捕捉の出所が区別されたまま残ること」である。
明示と推定を混ぜて1つの数字にすると、そこから導く閾値が信用できなくなる。
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
    """MCPツールを実システムで動かす環境（既存 test_tools.py と同じ組み立て）。"""
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
    # Core構成（Org無効）で回す。昇格ブロックはこの分岐に入らない。
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


def _used_events(event_logger):
    """RECALL_USED を {memory_id: source} で返す。source未指定は "explicit"。"""
    return {
        e.memory_id: (e.details.get("source") or "explicit")
        for e in event_logger.get_events(event_type=EventType.RECALL_USED, since_days=1)
    }


class TestExplicitCapture:
    async def test_used_memory_ids_recorded_as_explicit(self, env):
        """渡したIDが source="cycle_complete_explicit" で記録される。

        本文にIDが1つも出てこなくても捕捉できることが方向3の要点。
        既存のregex検出はここで必ず取りこぼしていた。
        """
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        a = system.store(content="使った記憶A", layer=3, context="implementation")
        b = system.store(content="使った記憶B", layer=3, context="implementation")

        result = await cycle_complete(
            summary="IDを本文に書かない要約",
            cycle_id="T1",
            used_memory_ids=[a.id, b.id],
        )

        used = _used_events(logger)
        assert used == {
            a.id: "cycle_complete_explicit",
            b.id: "cycle_complete_explicit",
        }
        assert "明示2件" in result

    async def test_no_double_count_with_regex(self, env):
        """本文にもIDが出る場合、明示を優先して二重計上しない。

        FR035 §2「同一mem_idが両方で挙がったら明示を優先」。
        二重に数えると捕捉率が実態より高く見え、そこから導く閾値が甘くなる。
        """
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        a = system.store(content="両方に出る記憶", layer=3, context="implementation")

        await cycle_complete(
            summary=f"本文に {a.id} と書いてある",
            cycle_id="T2",
            used_memory_ids=[a.id],
        )

        events = logger.get_events(event_type=EventType.RECALL_USED, since_days=1)
        assert len(events) == 1, "同じIDが2回記録されてはいけない"
        assert events[0].details.get("source") == "cycle_complete_explicit"

    async def test_regex_still_covers_ids_not_passed_explicitly(self, env):
        """明示に含まれないIDは、従来どおり本文検出で補完される。"""
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        a = system.store(content="明示で渡す", layer=3, context="implementation")
        b = system.store(content="本文にだけ出る", layer=3, context="implementation")

        await cycle_complete(
            summary=f"本文に {b.id} が出る",
            cycle_id="T3",
            used_memory_ids=[a.id],
        )

        used = _used_events(logger)
        assert used[a.id] == "cycle_complete_explicit"
        assert used[b.id] == "cycle_complete_auto"

    async def test_unknown_id_is_reported_not_swallowed(self, env):
        """存在しないIDは黙って捨てず、応答で知らせる。

        呼び出し側がIDを取り違えたまま「記録された」と思い込むと、
        捕捉率の数字だけが正しく見えて中身が空になる。
        """
        _, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        bogus = "mem_20991231_235959_ffffffff"
        result = await cycle_complete(
            summary="要約", cycle_id="T4", used_memory_ids=[bogus]
        )

        assert _used_events(logger) == {}
        assert "見つからないID" in result
        assert bogus in result

    async def test_omitted_argument_is_backward_compatible(self, env):
        """引数を渡さない既存の呼び方でも壊れない（従来の本文検出のみ）。"""
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        a = system.store(content="本文にだけ出る", layer=3, context="implementation")

        result = await cycle_complete(summary=f"本文に {a.id}", cycle_id="T5")

        assert _used_events(logger) == {a.id: "cycle_complete_auto"}
        assert "ヒント" in result, "渡していないときは使い方を案内する"

    async def test_duplicate_ids_counted_once(self, env):
        """同じIDを2回渡しても1件。呼び出し側のうっかりで数字が膨らまない。"""
        system, logger = env
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        a = system.store(content="重複して渡される", layer=3, context="implementation")

        result = await cycle_complete(
            summary="要約", cycle_id="T6", used_memory_ids=[a.id, a.id]
        )

        assert len(logger.get_events(event_type=EventType.RECALL_USED, since_days=1)) == 1
        assert "明示1件" in result


class TestSourceBreakdown:
    def test_collector_splits_recall_used_by_source(self, env):
        """捕捉の出所が区別されたまま集計される。

        明示と推定を混ぜて1つの数字にすると、そこから導く
        「空振り常連」の閾値が信用できなくなる。
        """
        system, logger = env
        from cyclegen.monitoring.collector import DiagnosticsCollector

        a = system.store(content="直接呼び出し", layer=3, context="implementation")
        b = system.store(content="明示引数", layer=3, context="implementation")
        c = system.store(content="本文検出", layer=3, context="implementation")

        logger.log(EventType.RECALL_USED, a.id, {})
        logger.log(EventType.RECALL_USED, b.id, {"source": "cycle_complete_explicit"})
        logger.log(EventType.RECALL_USED, c.id, {"source": "cycle_complete_auto"})

        report = DiagnosticsCollector(logger, system.persistence).collect()

        by_source = report.precision_stats.recall_used_by_source
        assert by_source["explicit"] == 1, "source未指定は直接呼び出しとして数える"
        assert by_source["cycle_complete_explicit"] == 1
        assert by_source["cycle_complete_auto"] == 1
