"""test_bulk_import.py — bulk_import のユニットテスト"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cyclegen.cli.bulk_import import (
    check_quality,
    discover_files,
    estimate_coordinates,
    generate_tags,
    parse_file,
    run_import,
)
from cyclegen.core.classifier import AutoLayerClassifier
from cyclegen.core.context import ContextSelector
from cyclegen.core.priority import PriorityManager
from cyclegen.config import DEFAULT_CONTEXTS
from cyclegen.models import ContextDefinition


@pytest.fixture
def contexts() -> dict[str, ContextDefinition]:
    return {name: ContextDefinition(**d) for name, d in DEFAULT_CONTEXTS.items()}


@pytest.fixture
def classifier() -> AutoLayerClassifier:
    return AutoLayerClassifier()


@pytest.fixture
def priority_mgr() -> PriorityManager:
    return PriorityManager()


@pytest.fixture
def context_selector(contexts) -> ContextSelector:
    return ContextSelector(contexts)


# === discover_files ===


class TestDiscoverFiles:
    def test_find_md_files(self, tmp_path):
        (tmp_path / "a.md").write_text("content a")
        (tmp_path / "b.txt").write_text("content b")
        (tmp_path / "c.py").write_text("content c")

        files = discover_files([tmp_path], [".md", ".txt"])
        names = [f.name for f in files]
        assert "a.md" in names
        assert "b.txt" in names
        assert "c.py" not in names

    def test_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "root.md").write_text("root")
        (sub / "child.md").write_text("child")

        files = discover_files([tmp_path], [".md"])
        assert len(files) == 2

    def test_max_depth(self, tmp_path):
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        (tmp_path / "root.md").write_text("root")
        (tmp_path / "a" / "level1.md").write_text("level1")
        (sub / "level2.md").write_text("level2")

        files = discover_files([tmp_path], [".md"], max_depth=1)
        names = [f.name for f in files]
        assert "root.md" in names
        assert "level1.md" in names
        assert "level2.md" not in names

    def test_multiple_paths(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "a.md").write_text("a")
        (dir_b / "b.md").write_text("b")

        files = discover_files([dir_a, dir_b], [".md"])
        assert len(files) == 2

    def test_empty_dir(self, tmp_path):
        files = discover_files([tmp_path], [".md"])
        assert files == []

    def test_single_file(self, tmp_path):
        f = tmp_path / "single.md"
        f.write_text("content")
        files = discover_files([f], [".md"])
        assert len(files) == 1


# === parse_file ===


class TestParseFile:
    def test_plain_text(self, tmp_path):
        f = tmp_path / "plain.md"
        f.write_text("これは普通のテキスト")
        content, fm = parse_file(f)
        assert content == "これは普通のテキスト"
        assert fm == {}

    def test_with_frontmatter(self, tmp_path):
        f = tmp_path / "with_fm.md"
        f.write_text("---\nlayer: 4\npriority: 0.9\ncontext: planning\ntags:\n  - design\n---\n本文テキスト")
        content, fm = parse_file(f)
        assert content == "本文テキスト"
        assert fm["layer"] == 4
        assert fm["priority"] == 0.9
        assert fm["context"] == "planning"
        assert fm["tags"] == ["design"]

    def test_partial_frontmatter(self, tmp_path):
        f = tmp_path / "partial.md"
        f.write_text("---\nlayer: 3\n---\n本文のみ")
        content, fm = parse_file(f)
        assert content == "本文のみ"
        assert fm["layer"] == 3
        assert "priority" not in fm

    def test_invalid_frontmatter(self, tmp_path):
        f = tmp_path / "bad.md"
        f.write_text("---\n: invalid yaml [[[[\n---\nfallback")
        content, fm = parse_file(f)
        # YAMLパースエラー時はフロントマターなしとして扱う
        assert fm == {} or content != ""


# === generate_tags ===


class TestGenerateTags:
    def test_from_path(self, tmp_path):
        base = tmp_path / "docs"
        sub = base / "dna"
        sub.mkdir(parents=True)
        f = sub / "dna001.md"
        f.write_text("x")

        tags = generate_tags(f, [base], [], None)
        assert "dna" in tags

    def test_extra_tags(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("x")
        tags = generate_tags(f, [tmp_path], ["custom", "tag"], None)
        assert "custom" in tags
        assert "tag" in tags

    def test_frontmatter_tags(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("x")
        tags = generate_tags(f, [tmp_path], [], ["fm_tag"])
        assert "fm_tag" in tags

    def test_deduplication(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("x")
        tags = generate_tags(f, [tmp_path], ["dup", "dup"], ["dup"])
        assert tags.count("dup") == 1


# === estimate_coordinates ===


class TestEstimateCoordinates:
    def test_all_auto(self, classifier, priority_mgr, context_selector):
        layer, priority, context, has_fm = estimate_coordinates(
            "アーキテクチャの戦略を設計する方針を決めた",
            {},
            classifier, priority_mgr, context_selector,
        )
        assert layer == 4  # strategy keywords
        assert priority == 0.5  # CYCLE12.7: 全件0.5固定
        assert has_fm is False

    def test_frontmatter_overrides(self, classifier, priority_mgr, context_selector):
        layer, priority, context, has_fm = estimate_coordinates(
            "何でもいい内容",
            {"layer": 5, "priority": 1.0, "context": "learning"},
            classifier, priority_mgr, context_selector,
        )
        assert layer == 5
        assert priority == 1.0
        assert context == "learning"
        assert has_fm is True

    def test_partial_frontmatter(self, classifier, priority_mgr, context_selector):
        layer, priority, context, has_fm = estimate_coordinates(
            "バグを修正する",
            {"layer": 1},
            classifier, priority_mgr, context_selector,
        )
        assert layer == 1  # フロントマターから
        assert context == "debugging"  # 自動判定
        assert has_fm is True


# === run_import (dry-run) ===


class TestRunImportDryRun:
    def test_dry_run(self, tmp_path, capsys):
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "memo1.md").write_text("Pythonでモデルを実装した")
        (seed / "memo2.md").write_text("アーキテクチャの戦略を決めた")
        (seed / "memo3.txt").write_text("とりあえずメモ")

        result = run_import(
            paths=[seed],
            dry_run=True,
            home=tmp_path / "home",
        )
        assert result["imported"] == 3
        assert result["skipped"] == 0
        assert result["errors"] == 0

        output = capsys.readouterr().out
        assert "ドライラン" in output
        assert "3ファイル" in output
        assert "投入予定: 3件" in output

    def test_dry_run_empty_dir(self, tmp_path, capsys):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = run_import(paths=[empty], dry_run=True, home=tmp_path / "home")
        assert result["imported"] == 0

    def test_dry_run_skips_empty_files(self, tmp_path, capsys):
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "empty.md").write_text("")
        (seed / "notempty.md").write_text("内容あり")

        result = run_import(paths=[seed], dry_run=True, home=tmp_path / "home")
        assert result["imported"] == 1
        assert result["skipped"] == 1

    def test_dry_run_multiple_dirs(self, tmp_path, capsys):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "a.md").write_text("内容A")
        (dir_b / "b.md").write_text("内容B")

        result = run_import(
            paths=[dir_a, dir_b],
            dry_run=True,
            home=tmp_path / "home",
        )
        assert result["imported"] == 2

        output = capsys.readouterr().out
        assert "2パス" in output


# === run_import (actual) ===


class TestRunImportActual:
    def test_actual_import(self, tmp_path, capsys):
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "memo1.md").write_text("Python実装パターン")
        (seed / "memo2.md").write_text("設計方針の戦略")

        home = tmp_path / "cyclegen_home"
        result = run_import(
            paths=[seed],
            dry_run=False,
            home=home,
        )
        assert result["imported"] == 2
        assert result["errors"] == 0

        # mdファイルが生成されている
        memories_dir = home / "memories"
        md_files = list(memories_dir.glob("*.md"))
        assert len(md_files) == 2

        output = capsys.readouterr().out
        assert "投入完了: 2件" in output

    def test_import_with_frontmatter(self, tmp_path, capsys):
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "fm.md").write_text(
            "---\nlayer: 5\npriority: 0.95\ncontext: learning\ntags:\n  - meta\n---\nメタ認知の記録"
        )

        home = tmp_path / "cyclegen_home"
        result = run_import(paths=[seed], dry_run=False, home=home)
        assert result["imported"] == 1

        output = capsys.readouterr().out
        assert "L5" in output
        assert "P0.95" in output
        assert "フロントマター" in output

    def test_import_with_tags(self, tmp_path, capsys):
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "test.md").write_text("タグテスト")

        home = tmp_path / "cyclegen_home"
        result = run_import(
            paths=[seed],
            dry_run=False,
            extra_tags=["imported", "test"],
            home=home,
        )
        assert result["imported"] == 1

        output = capsys.readouterr().out
        assert "imported" in output


# === 重複検知（CYCLE7.2.2） ===


class TestDuplicateDetection:
    def test_duplicate_skipped(self, tmp_path, capsys):
        """同一ファイルを2回importすると2回目はスキップ"""
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "memo.md").write_text("重複テスト用コンテンツ")

        home = tmp_path / "cyclegen_home"
        # 1回目
        result1 = run_import(paths=[seed], dry_run=False, home=home)
        assert result1["imported"] == 1
        assert result1["duplicates"] == 0

        # 2回目（同一内容）
        capsys.readouterr()  # バッファクリア
        result2 = run_import(paths=[seed], dry_run=False, home=home)
        assert result2["imported"] == 0
        assert result2["duplicates"] == 1

        output = capsys.readouterr().out
        assert "重複→スキップ" in output

    def test_duplicate_force(self, tmp_path, capsys):
        """--forceで重複でも投入される"""
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "memo.md").write_text("force テスト用コンテンツ")

        home = tmp_path / "cyclegen_home"
        run_import(paths=[seed], dry_run=False, home=home)

        # 2回目（force）
        capsys.readouterr()
        result2 = run_import(paths=[seed], dry_run=False, home=home, force=True)
        assert result2["imported"] == 1
        assert result2["duplicates"] == 0

    def test_changed_content_not_duplicate(self, tmp_path, capsys):
        """内容変更後は重複にならない"""
        seed = tmp_path / "seed"
        seed.mkdir()
        memo = seed / "memo.md"
        memo.write_text("初版の内容")

        home = tmp_path / "cyclegen_home"
        run_import(paths=[seed], dry_run=False, home=home)

        # 内容を変更
        memo.write_text("改訂後の内容")
        capsys.readouterr()
        result2 = run_import(paths=[seed], dry_run=False, home=home)
        assert result2["imported"] == 1
        assert result2["duplicates"] == 0


# === チャンク分割（CYCLE7.2.4） ===


class TestChunkMarkdown:
    def test_no_headings_no_split(self):
        """見出しなしのテキストは分割されない"""
        from cyclegen.cli.bulk_import import chunk_markdown
        content = "見出しのないプレーンテキスト。\n改行があっても分割されない。"
        chunks = chunk_markdown(content)
        assert len(chunks) == 1
        assert chunks[0] == content

    def test_split_by_h2(self):
        """## 見出しで分割される"""
        from cyclegen.cli.bulk_import import chunk_markdown
        content = "## セクション1\nセクション1の内容です。" + "あ" * 100 + "\n\n## セクション2\nセクション2の内容です。" + "い" * 100
        chunks = chunk_markdown(content)
        assert len(chunks) == 2
        assert "セクション1" in chunks[0]
        assert "セクション2" in chunks[1]

    def test_split_by_h3(self):
        """### 見出しでも分割される"""
        from cyclegen.cli.bulk_import import chunk_markdown
        content = "### 小セクション1\n内容1です。" + "あ" * 100 + "\n\n### 小セクション2\n内容2です。" + "い" * 100
        chunks = chunk_markdown(content)
        assert len(chunks) == 2

    def test_short_chunk_merged(self):
        """100文字以下のチャンクは前のチャンクに結合"""
        from cyclegen.cli.bulk_import import chunk_markdown
        content = "## メインセクション\n十分な長さの内容。" + "あ" * 100 + "\n\n## 短い\n短い"
        chunks = chunk_markdown(content)
        assert len(chunks) == 1  # 短いチャンクが前に結合
        assert "メインセクション" in chunks[0]
        assert "短い" in chunks[0]

    def test_long_chunk_force_split(self):
        """2,000文字超のチャンクは強制分割"""
        from cyclegen.cli.bulk_import import chunk_markdown
        content = "## 巨大セクション\n" + "あ\n" * 2500
        chunks = chunk_markdown(content)
        assert len(chunks) >= 2
        for c in chunks:
            assert len(c) <= 2100  # 若干の余裕

    def test_preamble_preserved(self):
        """見出し前のテキスト（プリアンブル）が保持される"""
        from cyclegen.cli.bulk_import import chunk_markdown
        content = "導入文です。" + "あ" * 100 + "\n\n## セクション1\n内容1。" + "い" * 100
        chunks = chunk_markdown(content)
        assert len(chunks) == 2
        assert "導入文" in chunks[0]
        assert "セクション1" in chunks[1]


