"""test_diagnostics_health.py — memory_diagnostics 拡張（CYCLE19.6 / A4）

目的は「利用者がAIに『記憶ストアの調子は？』と聞けば健康状態が返る」こと。
新しいCLIは作らず、既にCore 19ツールに入っている `memory_diagnostics` に足す。

このテストが守るのは3つ:
1. **Core利用者に、存在しない機能の数字を見せない**（昇格統計はEnterpriseだけ）
2. **測れていないものを「悪い」と言わない**（判定はデータが足りているときだけ）
3. **集計を書き直していない**（19.2〜19.5で作ったものを呼ぶだけ・読み込みは1回）
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


def _make_env(tmp_path, org_enabled: bool):
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
    server_module._system = system
    server_module._valve = IntegratedSearchValve(
        search_engine=SearchEngine(),
        cognitive_load=CognitiveLoadManager(7),
        org_client=None,
        personal_bonus=20,
    )
    server_module._event_logger = EventLogger(persistence.conn)
    server_module._config = CycleGenConfig(org_server_enabled=org_enabled)
    return system, server_module._event_logger, persistence


@pytest.fixture
def core_env(tmp_path):
    """Core構成（Org無効）。"""
    system, logger, persistence = _make_env(tmp_path, org_enabled=False)
    yield system, logger
    persistence.close()
    server_module._system = None
    server_module._valve = None
    server_module._event_logger = None
    server_module._config = None


@pytest.fixture
def enterprise_env(tmp_path):
    """Enterprise構成（Org有効）。"""
    system, logger, persistence = _make_env(tmp_path, org_enabled=True)
    yield system, logger
    persistence.close()
    server_module._system = None
    server_module._valve = None
    server_module._event_logger = None
    server_module._config = None


def _seed(system, event_logger, *, searches: int = 60):
    """返却の実績がある状態を作る（判定が出る規模）。"""
    idle = system.store(content="返るのに使われない記憶", layer=3)
    used = system.store(content="使われる記憶", layer=3)
    never = system.store(content="一度も返らない記憶", layer=3)
    for _ in range(searches):
        event_logger.log(EventType.SEARCH, details={"recalled_ids": [idle.id, used.id]})
    for _ in range(searches // 2):
        event_logger.log(EventType.RECALL_USED, used.id, {"session_id": "s1"})
    return idle, used, never


class TestCoreDoesNotShowEnterpriseNumbers:
    """受入条件1: Core構成で昇格・Org項目が出ない。"""

    async def test_promotion_block_hidden_in_core(self, core_env):
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics

        result = await memory_diagnostics()
        assert "昇格統計" not in result
        assert "昇格回数" not in result

    async def test_promotion_block_shown_in_enterprise(self, enterprise_env):
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics

        result = await memory_diagnostics()
        assert "昇格統計" in result
        assert "昇格回数" in result


class TestEmptyStore:
    """受入条件2: 記憶0件でもエラーにならず、意味のある表示になる。"""

    async def test_no_crash_and_no_judgement(self, core_env):
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics

        result = await memory_diagnostics()
        assert "3次元記憶 診断レポート" in result
        assert "記憶の健康状態" in result
        # 測れていないものを「悪い」と言わない
        assert "まだ判定できません" in result
        assert "🔴" not in result

    async def test_new_user_is_not_told_they_are_unhealthy(self, core_env):
        """記憶を書き始めたばかりでも赤信号を出さない。"""
        system, logger = core_env
        for i in range(5):
            system.store(content=f"書いたばかりの記憶{i}", layer=3)
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics

        result = await memory_diagnostics()
        assert "未返却率: 100.0%" in result  # 数値は正直に出す
        assert "🔴" not in result  # が、判定はしない


class TestHealthSection:
    async def test_unreturned_ratio_counts_memories_never_recalled(self, core_env):
        """「未返却」は「未利用（access_count=0）」とは別物。

        返ったうえで使われなかった記憶と、そもそも返っていない記憶を混ぜない。
        """
        system, logger = core_env
        _seed(system, logger)
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics

        result = await memory_diagnostics()
        # 3件中2件が返却経験あり → 未返却率 33.3%
        assert "未返却率: 33.3%" in result
        assert "2/3件は返却経験あり" in result

    async def test_concentration(self, core_env):
        system, logger = core_env
        _seed(system, logger)
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics

        result = await memory_diagnostics()
        assert "返却集中度: 上位1件が全返却スロットの50.0%を占有" in result

    async def test_dismiss_rate_denominator_is_searches(self, core_env):
        """既存の boost率（フィードバック内訳）と混同しないよう、分母を明示する。"""
        system, logger = core_env
        _seed(system, logger, searches=100)
        logger.log(EventType.DISMISS, "x", {"new_priority": 0.4})
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics

        result = await memory_diagnostics()
        assert "dismiss率: 1.00%（検索1回あたり）" in result

    async def test_capture_rate_is_shown(self, core_env):
        system, logger = core_env
        _seed(system, logger)
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics

        result = await memory_diagnostics()
        assert "mark_used捕捉率: 25.0%" in result

    async def test_idle_and_archive_counts(self, core_env):
        """19.5（空振り常連）と19.4（archive候補）の集計をそのまま出す。"""
        system, logger = core_env
        idle, _, _ = _seed(system, logger)
        sunk = system.store(content="沈んだ記憶", layer=3, priority=0.1)
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics

        result = await memory_diagnostics()
        assert "空振り常連: 1件" in result
        assert "archive候補: 1件" in result

    async def test_judgement_appears_when_data_is_enough(self, core_env):
        system, logger = core_env
        _seed(system, logger)
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics

        result = await memory_diagnostics()
        assert "まだ判定できません" not in result
        assert "🔴" in result or "🟡" in result or "🟢" in result


class TestEmbeddingProvenance:
    """CYCLE19.2 で入れた embedding_model 列の使いどころ（NULLのときに効く）。"""

    async def test_all_unknown(self, core_env):
        system, logger = core_env
        system.store(content="モデル未記録の記憶", layer=3)
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics

        result = await memory_diagnostics()
        assert "全1件がモデル未記録" in result

    async def test_mixed_models_warn(self, core_env):
        """モデルが混ざっている＝保存済みとクエリが別空間になっている可能性。"""
        system, logger = core_env
        a = system.store(content="記憶A", layer=3)
        b = system.store(content="記憶B", layer=3)
        system.persistence.update(a.id, {"embedding_model": "modelX@fastembed0.5.1"})
        system.persistence.update(b.id, {"embedding_model": "modelX@fastembed0.6.0"})
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics

        result = await memory_diagnostics()
        assert "embeddingのモデルが混在しています" in result
        assert "memory_reembed" in result


class TestReuseNotRewrite:
    """受入条件3: 19.2〜19.5の集計を再利用している（同じロジックを二重に書かない）。"""

    async def test_memories_are_loaded_only_once(self, core_env, monkeypatch):
        """collector・空振り常連・archive候補が別々に読み込むと3倍の時間がかかる。"""
        system, logger = core_env
        _seed(system, logger)

        # SQLiteバックエンドでは async_load_all が同期の load_all に委譲するので、
        # 実際にストアを読んだ回数はこちらで数えられる。
        calls = []
        original = system.persistence.load_all

        def _counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(system.persistence, "load_all", _counting)

        from cyclegen.mcp.tools.diagnostics import memory_diagnostics

        await memory_diagnostics()
        assert len(calls) == 1, f"記憶の読み込みが{len(calls)}回走っている（1回であるべき）"
