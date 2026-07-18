"""test_mcp_tools.py — finish MCPツールの結合テスト"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from cyclegen.mcp.tools.finish import HAS_DOCX, register_finish_tools


class MockMCP:
    """register_finish_tools のテスト用MCPモック"""

    def __init__(self):
        self._tools: dict[str, callable] = {}

    def tool(self):
        def decorator(func):
            self._tools[func.__name__] = func
            return func
        return decorator


SAMPLE_MD = """\
# Test Document

## Section

Body text with **bold**.

- item 1
- item 2

| A | B |
|---|---|
| 1 | 2 |
"""


@pytest.fixture
def mcp_tools():
    mock = MockMCP()
    register_finish_tools(mock)
    return mock._tools


@pytest.fixture
def md_file(tmp_path):
    p = tmp_path / "test.md"
    p.write_text(SAMPLE_MD, encoding="utf-8")
    return p


class TestDocumentFinish:
    async def test_basic_conversion(self, mcp_tools, md_file):
        result = await mcp_tools["document_finish"](str(md_file))
        assert "docx変換完了" in result
        assert md_file.with_suffix(".docx").exists()

    async def test_custom_template(self, mcp_tools, md_file):
        result = await mcp_tools["document_finish"](str(md_file), "minimal")
        assert "Minimal" in result

    async def test_custom_output_path(self, mcp_tools, md_file, tmp_path):
        out = tmp_path / "custom_output.docx"
        result = await mcp_tools["document_finish"](str(md_file), "executive", str(out))
        assert "docx変換完了" in result
        assert out.exists()

    async def test_file_not_found(self, mcp_tools):
        result = await mcp_tools["document_finish"]("/nonexistent/file.md")
        assert "エラー" in result
        assert "見つかりません" in result

    async def test_invalid_template(self, mcp_tools, md_file):
        result = await mcp_tools["document_finish"](str(md_file), "nonexistent_xyz")
        assert "見つかりません" in result
        assert "利用可能" in result

    async def test_invalid_extension(self, mcp_tools, tmp_path):
        txt = tmp_path / "test.py"
        txt.write_text("x = 1")
        result = await mcp_tools["document_finish"](str(txt))
        assert "エラー" in result
        assert "Markdown" in result


class TestListFinishTemplates:
    async def test_returns_all(self, mcp_tools):
        result = await mcp_tools["list_finish_templates"]()
        assert "executive" in result
        assert "minimal" in result
        assert "合計: 7種" in result


class TestHasDocxFalse:
    def test_no_tools_registered_when_docx_missing(self):
        mock = MockMCP()
        # HAS_DOCX を一時的に False に
        with patch("cyclegen.mcp.tools.finish.HAS_DOCX", False):
            from cyclegen.mcp.tools.finish import register_finish_tools as reg
            reg(mock)
        assert mock._tools == {}