class TestChunkImport:
    def test_chunk_import_creates_multiple_memories(self, tmp_path, capsys):
        """チャンク分割で1ファイルから複数記憶が作られる"""
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "doc.md").write_text(
            "## セクション1\nセクション1の詳細内容。" + "あ" * 100
            + "\n\n## セクション2\nセクション2の詳細内容。" + "い" * 100
        )

        home = tmp_path / "cyclegen_home"
        result = run_import(paths=[seed], dry_run=False, home=home, chunk=True)
        assert result["imported"] == 2

        output = capsys.readouterr().out
        assert "chunk 1/2" in output
        assert "chunk 2/2" in output

    def test_no_chunk_option(self, tmp_path, capsys):
        """chunk=Falseで1ファイル=1記憶"""
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "doc.md").write_text(
            "## セクション1\nセクション1の内容。" + "あ" * 100
            + "\n\n## セクション2\nセクション2の内容。" + "い" * 100
        )

        home = tmp_path / "cyclegen_home"
        result = run_import(paths=[seed], dry_run=False, home=home, chunk=False)
        assert result["imported"] == 1

    def test_chunk_parent_tag(self, tmp_path, capsys):
        """2番目以降のチャンクに親IDタグが付く"""
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "doc.md").write_text(
            "## パート1\nパート1の詳細内容。" + "あ" * 100
            + "\n\n## パート2\nパート2の詳細内容。" + "い" * 100
        )

        home = tmp_path / "cyclegen_home"
        result = run_import(paths=[seed], dry_run=False, home=home, chunk=True)
        assert result["imported"] == 2

        output = capsys.readouterr().out
        assert "chunk:" in output  # 2番目のチャンクに親IDタグ

    def test_txt_not_chunked(self, tmp_path, capsys):
        """.txtファイルはチャンク分割されない"""
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "doc.txt").write_text(
            "## セクション1\n内容1。" + "あ" * 100
            + "\n\n## セクション2\n内容2。" + "い" * 100
        )

        home = tmp_path / "cyclegen_home"
        result = run_import(paths=[seed], dry_run=False, home=home, chunk=True)
        assert result["imported"] == 1  # .txtは分割されない

    def test_chunk_dry_run(self, tmp_path, capsys):
        """ドライランでチャンク分割プレビュー"""
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "doc.md").write_text(
            "## A\n" + "あ" * 150 + "\n\n## B\n" + "い" * 150
        )

        result = run_import(paths=[seed], dry_run=True, chunk=True)
        assert result["imported"] == 2

        output = capsys.readouterr().out
        assert "chunk 1/2" in output
        assert "chunk 2/2" in output


