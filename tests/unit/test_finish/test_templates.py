"""test_templates.py — テンプレート読み込み・一覧テスト"""

import pytest

from cyclegen.finish.templates import list_templates, load_template


class TestLoadTemplate:
    def test_executive(self):
        tmpl = load_template("executive")
        assert tmpl["name"] == "Executive"
        assert "heading2" in tmpl
        assert "body" in tmpl

    def test_not_found(self):
        with pytest.raises(FileNotFoundError, match="利用可能"):
            load_template("nonexistent_template_xyz")


class TestListTemplates:
    def test_returns_all(self):
        templates = list_templates()
        assert len(templates) == 7
        names = [t["name"] for t in templates]
        assert "executive" in names
        assert "minimal" in names

    def test_structure(self):
        templates = list_templates()
        for t in templates:
            assert "name" in t
            assert "display_name" in t
            assert "description" in t
            assert isinstance(t["description"], str)
