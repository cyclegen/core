"""mcp/tools/bulk_import.py --- 一括登録MCPツール（14本目）

CYCLE7.2.3: CLIのrun_import()を再利用し、MCP経由でbulk-importを実行可能にする。
CYCLE7.2.5: 品質警告表示 + 投入後diagnostics自動連携。
CYCLE7.7.3.1: async化（ただしrun_importは同期のまま）
CYCLE8.4: SaaS guard追加
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Optional

from cyclegen.mcp.server import _async_get_system, mcp


@mcp.tool()
async def memory_bulk_import(
    paths: str,
    dry_run: bool = False,
    tags: str = "",
    max_depth: Optional[int] = None,
    chunk: bool = True,
) -> str:
    """既存ファイルを3次元記憶に一括登録する（MCPツール版）。

    CLIの cyclegen-import と同等の機能をMCP経由で提供する。
    Markdownファイルは見出し単位でチャンク分割して登録（chunk=falseで無効化可能）。
    YAMLフロントマター自動判別。content_hash(SHA-256)による重複検知あり。
    投入後は自動でdiagnosticsレポートを付与する。

    Args:
        paths: 対象ディレクトリ or ファイル（カンマ区切りで複数指定可）
        dry_run: Trueの場合、投入せずプレビュー表示のみ
        tags: 全ファイルに付与する共通タグ（カンマ区切り）
        max_depth: 再帰探索の深さ制限（省略時は無制限）
        chunk: Markdownを見出し単位でチャンク分割する（デフォルト: true）
    """
    # パスのパース・バリデーション
    path_list = [Path(p.strip()) for p in paths.split(",") if p.strip()]
    if not path_list:
        return "エラー: パスが指定されていません"

    invalid = [str(p) for p in path_list if not p.exists()]
    if invalid:
        return f"エラー: 存在しないパス: {', '.join(invalid)}"

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    # MCPサーバーの永続化層からホームディレクトリを取得
    system, _, _ = await _async_get_system()

    # SaaS guard（Quota残枠チェック + レート制限。SaaSモード外ではno-op）
    from cyclegen.saas.guard import guard_store_bulk
    file_count = sum(1 for p in path_list for _ in (p.rglob("*") if p.is_dir() else [p]) if _.suffix in (".md", ".yaml", ".yml"))
    await guard_store_bulk(system.persistence, file_count)
    home = system.persistence.home

    # run_import を呼び出し（stdout をキャプチャ）— 同期処理
    from cyclegen.cli.bulk_import import run_import

    stdout_buf = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf):
        result = run_import(
            paths=path_list,
            dry_run=dry_run,
            extra_tags=tag_list,
            home=home,
            max_depth=max_depth,
            chunk=chunk,
        )

    # インポート後: MCPサーバーの永続化層を再同期
    imported = result.get("imported", 0)
    if not dry_run and imported > 0:
        system.persistence.sync_from_md()

    # レスポンス構築
    output = stdout_buf.getvalue()
    mode = "ドライラン" if dry_run else "実行"
    skipped = result.get("skipped", 0)
    duplicates = result.get("duplicates", 0)
    errors = result.get("errors", 0)

    lines = [f"=== memory_bulk_import {mode}結果 ==="]

    if dry_run:
        lines.append(f"投入予定: {imported}件 / スキップ: {skipped}件 / 重複: {duplicates}件 / エラー: {errors}件")
    else:
        lines.append(f"投入完了: {imported}件 / スキップ: {skipped}件 / 重複: {duplicates}件 / エラー: {errors}件")

    # 品質警告
    warnings = result.get("warnings", [])
    if warnings:
        lines.append("")
        lines.append("--- 品質警告 ---")
        for w in warnings:
            lines.append(f"  ⚠ {w}")

    if output.strip():
        lines.append("")
        lines.append("--- 詳細 ---")
        lines.append(output.strip())

    # 投入後: 自動diagnosticsレポート（§3.3）
    if not dry_run and imported > 0:
        from cyclegen.mcp.tools.diagnostics import memory_diagnostics
        diag = await memory_diagnostics()
        lines.append("")
        lines.append("--- 投入後 diagnostics ---")
        lines.append(diag)

    return "\n".join(lines)