# === 品質警告（CYCLE7.2.5） ===


class TestCheckQuality:
    def test_no_warnings_balanced(self):
        """バランスの良い分布では警告なし"""
        warnings = check_quality(
            total=10, skipped=0,
            layer_dist={1: 3, 2: 3, 3: 4},
            priority_classes={"high": 3, "medium": 4, "low": 3, "archive": 0},
        )
        assert len(warnings) == 0

    def test_layer_bias_warning(self):
        """80%以上が同一LayerでLayer偏り警告"""
        warnings = check_quality(
            total=10, skipped=0,
            layer_dist={3: 9, 4: 1},
            priority_classes={"high": 0, "medium": 10, "low": 0, "archive": 0},
        )
        layer_warns = [w for w in warnings if "Layer偏り" in w]
        assert len(layer_warns) == 1
        assert "L3" in layer_warns[0]
        assert "90%" in layer_warns[0]

    def test_priority_bias_warning(self):
        """80%以上が同一Priority classでPriority偏り警告"""
        warnings = check_quality(
            total=5, skipped=0,
            layer_dist={1: 2, 2: 3},
            priority_classes={"high": 0, "medium": 5, "low": 0, "archive": 0},
        )
        priority_warns = [w for w in warnings if "Priority偏り" in w]
        assert len(priority_warns) == 1
        assert "medium" in priority_warns[0]

    def test_empty_content_warning(self):
        """30%以上が空コンテンツで警告"""
        warnings = check_quality(
            total=10, skipped=4,
            layer_dist={3: 6},
            priority_classes={"high": 0, "medium": 6, "low": 0, "archive": 0},
        )
        empty_warns = [w for w in warnings if "空コンテンツ" in w]
        assert len(empty_warns) == 1

    def test_no_warning_below_threshold(self):
        """閾値未満では警告なし"""
        warnings = check_quality(
            total=10, skipped=2,
            layer_dist={3: 7, 4: 1},
            priority_classes={"high": 2, "medium": 6, "low": 0, "archive": 0},
        )
        # L3が7/8=87.5%なので警告あり、medium=6/8=75%は警告なし
        assert any("Layer偏り" in w for w in warnings)
        assert not any("Priority偏り" in w for w in warnings)
        assert not any("空コンテンツ" in w for w in warnings)

    def test_zero_effective_no_crash(self):
        """全件スキップでもクラッシュしない"""
        warnings = check_quality(
            total=5, skipped=5,
            layer_dist={},
            priority_classes={"high": 0, "medium": 0, "low": 0, "archive": 0},
        )
        # 空コンテンツ率100%で警告あり
        assert any("空コンテンツ" in w for w in warnings)


class TestQualityInImport:
    def test_warnings_in_output(self, tmp_path, capsys):
        """偏ったデータでrun_importすると品質警告が出力される"""
        seed = tmp_path / "seed"
        seed.mkdir()
        # 全て同じ短い内容 → 同一Layer/Priorityに集中
        for i in range(5):
            (seed / f"memo{i}.md").write_text(f"メモ{i}の内容")

        result = run_import(paths=[seed], dry_run=True, chunk=False)
        output = capsys.readouterr().out
        # 全件同一Layerなので偏り警告が出るはず
        assert "品質警告" in output

    def test_warnings_in_result(self, tmp_path, capsys):
        """結果dictにwarningsが含まれる"""
        seed = tmp_path / "seed"
        seed.mkdir()
        for i in range(5):
            (seed / f"memo{i}.md").write_text(f"メモ{i}")

        result = run_import(paths=[seed], dry_run=True, chunk=False)
        assert "warnings" in result
        assert len(result["warnings"]) > 0
