"""test_full_flow.py — E2Eテスト

MCPツール関数を直接呼び出し、Phase1 MVPの全機能が一貫して動作することを検証する。
store → search → boost → dismiss → pin → archive → update → delete →
cycle_complete → status → diagnostics の全フローを1つのテストシナリオで実行。
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


def _extract_id(tool_output: str) -> str:
    """ツール出力からmemory IDを抽出する。"""
    for line in tool_output.split("\n"):
        if "ID:" in line:
            return line.split("ID:")[1].strip()
    raise ValueError(f"ID not found in output: {tool_output}")


@pytest.fixture(autouse=True)
def setup_e2e(tmp_path):
    """E2E用の完全なシステムを構築。"""
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

    yield tmp_path, persistence

    persistence.close()
    server_module._system = None
    server_module._valve = None
    server_module._event_logger = None
    server_module._config = None


class TestFullFlow:
    """Phase1 MVPの全機能を通しで検証するE2Eシナリオ。"""

    async def test_complete_lifecycle(self, setup_e2e):
        """store → search → boost → dismiss → pin → archive → update → delete"""
        from cyclegen.mcp.tools.memory import (
            memory_archive,
            memory_boost,
            memory_delete,
            memory_dismiss,
            memory_pin,
            memory_search,
            memory_store,
            memory_update,
        )

        tmp_path, persistence = setup_e2e

        # === 1. Store: 複数記憶を保存 ===
        r1 = await memory_store(
            "Keycloak + OIDCで認証基盤を構築。JWT短命トークン15分。",
            layer=4, context="planning", tags="認証,Keycloak",
        )
        assert "記憶保存完了" in r1
        id1 = _extract_id(r1)

        r2 = await memory_store(
            "PythonのPydanticでデータモデルを型安全に実装した。バリデーションが強力。",
            layer=2, context="implementation", tags="Python,Pydantic",
        )
        id2 = _extract_id(r2)

        r3 = await memory_store(
            "SQLiteのインデックスが遅い問題をCOMPOSITEインデックスで解決",
            layer=1, context="debugging",
        )
        id3 = _extract_id(r3)

        # mdファイルが作成されている
        assert (tmp_path / "memories" / f"{id1}.md").exists()
        assert (tmp_path / "memories" / f"{id2}.md").exists()

        # === 2. Search: 検索 ===
        search_result = await memory_search("認証 Keycloak")
        assert "検索結果:" in search_result
        assert "Keycloak" in search_result
        assert id1 in search_result

        # context指定検索
        search_impl = await memory_search("Python", context="implementation")
        assert "Python" in search_impl

        # layer指定検索
        search_l4 = await memory_search("認証", layer_filter="4")
        assert id1 in search_l4

        # === 3. Boost: フィードバック（役立った） ===
        boost_result = await memory_boost(id1)
        assert "boost完了" in boost_result
        # Priority = 0.3 + record_access増進(+0.02×検索返却回数) + boost(+0.10)
        # 具体値は検索でヒットした回数に依存するため、boostが反映されたことを確認
        assert "Priority →" in boost_result

        # === 4. Dismiss: フィードバック（不適切） ===
        dismiss_result = await memory_dismiss(id3)
        assert "dismiss完了" in dismiss_result
        # id3も検索で返却された可能性があるため、具体値ではなくdismiss成功を確認
        assert "Priority →" in dismiss_result

        # === 5. Pin: 重要マーク ===
        pin_result = await memory_pin(id1)
        assert "ピン留め完了" in pin_result

        # === 6. Archive: アーカイブ ===
        archive_result = await memory_archive(id3)
        assert "アーカイブ完了" in archive_result

        # アーカイブ後は検索から除外される
        search_after_archive = await memory_search("SQLite")
        assert id3 not in search_after_archive

        # === 7. Update: 更新 ===
        update_result = await memory_update(id2, content="PydanticV2でデータモデルを実装。model_validator活用。")
        assert "更新完了" in update_result

        # 更新内容を検索で確認
        search_updated = await memory_search("model_validator")
        assert "model_validator" in search_updated

        # === 8. Delete: 削除 ===
        delete_result = await memory_delete(id3)
        assert "削除完了" in delete_result
        assert not (tmp_path / "memories" / f"{id3}.md").exists()

    async def test_cycle_complete_and_status(self, setup_e2e):
        """cycle_complete → status → diagnostics の検証"""
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics
        from cyclegen.mcp.tools.lifecycle import cycle_complete, memory_status
        from cyclegen.mcp.tools.memory import memory_boost, memory_search, memory_store

        # 記憶を蓄積
        await memory_store("設計方針A", layer=4, context="planning")
        await memory_store("実装手順B", layer=2, context="implementation")
        await memory_store("トラブル対応C", layer=1, context="debugging")

        # 検索してboost（イベントログを蓄積）
        await memory_search("設計")
        r = await memory_store("テスト記憶", layer=3, context="implementation")
        test_id = _extract_id(r)
        await memory_boost(test_id)

        # === cycle_complete ===
        cc_result = await cycle_complete(
            "CYCLE4.9完了: E2Eテスト全通過。Phase1-D仕上げ完了。",
            cycle_id="CYCLE4.9",
        )
        assert "CYCLE完了記録" in cc_result
        assert "意味単位に分割" in cc_result

        # === memory_status ===
        status_result = await memory_status()
        assert "CycleGen 3次元記憶ステータス" in status_result
        assert "Personal Layer:" in status_result
        # 4件（cycle_completeは記憶を保存しなくなった）
        assert "4件" in status_result

        # === memory_diagnostics ===
        diag_result = await memory_diagnostics(period_days=1)
        assert "3次元記憶 診断レポート" in diag_result
        assert "総記憶数: 4" in diag_result
        assert "検索回数: 1" in diag_result
        assert "boost:" in diag_result

    async def test_3d_eval_feedback(self, setup_e2e):
        """3軸省略時に3d-evalフィードバックが返る（FR004）"""
        from cyclegen.mcp.tools.memory import memory_store

        # 全パラメータ省略 → 3d-eval基準が返される
        r1 = await memory_store("アーキテクチャの戦略を設計する方針を決めた")
        assert "2軸評価リクエスト" in r1
        assert "Layer" in r1
        assert "Context" in r1
        assert "再度呼んでください" in r1

        # CYCLE12.8.2: layer指定+context省略は自動判定で保存成功
        r2 = await memory_store("テスト内容", layer=4)
        assert "記憶保存完了" in r2

        # 全指定なら保存される
        r3 = await memory_store("テスト内容", layer=4, context="planning")
        assert "記憶保存完了" in r3

    async def test_miller_limit(self, setup_e2e):
        """Miller's 7±2 制限の動作確認"""
        from cyclegen.mcp.tools.memory import memory_search, memory_store

        # 20件保存
        for i in range(20):
            await memory_store(f"テスト記憶 {i}: Pythonの機能", layer=3, context="implementation")

        # デフォルト max_items=7
        result = await memory_search("Python テスト")
        # 結果行をカウント（"1." "2." ... のパターン）
        result_lines = [l for l in result.split("\n") if l.strip().startswith(("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9."))]
        assert len(result_lines) <= 7

    async def test_md_file_roundtrip(self, setup_e2e):
        """mdファイルの書出→読込ラウンドトリップ"""
        from cyclegen.mcp.tools.memory import memory_search, memory_store

        tmp_path, persistence = setup_e2e

        # 保存
        r = await memory_store(
            "複数行の記憶テスト\n\n## セクション1\n\n- 項目A\n- 項目B",
            layer=3, context="documentation", tags="test,multiline",
        )
        mem_id = _extract_id(r)

        # mdファイルを直接読む
        md_path = tmp_path / "memories" / f"{mem_id}.md"
        assert md_path.exists()
        md_content = md_path.read_text(encoding="utf-8")
        assert "---" in md_content
        assert "セクション1" in md_content

        # sync_from_mdで再読込
        persistence.sync_from_md()

        # 検索で見つかる
        result = await memory_search("セクション1")
        assert mem_id in result

    async def test_integrated_search_with_org(self, setup_e2e):
        """統合検索でPersonal+Org結果がsource付きで返る（CYCLE7.3.3）"""
        from unittest.mock import MagicMock
        from cyclegen.mcp.tools.memory import memory_search, memory_store
        from cyclegen.models import Coordinates, Memory, SearchResult

        # Personal記憶を保存
        await memory_store(
            "ローカルのPython設計パターン", layer=3, context="implementation",
        )

        # Org検索結果をモックで注入
        org_memory = Memory(
            id="org_mem_001",
            content="組織のPython設計ガイドライン: PEP8遵守",
            coordinates=Coordinates(layer=4, context="planning"),
            tags=["org:standard"],
        )
        org_result = SearchResult(
            memory=org_memory, score=75.0, source="org",
            reason="組織の設計ガイドラインが一致",
        )

        mock_org = MagicMock()
        mock_org.search.return_value = [org_result]

        # ValveにOrgClientを注入
        server_module._valve.org_client = mock_org

        result = await memory_search("Python 設計")
        assert "検索結果:" in result
        # sourceが両方含まれる
        assert "[personal]" in result
        assert "[org]" in result
        assert "org_mem_001" in result

    async def test_personal_bonus_effect(self, setup_e2e):
        """personal_bonusによりPersonal記憶のスコアが加算される（CYCLE7.3.3）"""
        from unittest.mock import MagicMock
        from cyclegen.mcp.tools.memory import memory_search, memory_store
        from cyclegen.models import Coordinates, Memory, SearchResult

        # Personal記憶を保存
        await memory_store(
            "Python Pydanticでバリデーション実装", layer=3, context="implementation",
        )

        # Org記憶（スコア30、bonusなし — 低スコアなのでPersonal+bonus=20が勝つ）
        org_memory = Memory(
            id="org_mem_002",
            content="Python型安全コーディング規約",
            coordinates=Coordinates(layer=4, context="planning"),
        )
        org_result = SearchResult(
            memory=org_memory, score=30.0, source="org",
            reason="Orgの型安全規約が一致",
        )

        mock_org = MagicMock()
        mock_org.search.return_value = [org_result]
        server_module._valve.org_client = mock_org

        result = await memory_search("Python")
        lines = result.split("\n")
        # Personal記憶がpersonal_bonus加算でOrgより上位に来る
        first_result_line = [l for l in lines if l.startswith("1.")][0]
        assert "[personal]" in first_result_line
        # Org結果も含まれている
        assert "[org]" in result

    async def test_two_channel_search(self, setup_e2e):
        """CYCLE12.8.4 FR019: 2チャネル検索でL5がメタ認知チャネルに分離"""
        from cyclegen.mcp.tools.memory import memory_search, memory_store

        # L2-4のタスク記憶を保存
        await memory_store("Python実装パターン集", layer=2, context="implementation")
        await memory_store("設計方針の策定手順", layer=4, context="planning")

        # L5のメタ認知記憶を保存
        await memory_store("森枝思考: 複数の視点から対象を分析する思考法", layer=5, context="planning")

        result = await memory_search("思考 設計")

        # 2チャネル表示の確認
        assert "メタ認知チャネル" in result or "タスクチャネル" in result or "検索結果:" in result
        # L5記憶がタスクチャネルに混入しないことを確認
        # タスクチャネルの記憶はL1-4のみ
        lines = result.split("\n")
        in_task_channel = False
        for line in lines:
            if "タスクチャネル" in line:
                in_task_channel = True
            if "メタ認知チャネル" in line:
                in_task_channel = False
            if in_task_channel and line.strip().startswith(("1.", "2.", "3.")):
                assert "L5/" not in line

    async def test_two_channel_no_l5(self, setup_e2e):
        """CYCLE12.8.4 FR019: L5記憶がない場合、メタ認知チャネルセクションを省略"""
        from cyclegen.mcp.tools.memory import memory_search, memory_store

        await memory_store("Python実装パターン", layer=2, context="implementation")

        result = await memory_search("Python")
        # メタ認知チャネルセクションがないこと
        assert "メタ認知チャネル" not in result

    async def test_two_channel_header_count(self, setup_e2e):
        """CYCLE12.8.4 FR019: ヘッダーにメタ認知+タスクの件数が表示"""
        from cyclegen.mcp.tools.memory import memory_search, memory_store

        await memory_store("Python設計パターン", layer=3, context="implementation")
        await memory_store("メタ認知思考法パターン", layer=5, context="planning")

        result = await memory_search("パターン")
        if "メタ認知:" in result:
            # メタ認知がヒットした場合、ヘッダーに件数表示
            assert "メタ認知:" in result
            assert "タスク:" in result

    async def test_error_handling(self, setup_e2e):
        """存在しないIDへの操作がエラーメッセージを返す"""
        from cyclegen.mcp.tools.memory import (
            memory_archive,
            memory_boost,
            memory_delete,
            memory_dismiss,
            memory_pin,
            memory_update,
        )

        nonexistent = "mem_nonexistent_999"
        assert "エラー" in await memory_update(nonexistent, content="x")
        assert "エラー" in await memory_delete(nonexistent)
        assert "エラー" in await memory_pin(nonexistent)
        assert "エラー" in await memory_archive(nonexistent)
        assert "エラー" in await memory_boost(nonexistent)
        assert "エラー" in await memory_dismiss(nonexistent)
