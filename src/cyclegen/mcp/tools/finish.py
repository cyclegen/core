"""mcp/tools/finish.py — Markdown→docx変換ツール（optional extras: docx）

python-docx がインストールされている場合のみツールを登録する。
pip install cyclegen[docx] で有効化。

CYCLE12.10.2: 初期実装（FR024）
"""

from __future__ import annotations

from pathlib import Path

try:
    from docx import Document  # noqa: F401

    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False


def register_finish_tools(mcp) -> None:
    """HAS_DOCX=True の場合のみ finish ツールを登録する。"""
    if not HAS_DOCX:
        return

    @mcp.tool()
    async def document_finish(
        input_path: str,
        template: str = "executive",
        output_path: str = "",
    ) -> str:
        """Markdownファイルを装飾されたdocxに変換する。

        テンプレートを指定して、Markdownから見出し・リスト・テーブル・コードブロック等を
        保持した装飾docxを生成する。

        Args:
            input_path: 入力Markdownファイルの絶対パス
            template: テンプレート名（デフォルト: executive）。list_finish_templates で一覧を確認可能
            output_path: 出力docxファイルパス。省略時は入力ファイル名.docx
        """
        from cyclegen.finish.converter import generate_docx, parse_markdown
        from cyclegen.finish.templates import load_template

        input_file = Path(input_path)
        if not input_file.exists():
            return f"エラー: ファイルが見つかりません: {input_path}"

        if not input_file.suffix.lower() in (".md", ".markdown", ".txt"):
            return f"エラー: Markdownファイルを指定してください（拡張子: .md, .markdown, .txt）: {input_path}"

        # 出力先の決定
        if output_path:
            out = Path(output_path)
        else:
            out = input_file.with_suffix(".docx")

        # テンプレート読み込み
        try:
            tmpl = load_template(template)
        except FileNotFoundError as e:
            return str(e)

        # 変換実行
        content = input_file.read_text(encoding="utf-8")
        elements = parse_markdown(content)
        result = generate_docx(elements, tmpl, out, base_dir=input_file.resolve().parent)

        tmpl_name = tmpl.get("name", template)
        return (
            f"docx変換完了\n"
            f"  入力: {input_path}\n"
            f"  出力: {result}\n"
            f"  テンプレート: {tmpl_name}\n"
            f"  要素数: {len(elements)}"
        )

    @mcp.tool()
    async def list_finish_templates() -> str:
        """利用可能なdocxテンプレート一覧を返す。

        各テンプレートの名前と説明を表示する。
        document_finish の template 引数に名前を指定して使用する。
        """
        from cyclegen.finish.templates import list_templates

        templates = list_templates()
        lines = ["利用可能なテンプレート:", ""]
        for t in templates:
            lines.append(f"  {t['name']:25s} {t['description']}")
        lines.append("")
        lines.append(f"合計: {len(templates)}種")
        return "\n".join(lines)
