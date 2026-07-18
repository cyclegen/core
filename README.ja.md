# CycleGen Core

3次元記憶システム（スキル・記憶ストア）を備えた、1時間1サイクルの人間-AI協働フレームワーク — CycleGen Core（Apache 2.0 OSS）。

## インストール

```bash
pip install cyclegen
```

### Optional Extras

```bash
# Markdown → docx変換（CycleGen Finish）
pip install cyclegen[docx]
```

## MCPツール

### CycleGen Finish（docx extras 必要）

`pip install cyclegen[docx]` でインストールすると、以下のMCPツールが追加される:

- **document_finish**: Markdownファイルを装飾されたdocxに変換
  - `input_path`: 入力Markdownファイルの絶対パス
  - `template`: テンプレート名（デフォルト: executive）
  - `output_path`: 出力先（省略時は入力ファイル名.docx）
- **list_finish_templates**: 利用可能なテンプレート一覧を表示

利用可能なテンプレート: executive, minimal, creative, startup, wa-modern, deep-out, deep-out-paperback, concept-book
