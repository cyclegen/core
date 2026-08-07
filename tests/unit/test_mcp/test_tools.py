"""test_tools.py — MCPツール11本のユニットテスト

MCPサーバーの遅延初期化をモック化し、各ツール関数を直接呼び出してテストする。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import cyclegen.mcp.server as server_module
from cyclegen.config import DEFAULT_CONTEXTS
from cyclegen.core.classifier import AutoLayerClassifier
from cyclegen.core.context import ContextSelector
from cyclegen.core.layer import LayerHierarchy
from cyclegen.core.memory_system import MemorySystem3D
from cyclegen.core.priority import PriorityManager
from cyclegen.models import ContextDefinition, CycleGenConfig
from cyclegen.monitoring.event_log import EventLogger
from cyclegen.persistence.md_sqlite import MdWithSQLitePersistence
from cyclegen.search.cognitive_load import CognitiveLoadManager
from cyclegen.search.engine import SearchEngine
from cyclegen.search.valve import IntegratedSearchValve


@pytest.fixture(autouse=True)
def setup_mcp_globals(tmp_path):
    """各テスト前にMCPグローバル状態を初期化する。"""
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
    server_module._config = CycleGenConfig()

    # CYCLE13.2 FR031 P1: テスト間でsession_idを持ち越さない
    from cyclegen.mcp.session import reset_session_id
    reset_session_id()

    yield

    reset_session_id()
    persistence.close()
    server_module._system = None
    server_module._valve = None
    server_module._event_logger = None
    server_module._config = None


# === memory_store ===

class TestMemoryStore:
    async def test_store_with_params(self):
        from cyclegen.mcp.tools.memory import memory_store
        result = await memory_store("方針確定", layer=4, context="planning", tags="test,memo")
        assert "記憶保存完了" in result
        assert "L4" in result
        assert "P0.50" in result
        assert "ID: mem_" in result

    async def test_store_3d_eval_feedback(self):
        """3軸省略時に3d-evalフィードバックが返る（FR004）"""
        from cyclegen.mcp.tools.memory import memory_store
        result = await memory_store("テスト記憶を保存する")
        assert "2軸評価リクエスト" in result
        assert "Layer" in result
        assert "再度呼んでください" in result

    async def test_store_layer_only_auto_context(self):
        """CYCLE12.8.2: layer指定+context省略は自動判定で保存成功"""
        from cyclegen.mcp.tools.memory import memory_store
        result = await memory_store("バグを修正する手順", layer=1)
        assert "記憶保存完了" in result

    async def test_store_undefined_context_warning(self):
        """CYCLE12.8.2 FR023: 未定義Contextは警告+自動補正"""
        from cyclegen.mcp.tools.memory import memory_store
        result = await memory_store("バグを修正する", layer=3, context="invalid_ctx")
        assert "記憶保存完了" in result
        assert "未定義" in result
        assert "自動補正" in result

    async def test_store_with_agent_id(self):
        from cyclegen.mcp.tools.memory import memory_store
        result = await memory_store("エージェントの記憶", layer=3, context="implementation", agent_id="agent-alpha")
        assert "記憶保存完了" in result


# === memory_search ===

class TestMemorySearch:
    async def test_search_empty(self):
        from cyclegen.mcp.tools.memory import memory_search
        result = await memory_search("何か")
        assert "0件" in result

    async def test_search_after_store(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_search
        await memory_store("Pythonでデータモデルを実装した", layer=3, context="implementation")
        result = await memory_search("Python")
        assert "検索結果:" in result
        assert "Python" in result

    async def test_search_with_filters(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_search
        await memory_store("テスト1", layer=2, context="implementation")
        await memory_store("テスト2", layer=4, context="planning")
        result = await memory_search("テスト", layer_filter="4")
        assert "検索結果:" in result

    async def test_search_shows_tags(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_search
        await memory_store("タグ付き記憶", tags="python,api", layer=3, context="implementation")
        result = await memory_search("タグ付き")
        assert "タグ: python, api" in result

    async def test_search_shows_pinned(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_search, memory_pin
        store_result = await memory_store("ピン留め表示テスト", layer=3, context="implementation")
        mem_id = [line for line in store_result.split("\n") if "ID:" in line][0].split("ID:")[1].strip()
        await memory_pin(mem_id)
        result = await memory_search("ピン留め表示")
        assert "ピン留め" in result

    async def test_search_shows_agent_id(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_search
        await memory_store("エージェント検索テスト", agent_id="agent-x", layer=3, context="implementation")
        result = await memory_search("エージェント検索")
        assert "agent: agent-x" in result

    async def test_search_footer(self):
        """検索結果にboost/dismiss/mark_used案内フッターが含まれる（FR007）"""
        from cyclegen.mcp.tools.memory import memory_store, memory_search
        await memory_store("フッターテスト用記憶", layer=3, context="implementation")
        result = await memory_search("フッター")
        assert "memory_boost" in result
        assert "memory_dismiss" in result
        assert "memory_mark_used" in result

    async def test_search_empty_no_footer(self):
        """検索結果0件時にフッターなし"""
        from cyclegen.mcp.tools.memory import memory_search
        result = await memory_search("存在しないクエリ")
        assert "0件" in result
        assert "memory_boost" not in result

    async def test_search_logs_recalled_ids(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_search
        import cyclegen.mcp.server as srv
        await memory_store("recalled_ids記録テスト", layer=3, context="implementation")
        await memory_search("recalled_ids記録")
        # event_logからSEARCHイベントを取得してrecalled_idsがあることを確認
        from cyclegen.models import EventType
        events = srv._event_logger.get_events(EventType.SEARCH)
        assert len(events) > 0
        assert "recalled_ids" in events[0].details
        assert len(events[0].details["recalled_ids"]) > 0

    async def test_search_logs_session_id(self):
        """SEARCHイベントにsession_idが記録される（CYCLE13.2 FR031 P1）"""
        from cyclegen.mcp.tools.memory import memory_store, memory_search
        import cyclegen.mcp.server as srv
        from cyclegen.models import EventType
        await memory_store("session_id記録テスト", layer=3, context="implementation")
        await memory_search("session_id記録")
        events = srv._event_logger.get_events(EventType.SEARCH)
        assert events[0].details.get("session_id", "").startswith("sess_")

    async def test_search_reuses_session_id(self):
        """同一プロセス内の複数検索は同じsession_idを共有する（CYCLE13.2 FR031 P1）"""
        from cyclegen.mcp.tools.memory import memory_store, memory_search
        import cyclegen.mcp.server as srv
        from cyclegen.models import EventType
        await memory_store("session継続テスト", layer=3, context="implementation")
        await memory_search("session継続1")
        await memory_search("session継続2")
        events = srv._event_logger.get_events(EventType.SEARCH)
        sids = {e.details.get("session_id") for e in events}
        assert len(sids) == 1
        assert next(iter(sids)).startswith("sess_")


# === memory_update ===

class TestMemoryUpdate:
    async def test_update_content(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_update
        store_result = await memory_store("元の内容", layer=3, context="implementation")
        # IDを抽出
        mem_id = [line for line in store_result.split("\n") if "ID:" in line][0].split("ID:")[1].strip()
        result = await memory_update(mem_id, content="新しい内容")
        assert "更新完了" in result

    async def test_update_nonexistent(self):
        from cyclegen.mcp.tools.memory import memory_update
        result = await memory_update("nonexistent", content="x")
        assert "エラー" in result


# === memory_delete ===

class TestMemoryDelete:
    async def test_delete(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_delete
        store_result = await memory_store("削除対象", layer=3, context="implementation")
        mem_id = [line for line in store_result.split("\n") if "ID:" in line][0].split("ID:")[1].strip()
        result = await memory_delete(mem_id)
        assert "削除完了" in result

    async def test_delete_nonexistent(self):
        from cyclegen.mcp.tools.memory import memory_delete
        result = await memory_delete("nonexistent")
        assert "エラー" in result


# === memory_pin ===

class TestMemoryPin:
    async def test_pin(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_pin
        store_result = await memory_store("ピン留め対象", layer=3, context="implementation")
        mem_id = [line for line in store_result.split("\n") if "ID:" in line][0].split("ID:")[1].strip()
        result = await memory_pin(mem_id)
        assert "ピン留め完了" in result

    async def test_pin_nonexistent(self):
        from cyclegen.mcp.tools.memory import memory_pin
        result = await memory_pin("nonexistent")
        assert "エラー" in result


# === memory_archive ===

class TestMemoryArchive:
    async def test_archive(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_archive
        store_result = await memory_store("アーカイブ対象", layer=3, context="implementation")
        mem_id = [line for line in store_result.split("\n") if "ID:" in line][0].split("ID:")[1].strip()
        result = await memory_archive(mem_id)
        assert "アーカイブ完了" in result

    async def test_archive_nonexistent(self):
        from cyclegen.mcp.tools.memory import memory_archive
        result = await memory_archive("nonexistent")
        assert "エラー" in result


# === memory_unarchive ===

class TestMemoryUnarchive:
    async def test_unarchive(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_archive, memory_unarchive
        store_result = await memory_store("復帰対象", layer=3, context="implementation")
        mem_id = [line for line in store_result.split("\n") if "ID:" in line][0].split("ID:")[1].strip()
        await memory_archive(mem_id)
        result = await memory_unarchive(mem_id)
        assert "アーカイブ解除完了" in result

    async def test_unarchive_nonexistent(self):
        from cyclegen.mcp.tools.memory import memory_unarchive
        result = await memory_unarchive("nonexistent")
        assert "エラー" in result


# === memory_boost ===

class TestMemoryBoost:
    async def test_boost(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_boost
        store_result = await memory_store("boost対象", layer=3, context="implementation")
        mem_id = [line for line in store_result.split("\n") if "ID:" in line][0].split("ID:")[1].strip()
        result = await memory_boost(mem_id)
        assert "boost完了" in result
        assert "0.60" in result  # 0.5 + 0.10

    async def test_boost_nonexistent(self):
        from cyclegen.mcp.tools.memory import memory_boost
        result = await memory_boost("nonexistent")
        assert "エラー" in result


# === memory_dismiss ===

class TestMemoryDismiss:
    async def test_dismiss(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_dismiss
        store_result = await memory_store("dismiss対象", layer=3, context="implementation")
        mem_id = [line for line in store_result.split("\n") if "ID:" in line][0].split("ID:")[1].strip()
        result = await memory_dismiss(mem_id)
        assert "dismiss完了" in result
        assert "0.40" in result  # 0.5 - 0.10

    async def test_dismiss_nonexistent(self):
        from cyclegen.mcp.tools.memory import memory_dismiss
        result = await memory_dismiss("nonexistent")
        assert "エラー" in result


# === memory_status ===

class TestMemoryStatus:
    async def test_status_empty(self):
        from cyclegen.mcp.tools.lifecycle import memory_status
        result = await memory_status()
        assert "CycleGen 3次元記憶ステータス" in result
        assert "Personal Layer: 0件" in result

    async def test_status_after_store(self):
        from cyclegen.mcp.tools.memory import memory_store
        from cyclegen.mcp.tools.lifecycle import memory_status
        await memory_store("テスト記憶", layer=3, context="implementation")
        result = await memory_status()
        assert "1件" in result


# === cycle_complete ===

class TestCycleComplete:
    async def test_cycle_complete_org_disabled(self):
        from cyclegen.mcp.tools.lifecycle import cycle_complete
        result = await cycle_complete("CYCLE完了報告書の内容", cycle_id="CYCLE4.7")
        assert "CYCLE完了記録" in result
        assert "意味単位に分割" in result
        assert "Org Layer: 無効" in result

    async def test_cycle_complete_without_id(self):
        from cyclegen.mcp.tools.lifecycle import cycle_complete
        result = await cycle_complete("報告書内容")
        assert "CYCLE完了記録" in result

    async def test_mark_used_auto_estimation(self):
        """summaryにmemory IDが含まれる場合にRECALL_USEDイベントが記録される（FR008）"""
        from cyclegen.mcp.tools.memory import memory_store
        from cyclegen.mcp.tools.lifecycle import cycle_complete
        from cyclegen.models import EventType

        r = await memory_store("自動推定テスト", layer=3, context="implementation")
        mem_id = [l for l in r.split("\n") if "ID:" in l][0].split("ID:")[1].strip()

        summary = f"## 成果\n記憶 {mem_id} を参照して作業した"
        result = await cycle_complete(summary, cycle_id="TEST-FR008")
        # CYCLE19.3(FR035 方向3)で出力形式が変わった: 明示と本文検出を分けて報告する
        assert "本文検出1件" in result

        events = server_module._event_logger.get_events(EventType.RECALL_USED)
        used_ids = [e.memory_id for e in events]
        assert mem_id in used_ids

        # CYCLE13.3サブ計画 第1手: 自動推定recall_usedにsession_id + sourceが付与される
        auto_ev = [e for e in events if e.memory_id == mem_id][0]
        assert auto_ev.details.get("session_id"), "自動推定にsession_idが付与されていない"
        assert auto_ev.details.get("source") == "cycle_complete_auto"

    async def test_mark_used_auto_no_ids(self):
        """summaryにIDがない場合はmark_used 0件"""
        from cyclegen.mcp.tools.lifecycle import cycle_complete
        result = await cycle_complete("IDなしの報告書", cycle_id="TEST-NO-ID")
        assert "明示0件 / 本文検出0件" in result

    async def test_mark_used_auto_nonexistent_id(self):
        """存在しないIDはスキップされる"""
        from cyclegen.mcp.tools.lifecycle import cycle_complete
        summary = "参照: mem_20260424_000000_deadbeef を使った"
        result = await cycle_complete(summary, cycle_id="TEST-NOEXIST")
        assert "明示0件 / 本文検出0件" in result

    async def test_hitl_no_qualifying(self):
        """Org有効だが基準を満たす記憶がない場合"""
        from unittest.mock import patch
        from cyclegen.mcp.tools.memory import memory_store
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        # L2/P0.3の記憶は候補にならない
        await memory_store("低優先度メモ", layer=2, context="implementation")

        server_module._config = CycleGenConfig(
            org_server_enabled=True,
            org_server_url="http://localhost:8100",
            org_api_key="test-key",
        )
        result = await cycle_complete("テスト報告書", cycle_id="TEST1")
        assert "昇格候補: 0件" in result

    async def test_hitl_suggests_qualifying(self):
        """基準を満たす記憶が候補として提示される（自動昇格しない）"""
        from cyclegen.mcp.tools.memory import memory_store
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        r = await memory_store("重要方針確定", layer=4, context="planning")
        mem_id = [l for l in r.split("\n") if "ID:" in l][0].split("ID:")[1].strip()
        system = server_module._system
        system.persistence.update(mem_id, {"access_count": 3, "coordinates.priority": 0.8})

        server_module._config = CycleGenConfig(
            org_server_enabled=True,
            org_server_url="http://localhost:8100",
            org_api_key="test-key",
        )
        result = await cycle_complete("テスト報告書", cycle_id="TEST2")

        assert "昇格候補" in result
        assert mem_id in result
        assert "promotion_approve" in result

        # pendingタグが付与されている
        updated = system.persistence.load(mem_id)
        assert "promotion:pending" in updated.tags

        # Org Layerには昇格されていない（promoted:orgがない）
        assert "promoted:org" not in updated.tags

    async def test_hitl_skips_import_tag(self):
        """import:*タグ付き記憶は候補にならない"""
        from cyclegen.mcp.tools.memory import memory_store
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        r = await memory_store("外部ドキュメント", layer=4, context="planning", tags="import:ref")
        mem_id = [l for l in r.split("\n") if "ID:" in l][0].split("ID:")[1].strip()
        system = server_module._system
        system.persistence.update(mem_id, {"access_count": 5, "coordinates.priority": 0.8})

        server_module._config = CycleGenConfig(
            org_server_enabled=True,
            org_server_url="http://localhost:8100",
            org_api_key="test-key",
        )
        result = await cycle_complete("報告書", cycle_id="TEST3")
        assert "昇格候補: 0件" in result

    async def test_hitl_pinned_auto_candidate(self):
        """pinned=trueはaccess_count不足でも候補になる"""
        from cyclegen.mcp.tools.memory import memory_store, memory_pin
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        r = await memory_store("ピン留め記憶", layer=2, context="implementation")
        mem_id = [l for l in r.split("\n") if "ID:" in l][0].split("ID:")[1].strip()
        await memory_pin(mem_id)

        server_module._config = CycleGenConfig(
            org_server_enabled=True,
            org_server_url="http://localhost:8100",
            org_api_key="test-key",
        )
        result = await cycle_complete("報告書", cycle_id="TEST4")

        assert "昇格候補" in result
        assert mem_id in result
        assert "pinned" in result

    async def test_hitl_pending_tag_attached(self):
        """候補にはpromotion:pendingタグが付与される"""
        from cyclegen.mcp.tools.memory import memory_store
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        r = await memory_store("昇格対象", layer=4, context="planning")
        mem_id = [l for l in r.split("\n") if "ID:" in l][0].split("ID:")[1].strip()
        system = server_module._system
        system.persistence.update(mem_id, {"access_count": 3, "coordinates.priority": 0.8})

        server_module._config = CycleGenConfig(
            org_server_enabled=True,
            org_server_url="http://localhost:8100",
            org_api_key="test-key",
        )
        await cycle_complete("報告書", cycle_id="TEST5")

        updated_mem = system.persistence.load(mem_id)
        assert "promotion:pending" in updated_mem.tags

    async def test_hitl_no_double_suggest(self):
        """promoted:orgタグ付き記憶は候補にならない"""
        from cyclegen.mcp.tools.memory import memory_store
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        r = await memory_store("既に昇格済み", layer=4, context="planning", tags="promoted:org")
        mem_id = [l for l in r.split("\n") if "ID:" in l][0].split("ID:")[1].strip()
        system = server_module._system
        system.persistence.update(mem_id, {"access_count": 5, "coordinates.priority": 0.8})

        server_module._config = CycleGenConfig(
            org_server_enabled=True,
            org_server_url="http://localhost:8100",
            org_api_key="test-key",
        )
        result = await cycle_complete("報告書", cycle_id="TEST6")
        assert "昇格候補: 0件" in result

    async def test_hitl_pending_not_re_suggested(self):
        """既にpendingの記憶は新規候補として重複しない"""
        from cyclegen.mcp.tools.memory import memory_store
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        r = await memory_store("既にpending", layer=4, context="planning", tags="promotion:pending")
        mem_id = [l for l in r.split("\n") if "ID:" in l][0].split("ID:")[1].strip()
        system = server_module._system
        system.persistence.update(mem_id, {"access_count": 3, "coordinates.priority": 0.8})

        server_module._config = CycleGenConfig(
            org_server_enabled=True,
            org_server_url="http://localhost:8100",
            org_api_key="test-key",
        )
        result = await cycle_complete("報告書", cycle_id="TEST7")

        # 既存pendingとして表示されるが、pendingタグが二重に付かない
        updated = system.persistence.load(mem_id)
        assert updated.tags.count("promotion:pending") == 1

    async def test_hitl_pending_and_promoted_corrected(self):
        """FR033: pending+promoted:org両方持つ記憶はタグ補正され候補に出ない"""
        from cyclegen.mcp.tools.memory import memory_store
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        # pending と promoted:org が同時に存在する不整合状態を再現
        r = await memory_store(
            "タグ不整合記憶", layer=4, context="planning",
            tags="promotion:pending,promoted:org"
        )
        mem_id = [l for l in r.split("\n") if "ID:" in l][0].split("ID:")[1].strip()
        system = server_module._system
        system.persistence.update(mem_id, {"access_count": 5, "coordinates.priority": 0.8})

        server_module._config = CycleGenConfig(
            org_server_enabled=True,
            org_server_url="http://localhost:8100",
            org_api_key="test-key",
        )
        result = await cycle_complete("報告書", cycle_id="TEST-FR033")

        # 候補として提示されない
        assert mem_id not in result

        # タグが補正されている（pendingが除去、promoted:orgのみ残る）
        updated = system.persistence.load(mem_id)
        assert "promoted:org" in updated.tags
        assert "promotion:pending" not in updated.tags

    async def test_hitl_rejection_cooldown(self):
        """却下30日以内の記憶は候補にならない"""
        from datetime import datetime
        from cyclegen.mcp.tools.memory import memory_store
        from cyclegen.mcp.tools.lifecycle import cycle_complete

        today = datetime.now().strftime("%Y-%m-%d")
        r = await memory_store(
            "最近却下された", layer=4, context="planning",
            tags=f"promotion:rejected:{today}"
        )
        mem_id = [l for l in r.split("\n") if "ID:" in l][0].split("ID:")[1].strip()
        system = server_module._system
        system.persistence.update(mem_id, {"access_count": 5, "coordinates.priority": 0.8})

        server_module._config = CycleGenConfig(
            org_server_enabled=True,
            org_server_url="http://localhost:8100",
            org_api_key="test-key",
        )
        result = await cycle_complete("報告書", cycle_id="TEST8")
        assert "昇格候補: 0件" in result


# === _is_rejection_active ===

class TestIsRejectionActive:
    def test_no_rejection_tag(self):
        """却下タグなし→アクティブでない"""
        from cyclegen.mcp.tools.lifecycle import _is_rejection_active
        from cyclegen.models import Coordinates, Memory

        mem = Memory(content="test", coordinates=Coordinates(layer=4, context="planning"))
        assert not _is_rejection_active(mem)

    def test_recent_rejection(self):
        """30日以内の却下→アクティブ"""
        from datetime import datetime
        from cyclegen.mcp.tools.lifecycle import _is_rejection_active
        from cyclegen.models import Coordinates, Memory

        today = datetime.now().strftime("%Y-%m-%d")
        mem = Memory(
            content="test",
            coordinates=Coordinates(layer=4, context="planning"),
            tags=[f"promotion:rejected:{today}"],
        )
        assert _is_rejection_active(mem)

    def test_old_rejection(self):
        """31日前の却下→アクティブでない"""
        from datetime import datetime, timedelta
        from cyclegen.mcp.tools.lifecycle import _is_rejection_active
        from cyclegen.models import Coordinates, Memory

        old_date = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d")
        mem = Memory(
            content="test",
            coordinates=Coordinates(layer=4, context="planning"),
            tags=[f"promotion:rejected:{old_date}"],
        )
        assert not _is_rejection_active(mem)


# === memory_mark_used ===

class TestMemoryMarkUsed:
    async def test_mark_used(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_mark_used
        store_result = await memory_store("mark_used対象", layer=3, context="implementation")
        mem_id = [line for line in store_result.split("\n") if "ID:" in line][0].split("ID:")[1].strip()
        result = await memory_mark_used(mem_id)
        assert "利用記録完了" in result
        assert "0.55" in result  # 0.5 + 0.05

    async def test_mark_used_nonexistent(self):
        from cyclegen.mcp.tools.memory import memory_mark_used
        result = await memory_mark_used("nonexistent")
        assert "エラー" in result

    async def test_search_and_mark_used_share_session_id(self):
        """同一セッションのsearchとrecall_usedが同じsession_idを持つ（CYCLE13.2 FR031 P1）"""
        from cyclegen.mcp.tools.memory import memory_store, memory_search, memory_mark_used
        import cyclegen.mcp.server as srv
        from cyclegen.models import EventType
        store_result = await memory_store("session紐付けテスト", layer=3, context="implementation")
        mem_id = [l for l in store_result.split("\n") if "ID:" in l][0].split("ID:")[1].strip()
        await memory_search("session紐付け")
        await memory_mark_used(mem_id)
        search_ev = srv._event_logger.get_events(EventType.SEARCH)[0]
        used_ev = srv._event_logger.get_events(EventType.RECALL_USED)[0]
        assert search_ev.details["session_id"] == used_ev.details["session_id"]


# === memory_diagnostics ===

class TestMemoryDiagnostics:
    async def test_diagnostics_empty(self):
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics
        result = await memory_diagnostics()
        assert "3次元記憶 診断レポート" in result
        assert "総記憶数: 0" in result

    async def test_diagnostics_after_operations(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_search
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics
        await memory_store("テスト1", layer=3, context="implementation")
        await memory_store("テスト2", layer=4, context="planning")
        await memory_search("テスト")
        result = await memory_diagnostics()
        assert "総記憶数: 2" in result
        assert "検索回数: 1" in result

    async def test_diagnostics_shows_precision(self):
        from cyclegen.mcp.tools.memory import memory_store, memory_search, memory_mark_used
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics
        store_result = await memory_store("Precision測定テスト", layer=3, context="implementation")
        mem_id = [line for line in store_result.split("\n") if "ID:" in line][0].split("ID:")[1].strip()
        await memory_search("Precision測定")
        await memory_mark_used(mem_id)
        result = await memory_diagnostics()
        assert "3指標（証明装置）" in result
        assert "Memory Precision:" in result


# === 3d-eval フィードバック（FR004） ===

class TestThreeDEval:
    async def test_all_omitted_returns_criteria(self):
        """3軸全省略で評価基準が返る"""
        from cyclegen.mcp.tools.memory import memory_store
        result = await memory_store("テスト内容")
        assert "2軸評価リクエスト" in result
        assert "Layer" in result
        assert "Priority" in result
        assert "Context" in result
        assert "再度呼んでください" in result

    async def test_layer_only_saves_with_auto_context(self):
        """CYCLE12.8.2: layer指定+context省略は自動判定で保存成功"""
        from cyclegen.mcp.tools.memory import memory_store
        r1 = await memory_store("テスト", layer=4)
        assert "記憶保存完了" in r1

    async def test_context_only_triggers_eval(self):
        """context指定+layer省略は3d-eval"""
        from cyclegen.mcp.tools.memory import memory_store
        r1 = await memory_store("テスト", context="planning")
        assert "2軸評価リクエスト" in r1

    async def test_all_specified_saves(self):
        """3軸全指定なら保存される"""
        from cyclegen.mcp.tools.memory import memory_store
        result = await memory_store("テスト", layer=4, context="planning")
        assert "記憶保存完了" in result
        assert "L4" in result

    async def test_criteria_content_preview(self):
        """評価基準にコンテンツプレビューが含まれる"""
        from cyclegen.mcp.tools.memory import memory_store
        result = await memory_store("アーキテクチャの設計方針を議論した")
        assert "アーキテクチャの設計方針を議論した" in result


# === content_hash 重複検知 ===

class TestContentHash:
    async def test_store_generates_hash(self):
        """保存時にcontent_hashが生成される"""
        import cyclegen.mcp.server as srv
        from cyclegen.mcp.tools.memory import memory_store
        await memory_store("ハッシュテスト", layer=3, context="implementation")
        system = srv._system
        memories = system.persistence.load_all()
        assert len(memories) == 1
        assert memories[0].content_hash != ""
        assert len(memories[0].content_hash) == 64  # SHA-256

    async def test_find_by_hash(self):
        """content_hashで記憶を検索できる"""
        import hashlib
        import cyclegen.mcp.server as srv
        from cyclegen.mcp.tools.memory import memory_store
        content = "重複検知テスト用コンテンツ"
        await memory_store(content, layer=3, context="implementation")
        system = srv._system
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        found = system.find_by_hash(expected_hash)
        assert found is not None
        assert found.content == content

    async def test_find_by_hash_not_found(self):
        """存在しないハッシュではNone"""
        import cyclegen.mcp.server as srv
        from cyclegen.mcp.tools.memory import memory_store
        await memory_store("何か", layer=3, context="implementation")
        system = srv._system
        assert system.find_by_hash("nonexistent_hash") is None


# === memory_bulk_import ===

class TestMemoryBulkImport:
    async def test_dry_run(self, tmp_path):
        """ドライランでプレビューが返る"""
        (tmp_path / "test1.md").write_text("テスト記憶その1")
        (tmp_path / "test2.md").write_text("テスト記憶その2")

        from cyclegen.mcp.tools.bulk_import import memory_bulk_import
        result = await memory_bulk_import(paths=str(tmp_path), dry_run=True)
        assert "ドライラン" in result
        assert "投入予定: 2件" in result

    async def test_import_files(self, tmp_path):
        """実際にファイルを投入して記憶が増える"""
        (tmp_path / "memo.md").write_text("MCPツール版bulk-importテスト")

        from cyclegen.mcp.tools.bulk_import import memory_bulk_import
        result = await memory_bulk_import(paths=str(tmp_path))
        assert "投入完了: 1件" in result
        assert "実行" in result

    async def test_with_tags(self, tmp_path):
        """タグ付きインポート"""
        (tmp_path / "tagged.md").write_text("タグ付きテスト")

        from cyclegen.mcp.tools.bulk_import import memory_bulk_import
        result = await memory_bulk_import(paths=str(tmp_path), tags="test,bulk")
        assert "投入完了: 1件" in result

    async def test_empty_path_error(self):
        """空パスでエラー"""
        from cyclegen.mcp.tools.bulk_import import memory_bulk_import
        result = await memory_bulk_import(paths="")
        assert "エラー" in result

    async def test_nonexistent_path_error(self):
        """存在しないパスでエラー"""
        from cyclegen.mcp.tools.bulk_import import memory_bulk_import
        result = await memory_bulk_import(paths="/nonexistent/path/to/nowhere")
        assert "エラー" in result
        assert "存在しないパス" in result

    async def test_multiple_paths(self, tmp_path):
        """カンマ区切り複数パス"""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        (dir1 / "a.md").write_text("ファイルA")
        (dir2 / "b.md").write_text("ファイルB")

        from cyclegen.mcp.tools.bulk_import import memory_bulk_import
        result = await memory_bulk_import(paths=f"{dir1},{dir2}")
        assert "投入完了: 2件" in result

    async def test_max_depth(self, tmp_path):
        """max_depthで深さ制限"""
        deep = tmp_path / "level1" / "level2"
        deep.mkdir(parents=True)
        (tmp_path / "top.md").write_text("トップ")
        (tmp_path / "level1" / "mid.md").write_text("中間")
        (deep / "bottom.md").write_text("深い")

        from cyclegen.mcp.tools.bulk_import import memory_bulk_import
        result = await memory_bulk_import(paths=str(tmp_path), max_depth=0)
        assert "投入完了: 1件" in result  # topのみ

    async def test_duplicate_skipped(self, tmp_path):
        """重複ファイルがスキップされる"""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "dup.md").write_text("重複テスト内容")

        from cyclegen.mcp.tools.bulk_import import memory_bulk_import
        # 1回目
        result1 = await memory_bulk_import(paths=str(source_dir))
        assert "投入完了: 1件" in result1
        # 2回目 — 重複検知
        result2 = await memory_bulk_import(paths=str(source_dir))
        assert "重複: 1件" in result2

    async def test_chunk_split(self, tmp_path):
        """チャンク分割で複数記憶が作られる"""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "doc.md").write_text(
            "## セクション1\n内容1。" + "あ" * 100
            + "\n\n## セクション2\n内容2。" + "い" * 100
        )

        from cyclegen.mcp.tools.bulk_import import memory_bulk_import
        result = await memory_bulk_import(paths=str(source_dir), chunk=True)
        assert "投入完了: 2件" in result

    async def test_no_chunk(self, tmp_path):
        """chunk=Falseで1ファイル=1記憶"""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "doc.md").write_text(
            "## セクション1\n内容1。" + "あ" * 100
            + "\n\n## セクション2\n内容2。" + "い" * 100
        )

        from cyclegen.mcp.tools.bulk_import import memory_bulk_import
        result = await memory_bulk_import(paths=str(source_dir), chunk=False)
        assert "投入完了: 1件" in result

    async def test_diagnostics_after_import(self, tmp_path):
        """実投入後にdiagnosticsレポートが付与される"""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "memo.md").write_text("diagnostics連携テスト")

        from cyclegen.mcp.tools.bulk_import import memory_bulk_import
        result = await memory_bulk_import(paths=str(source_dir))
        assert "投入完了: 1件" in result
        assert "投入後 diagnostics" in result
        assert "3次元記憶 診断レポート" in result

    async def test_no_diagnostics_on_dry_run(self, tmp_path):
        """ドライランではdiagnosticsレポートは付与されない"""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "memo.md").write_text("ドライランテスト")

        from cyclegen.mcp.tools.bulk_import import memory_bulk_import
        result = await memory_bulk_import(paths=str(source_dir), dry_run=True)
        assert "投入予定: 1件" in result
        assert "投入後 diagnostics" not in result

    async def test_quality_warnings_shown(self, tmp_path):
        """品質警告がMCPレスポンスに含まれる"""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        for i in range(5):
            (source_dir / f"memo{i}.md").write_text(f"メモ{i}")

        from cyclegen.mcp.tools.bulk_import import memory_bulk_import
        result = await memory_bulk_import(paths=str(source_dir), dry_run=True, chunk=False)
        assert "品質警告" in result


# === memory_reembed (CYCLE12.7.5) ===

class TestMemoryReembed:
    async def test_reembed_dry_run_empty(self):
        """記憶なし → 対象0件"""
        from cyclegen.mcp.tools.diagnostics import memory_reembed
        result = await memory_reembed(dry_run=True)
        # fastembed未インストールの場合はエラーメッセージ
        if "fastembed未インストール" in result:
            assert "pip install" in result
        else:
            assert "embedding一括生成レポート" in result
            assert "embedding未設定: 0件" in result

    async def test_reembed_dry_run_with_memories(self):
        """記憶あり → 未設定件数を表示"""
        from cyclegen.mcp.tools.memory import memory_store
        from cyclegen.mcp.tools.diagnostics import memory_reembed
        await memory_store("reembedテスト1", layer=3, context="implementation")
        await memory_store("reembedテスト2", layer=4, context="planning")
        result = await memory_reembed(dry_run=True)
        if "fastembed未インストール" in result:
            pytest.skip("fastembed not installed")
        assert "embedding一括生成レポート" in result
        assert "dry-run" in result


class TestMemoryReclassify:
    async def test_reclassify_dry_run_empty(self):
        """記憶なし → 候補0件"""
        from cyclegen.mcp.tools.diagnostics import memory_reclassify
        result = await memory_reclassify(dry_run=True)
        if "fastembed未インストール" in result:
            assert "pip install" in result
        elif "descriptionフィールドがありません" in result:
            pytest.skip("enterprise_contexts.yaml has no descriptions")
        else:
            assert "Context再分類レポート" in result

    async def test_reclassify_dry_run_with_memories(self):
        """記憶あり → 変更候補を表示"""
        from cyclegen.mcp.tools.memory import memory_store
        from cyclegen.mcp.tools.diagnostics import memory_reclassify
        await memory_store("SaaS認証ミドルウェア実装。API Key検証。", layer=3, context="planning")
        result = await memory_reclassify(dry_run=True)
        if "fastembed未インストール" in result:
            pytest.skip("fastembed not installed")
        elif "descriptionフィールドがありません" in result:
            pytest.skip("enterprise_contexts.yaml has no descriptions")
        assert "Context再分類レポート" in result
        assert "dry-run" in result
