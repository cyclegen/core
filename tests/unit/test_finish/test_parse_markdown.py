"""test_parse_markdown.py — parse_markdown の各要素タイプテスト"""

import pytest

from cyclegen.finish.converter import parse_markdown


class TestHeading:
    def test_h1(self):
        elements = parse_markdown("# Title")
        assert len(elements) == 1
        assert elements[0] == {"type": "heading", "level": 1, "text": "Title"}

    def test_h2_to_h5(self):
        md = "## H2\n### H3\n#### H4\n##### H5"
        elements = parse_markdown(md)
        assert len(elements) == 4
        for i, level in enumerate([2, 3, 4, 5]):
            assert elements[i]["type"] == "heading"
            assert elements[i]["level"] == level


class TestParagraph:
    def test_simple(self):
        elements = parse_markdown("Hello world")
        assert len(elements) == 1
        assert elements[0] == {"type": "paragraph", "text": "Hello world"}

    def test_multiline_joined(self):
        md = "Line one\nLine two"
        elements = parse_markdown(md)
        assert len(elements) == 1
        assert elements[0]["text"] == "Line one\nLine two"

    def test_bold_italic_preserved(self):
        md = "**bold** and *italic*"
        elements = parse_markdown(md)
        assert "**bold**" in elements[0]["text"]
        assert "*italic*" in elements[0]["text"]

    def test_br_tag(self):
        md = "line1<br>line2"
        elements = parse_markdown(md)
        assert "\n" in elements[0]["text"]


class TestList:
    def test_unordered(self):
        md = "- item1\n- item2"
        elements = parse_markdown(md)
        assert len(elements) == 1
        assert elements[0]["type"] == "list"
        assert len(elements[0]["items"]) == 2
        assert elements[0]["items"][0]["text"] == "item1"

    def test_nested(self):
        md = "- parent\n  - child"
        elements = parse_markdown(md)
        assert elements[0]["items"][0]["level"] == 0
        assert elements[0]["items"][1]["level"] == 1

    def test_ordered(self):
        md = "1. first\n2. second"
        elements = parse_markdown(md)
        assert elements[0]["type"] == "ordered_list"
        assert len(elements[0]["items"]) == 2


class TestTable:
    def test_basic(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        elements = parse_markdown(md)
        assert len(elements) == 1
        assert elements[0]["type"] == "table"
        assert elements[0]["headers"] == ["A", "B"]
        assert elements[0]["rows"] == [["1", "2"]]

    def test_multi_row(self):
        md = "| H1 | H2 |\n|---|---|\n| a | b |\n| c | d |"
        elements = parse_markdown(md)
        assert len(elements[0]["rows"]) == 2


class TestBlockquote:
    def test_basic(self):
        elements = parse_markdown("> quote text")
        assert len(elements) == 1
        assert elements[0] == {"type": "blockquote", "text": "quote text"}

    def test_multiline(self):
        md = "> line1\n> line2"
        elements = parse_markdown(md)
        assert elements[0]["text"] == "line1\nline2"


class TestCodeBlock:
    def test_basic(self):
        md = "```python\nprint('hello')\n```"
        elements = parse_markdown(md)
        assert len(elements) == 1
        assert elements[0]["type"] == "code_block"
        assert elements[0]["language"] == "python"
        assert elements[0]["code"] == "print('hello')"

    def test_no_language(self):
        md = "```\ncode\n```"
        elements = parse_markdown(md)
        assert elements[0]["language"] == ""


class TestSpecialElements:
    def test_hr(self):
        elements = parse_markdown("---")
        assert elements[0]["type"] == "hr"

    def test_pagebreak(self):
        elements = parse_markdown("<!-- pagebreak -->")
        assert elements[0]["type"] == "pagebreak"

    def test_toc(self):
        elements = parse_markdown("<!-- toc -->")
        assert elements[0]["type"] == "toc"

    def test_image(self):
        elements = parse_markdown("![alt text](image.png)")
        assert elements[0]["type"] == "image"
        assert elements[0]["alt"] == "alt text"
        assert elements[0]["path"] == "image.png"


class TestCRLF:
    def test_crlf_normalized(self):
        md = "# Title\r\n\r\nBody"
        elements = parse_markdown(md)
        assert len(elements) == 2
        assert elements[0]["type"] == "heading"
        assert elements[1]["type"] == "paragraph"


class TestEmpty:
    def test_empty_string(self):
        assert parse_markdown("") == []

    def test_only_whitespace(self):
        assert parse_markdown("   \n\n  ") == []
