"""test_server.py — MCPサーバーのtransport切替テスト

CYCLE7.4.1: SSE/Streamable HTTP transport対応
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class TestMainTransport:
    """main()のtransport切替ロジックをテストする。"""

    def test_default_stdio(self):
        """引数なしでstdio"""
        with patch("cyclegen.mcp.server.register_tools"), \
             patch("cyclegen.mcp.server.mcp") as mock_mcp, \
             patch("sys.argv", ["cyclegen-mcp"]):
            from cyclegen.mcp.server import main
            main()
            mock_mcp.run.assert_called_once_with(transport="stdio")

    def test_cli_arg_sse(self):
        """--transport sseでSSE"""
        with patch("cyclegen.mcp.server.register_tools"), \
             patch("cyclegen.mcp.server.mcp") as mock_mcp, \
             patch("sys.argv", ["cyclegen-mcp", "--transport", "sse"]):
            from cyclegen.mcp.server import main
            main()
            mock_mcp.run.assert_called_once_with(transport="sse")

    def test_cli_arg_streamable_http(self):
        """--transport streamable-httpでCORS付きStreamable HTTP起動"""
        with patch("cyclegen.mcp.server.register_tools"), \
             patch("cyclegen.mcp.server.mcp") as mock_mcp, \
             patch("cyclegen.mcp.server.asyncio") as mock_asyncio, \
             patch("sys.argv", ["cyclegen-mcp", "--transport", "streamable-http"]):
            from cyclegen.mcp.server import main
            main()
            # streamable-httpはasyncio.run(_run_with_cors)で起動される
            mock_asyncio.run.assert_called_once()
            mock_mcp.run.assert_not_called()

    def test_env_var_transport(self):
        """環境変数CYCLEGEN_TRANSPORTでSSE"""
        with patch("cyclegen.mcp.server.register_tools"), \
             patch("cyclegen.mcp.server.mcp") as mock_mcp, \
             patch("sys.argv", ["cyclegen-mcp"]), \
             patch.dict(os.environ, {"CYCLEGEN_TRANSPORT": "sse"}):
            from cyclegen.mcp.server import main
            main()
            mock_mcp.run.assert_called_once_with(transport="sse")

    def test_cli_overrides_env(self):
        """CLI引数が環境変数より優先"""
        with patch("cyclegen.mcp.server.register_tools"), \
             patch("cyclegen.mcp.server.mcp") as mock_mcp, \
             patch("cyclegen.mcp.server.asyncio") as mock_asyncio, \
             patch("sys.argv", ["cyclegen-mcp", "--transport", "streamable-http"]), \
             patch.dict(os.environ, {"CYCLEGEN_TRANSPORT": "sse"}):
            from cyclegen.mcp.server import main
            main()
            # CLI引数のstreamable-httpが優先 → asyncio.run経由で起動
            mock_asyncio.run.assert_called_once()
            mock_mcp.run.assert_not_called()

    def test_invalid_transport_exits(self):
        """不明なtransportでsys.exit(1)"""
        with patch("cyclegen.mcp.server.register_tools"), \
             patch("cyclegen.mcp.server.mcp"), \
             patch("sys.argv", ["cyclegen-mcp", "--transport", "websocket"]), \
             pytest.raises(SystemExit) as exc_info:
            from cyclegen.mcp.server import main
            main()
        assert exc_info.value.code == 1


class TestMainSse:
    """main_sse()のテスト。"""

    def test_main_sse_calls_sse(self):
        """main_sse()はsse transportで起動"""
        with patch("cyclegen.mcp.server.register_tools"), \
             patch("cyclegen.mcp.server.mcp") as mock_mcp:
            from cyclegen.mcp.server import main_sse
            main_sse()
            mock_mcp.run.assert_called_once_with(transport="sse")


class TestPreimportEmbedding:
    """CYCLE20.7 / F-22: 起動時のfastembed preimport。

    背景: 遅延importのままだと主スレッドとワーカースレッドが同時に
    `import fastembed`→`numpy` の C拡張をロードしてデッドロックし、
    最初のツール呼び出しが返らない（WIN-01実測 150秒タイムアウト → 0.05秒）。
    """

    def test_calls_embedding_manager_create(self):
        """preimportは EmbeddingManager.create() を1回だけ呼ぶ"""
        with patch("cyclegen.search.embedding.EmbeddingManager.create") as mock_create:
            from cyclegen.mcp.server import _preimport_embedding
            _preimport_embedding()
            mock_create.assert_called_once()

    def test_survives_missing_fastembed(self):
        """★fastembed未導入（semantic extra なし）でも起動を止めない。

        create() は ImportError を握って None を返す設計だが、
        その設計が将来変わってもサーバ起動が落ちないことを、ここで固定する。
        """
        with patch(
            "cyclegen.search.embedding.EmbeddingManager.create",
            side_effect=ImportError("No module named 'fastembed'"),
        ):
            from cyclegen.mcp.server import _preimport_embedding
            _preimport_embedding()  # 例外が出なければ合格

    def test_survives_unexpected_error(self):
        """想定外の失敗（壊れたwheel等）でも起動を止めない"""
        with patch(
            "cyclegen.search.embedding.EmbeddingManager.create",
            side_effect=RuntimeError("壊れたネイティブ拡張"),
        ):
            from cyclegen.mcp.server import _preimport_embedding
            _preimport_embedding()

    def test_main_preimports_before_run(self):
        """★main() は mcp.run() より前に preimport する（イベントループに入る前）"""
        calls = []
        with patch("cyclegen.mcp.server.register_tools"), \
             patch("cyclegen.mcp.server._preimport_embedding",
                   side_effect=lambda: calls.append("preimport")), \
             patch("cyclegen.mcp.server.mcp") as mock_mcp, \
             patch("sys.argv", ["cyclegen-mcp"]):
            mock_mcp.run.side_effect = lambda **_kw: calls.append("run")
            from cyclegen.mcp.server import main
            main()
        assert calls == ["preimport", "run"]

    def test_main_sse_preimports_before_run(self):
        """★入口は2つある: cyclegen-mcp-sse でも preimport する"""
        calls = []
        with patch("cyclegen.mcp.server.register_tools"), \
             patch("cyclegen.mcp.server._preimport_embedding",
                   side_effect=lambda: calls.append("preimport")), \
             patch("cyclegen.mcp.server.mcp") as mock_mcp:
            mock_mcp.run.side_effect = lambda **_kw: calls.append("run")
            from cyclegen.mcp.server import main_sse
            main_sse()
        assert calls == ["preimport", "run"]

    def test_invalid_transport_skips_preimport(self):
        """不正なtransportで終了するときは preimport しない（10秒待たせない）"""
        with patch("cyclegen.mcp.server.register_tools"), \
             patch("cyclegen.mcp.server._preimport_embedding") as mock_pre, \
             patch("cyclegen.mcp.server.mcp"), \
             patch("sys.argv", ["cyclegen-mcp", "--transport", "websocket"]), \
             pytest.raises(SystemExit):
            from cyclegen.mcp.server import main
            main()
        mock_pre.assert_not_called()
