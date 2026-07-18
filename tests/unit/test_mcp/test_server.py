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
