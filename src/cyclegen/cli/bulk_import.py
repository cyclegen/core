"""cli/bulk_import.py — 既存ファイル一括登録ツール

設計書 bulk_import_設計書.md §2-§3.1 に基づく実装。
複数ディレクトリ対応、dry-run、フロントマター自動判別（案A）、
Markdown見出し単位のチャンク分割対応。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

from cyclegen import create_memory_system
from cyclegen.core.classifier import AutoLayerClassifier
from cyclegen.core.context import ContextSelector
from cyclegen.core.priority import PriorityManager
from cyclegen.config import DEFAULT_CONTEXTS, load_config, load_contexts, resolve_home
from cyclegen.models import ContextDefinition, compute_content_hash


def discover_files(
    paths: list[Path],
    extensions: list[str],
    max_depth: int | None = None,
) -> list[Path]:
    """対象ファイルを再帰探索する。"""
    files: list[Path] = []
    for p in paths:
        if p.is_file() and p.suffix in extensions:
            files.append(p)
        elif p.is_dir():
            for ext in extensions:
                if max_depth is not None:
                    # 深さ制限付き探索
                    for f in p.rglob(f"*{ext}"):
                        rel = f.relative_to(p)
                        if len(rel.parts) - 1 <= max_depth:
                            files.append(f)
                else:
                    files.extend(p.rglob(f"*{ext}"))
    # 重複除去 + ソート
    return sorted(set(files))


def parse_file(file_path: Path) -> tuple[str, dict]:
    """ファイルを読み込み、(content, metadata) を返す。

    YAMLフロントマター付きの場合はメタ情報を抽出（案A）。
    """
    text = file_path.read_text(encoding="utf-8")

    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
                content = parts[2].strip()
                return content, frontmatter
            except yaml.YAMLError:
                pass

    return text.strip(), {}


def generate_tags(
    file_path: Path,
    base_paths: list[Path],
    extra_tags: list[str],
    frontmatter_tags: list | None,
) -> list[str]:
    """タグを生成する。

    優先順: フロントマターtags + --tagsオプション + パス由来タグ
    """
    tags: list[str] = []

    # フロントマターのtags
    if frontmatter_tags:
        tags.extend(frontmatter_tags)

    # --tags オプション
    tags.extend(extra_tags)

    # パス由来タグ（親ディレクトリ名）
    for base in base_paths:
        try:
            rel = file_path.relative_to(base)
            for part in rel.parent.parts:
                if part not in tags:
                    tags.append(part)
            break
        except ValueError:
            continue

    # 重複除去（順序保持）
    seen: set[str] = set()
    unique: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def estimate_coordinates(
    content: str,
    frontmatter: dict,
    classifier: AutoLayerClassifier,
    priority_mgr: PriorityManager,
    context_selector: ContextSelector,
) -> tuple[int | None, float | None, str | None, bool]:
    """Layer/Priority/Contextを推定する。

    フロントマターに値があればそれを使用（案A）。
    Returns: (layer, priority, context, from_frontmatter)
    """
    fm_layer = frontmatter.get("layer")
    fm_priority = frontmatter.get("priority")
    fm_context = frontmatter.get("context")

    has_fm = any(v is not None for v in [fm_layer, fm_priority, fm_context])

    context = fm_context or context_selector.detect(content)
    layer = fm_layer or classifier.classify(content, context)
    priority = fm_priority if fm_priority is not None else priority_mgr.estimate_initial(content)

    return layer, priority, context, has_fm


# チャンク分割の閾値（設計書§3.1準拠）
CHUNK_MIN_CHARS = 100
CHUNK_MAX_CHARS = 2000

# Markdown見出しパターン（## または ###）
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)


def chunk_markdown(content: str) -> list[str]:
    """Markdownを見出し単位でチャンク分割する。

    設計書§3.1:
    - ## / ### で分割
    - 最小100文字以下は前チャンクに結合
    - 最大2,000文字超は強制分割（改行位置で）
    """
    matches = list(_HEADING_RE.finditer(content))
    if not matches:
        # 見出しがなければ分割しない
        return [content]

    # 見出し位置でテキストを区切る
    raw_chunks: list[str] = []
    # 最初の見出しより前のテキスト
    preamble = content[:matches[0].start()].strip()
    if preamble:
        raw_chunks.append(preamble)

    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        raw_chunks.append(content[start:end].strip())

    # 短すぎるチャンクを前のチャンクに結合
    merged: list[str] = []
    for chunk in raw_chunks:
        if merged and len(chunk) < CHUNK_MIN_CHARS:
            merged[-1] = merged[-1] + "\n\n" + chunk
        else:
            merged.append(chunk)

    # 長すぎるチャンクを強制分割（改行位置で）
    result: list[str] = []
    for chunk in merged:
        if len(chunk) <= CHUNK_MAX_CHARS:
            result.append(chunk)
        else:
            _split_long_chunk(chunk, result)

    return result


def _split_long_chunk(chunk: str, out: list[str]) -> None:
    """2,000文字超のチャンクを改行位置で分割する。"""
    lines = chunk.split("\n")
    current: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if current and current_len + line_len > CHUNK_MAX_CHARS:
            out.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len

    if current:
        out.append("\n".join(current))


# 品質警告の閾値
BIAS_THRESHOLD = 0.8  # 80%以上が同一値なら偏り警告


def check_quality(
    total: int,
    skipped: int,
    layer_dist: dict[int, int],
    priority_classes: dict[str, int],
) -> list[str]:
    """投入品質の警告を生成する（設計書§3.3準拠）。

    Returns: 警告メッセージのリスト（問題なければ空リスト）
    """
    warnings: list[str] = []

    # 空コンテンツ比率（totalベースで判定）
    if total > 0 and skipped / total >= 0.3:
        pct = skipped / total * 100
        warnings.append(f"空コンテンツ率が高い: {pct:.0f}%がスキップされました（{skipped}/{total}件）")

    effective = total - skipped
    if effective <= 0:
        return warnings

    # Layer分布偏り
    for layer, count in layer_dist.items():
        if count / effective >= BIAS_THRESHOLD:
            pct = count / effective * 100
            warnings.append(f"Layer偏り: L{layer}が{pct:.0f}%を占めています（{count}/{effective}件）")

    # Priority分布偏り
    for pclass, count in priority_classes.items():
        if count / effective >= BIAS_THRESHOLD:
            pct = count / effective * 100
            warnings.append(f"Priority偏り: {pclass}が{pct:.0f}%を占めています（{count}/{effective}件）")

    return warnings


def run_import(
    paths: list[Path],
    dry_run: bool = False,
    extra_tags: list[str] | None = None,
    home: Path | None = None,
    max_depth: int | None = None,
    extensions: list[str] | None = None,
    force: bool = False,
    chunk: bool = True,
) -> dict:
    """一括登録を実行する。

    Args:
        force: Trueの場合、重複でも強制投入する
        chunk: Trueの場合、Markdown見出し単位でチャンク分割する

    Returns: 結果サマリ dict
    """
    exts = extensions or [".md", ".txt"]
    extra_tags = extra_tags or []

    # ファイル探索
    files = discover_files(paths, exts, max_depth)
    if not files:
        print("対象ファイルが見つかりません。")
        return {"imported": 0, "skipped": 0, "errors": 0}

    # システム初期化
    config = load_config()
    if home:
        config.home = str(home)
    resolved_home = resolve_home(config)
    contexts = load_contexts(config)

    classifier = AutoLayerClassifier()
    priority_mgr = PriorityManager()
    context_selector = ContextSelector(contexts)

    system = None
    if not dry_run:
        system = create_memory_system(home=resolved_home)

    # 統計
    imported = 0
    skipped = 0
    duplicates = 0
    errors = 0
    layer_dist: dict[int, int] = {}
    priority_classes: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "archive": 0}
    context_dist: dict[str, int] = {}

    header = "=== cyclegen-import ドライラン ===" if dry_run else "=== cyclegen-import 実行 ==="
    print(f"{header}")
    print(f"対象: {len(paths)}パス, {len(files)}ファイル")
    print()

    item_num = 0
    for file_path in files:
        try:
            content, frontmatter = parse_file(file_path)
            if not content:
                item_num += 1
                print(f"  {item_num}. {file_path} → スキップ（空ファイル）")
                skipped += 1
                continue

            # チャンク分割
            if chunk and file_path.suffix == ".md":
                chunks = chunk_markdown(content)
            else:
                chunks = [content]

            base_tags = generate_tags(file_path, paths, extra_tags, frontmatter.get("tags"))
            is_chunked = len(chunks) > 1
            parent_id = None

            for chunk_idx, chunk_content in enumerate(chunks):
                item_num += 1

                layer, priority, context, has_fm = estimate_coordinates(
                    chunk_content, frontmatter if chunk_idx == 0 else {},
                    classifier, priority_mgr, context_selector,
                )
                # チャンクタグ: 親IDで親子関係を保持
                tags = list(base_tags)
                if is_chunked and parent_id:
                    tags.append(f"chunk:{parent_id}")

                source = "フロントマター" if has_fm else "自動判定"
                chunk_label = f" [chunk {chunk_idx + 1}/{len(chunks)}]" if is_chunked else ""

                # 統計更新
                layer_dist[layer] = layer_dist.get(layer, 0) + 1
                p_class = priority_mgr.classify(priority)
                priority_classes[p_class] = priority_classes.get(p_class, 0) + 1
                context_dist[context] = context_dist.get(context, 0) + 1

                # 重複チェック（CYCLE7.2.2）
                content_hash = compute_content_hash(chunk_content)
                is_duplicate = False
                if system is not None and not force:
                    existing = system.find_by_hash(content_hash)
                    if existing is not None:
                        is_duplicate = True

                dup_marker = " [重複→スキップ]" if is_duplicate else ""
                print(
                    f"  {item_num}. {file_path}{chunk_label}{dup_marker}\n"
                    f"     → L{layer}/P{priority:.2f}/C:{context}  "
                    f"tags:{tags}  ({source})"
                )

                if is_duplicate:
                    duplicates += 1
                elif not dry_run and system is not None:
                    memory = system.store(
                        content=chunk_content,
                        layer=layer,
                        priority=priority,
                        context=context,
                        tags=tags,
                    )
                    # 最初のチャンクのIDを親IDとして記録
                    if is_chunked and chunk_idx == 0:
                        parent_id = memory.id
                    imported += 1
                elif dry_run:
                    imported += 1  # dry-runでは「投入予定」としてカウント

        except Exception as e:
            item_num += 1
            print(f"  {item_num}. {file_path} → エラー: {e}")
            errors += 1

    # サマリ
    print()
    print("--- 分布 ---")
    layer_line = "  ".join(f"L{l}:{c}" for l, c in sorted(layer_dist.items()))
    print(f"Layer:    {layer_line}")
    priority_line = "  ".join(f"{k}:{v}" for k, v in priority_classes.items())
    print(f"Priority: {priority_line}")
    context_line = "  ".join(f"{k}:{v}" for k, v in sorted(context_dist.items()))
    print(f"Context:  {context_line}")

    # 品質警告（§3.3）
    total_items = imported + skipped + duplicates
    quality_warnings = check_quality(total_items, skipped, layer_dist, priority_classes)
    if quality_warnings:
        print()
        print("--- 品質警告 ---")
        for w in quality_warnings:
            print(f"  ⚠ {w}")

    print()

    if dry_run:
        print(f"投入予定: {imported}件 / スキップ: {skipped}件 / 重複: {duplicates}件 / エラー: {errors}件")
        print("→ --dry-run を外して再実行すると投入されます")
    else:
        print(f"投入完了: {imported}件 / スキップ: {skipped}件 / 重複: {duplicates}件 / エラー: {errors}件")

    result = {"imported": imported, "skipped": skipped, "duplicates": duplicates, "errors": errors}
    if quality_warnings:
        result["warnings"] = quality_warnings
    return result


def main() -> None:
    """CLIエントリポイント。"""
    parser = argparse.ArgumentParser(
        prog="cyclegen-import",
        description="既存ファイルを3次元記憶システムに一括登録する",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="対象ディレクトリ or ファイル（複数指定可）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="投入せずプレビュー表示",
    )
    parser.add_argument(
        "--tags",
        type=str,
        default="",
        help="全ファイルに付与する共通タグ（カンマ区切り）",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=None,
        help="CycleGenホームディレクトリ（デフォルト: ~/.cyclegen）",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=None,
        help="再帰探索の深さ制限",
    )
    parser.add_argument(
        "--ext",
        type=str,
        default=".md,.txt",
        help="対象ファイル拡張子（カンマ区切り、デフォルト: .md,.txt）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="重複でも強制投入する",
    )
    parser.add_argument(
        "--no-chunk",
        action="store_true",
        help="チャンク分割を無効にする（1ファイル=1記憶）",
    )

    args = parser.parse_args()

    extra_tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    extensions = [e.strip() for e in args.ext.split(",")]

    result = run_import(
        paths=args.paths,
        dry_run=args.dry_run,
        extra_tags=extra_tags,
        home=args.home,
        max_depth=args.max_depth,
        extensions=extensions,
        force=args.force,
        chunk=not args.no_chunk,
    )

    sys.exit(0 if result["errors"] == 0 else 1)


if __name__ == "__main__":
    main()
