"""test_context_detector.py — ContextAutoDetector のユニットテスト

CYCLE12.7.8: embedding類似度ベースのContext自動判定。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from cyclegen.search.context_detector import ContextAutoDetector


# === モックEmbeddingManager ===


def _make_mock_embedding_manager():
    """テスト用のモックEmbeddingManagerを作成する。

    各テキストのembeddingとして固定バイト列を返す。
    cosine_similarityはテキスト内容に基づく固定値を返す。
    """
    mgr = MagicMock()
    _call_count = {"embed": 0}

    def mock_embed(text: str) -> bytes:
        # 各テキストごとに異なるembeddingを返す
        _call_count["embed"] += 1
        return f"emb:{text[:30]}".encode("utf-8")

    def mock_embed_batch(texts: list[str]) -> list[bytes]:
        return [mock_embed(t) for t in texts]

    mgr.embed = mock_embed
    mgr.embed_batch = mock_embed_batch
    return mgr


SAMPLE_DESCRIPTIONS = {
    "planning": "計画策定・設計方針・アーキテクチャ設計",
    "implementation": "コード実装・機能開発・テスト作成",
    "debugging": "バグ調査・エラー解析・問題の根本原因特定",
    "research": "市場調査・技術調査・競合分析",
}


class TestContextAutoDetectorInit:
    """ContextAutoDetector初期化テスト。"""

    def test_create_with_descriptions(self):
        mgr = _make_mock_embedding_manager()
        detector = ContextAutoDetector(mgr, SAMPLE_DESCRIPTIONS)
        assert detector is not None

    def test_lazy_embedding_initialization(self):
        mgr = _make_mock_embedding_manager()
        detector = ContextAutoDetector(mgr, SAMPLE_DESCRIPTIONS)
        # 初期化直後はembeddingが生成されていない
        assert detector._context_embeddings is None

    def test_embeddings_generated_on_first_detect(self):
        mgr = _make_mock_embedding_manager()
        mgr.cosine_similarity = MagicMock(return_value=0.5)
        detector = ContextAutoDetector(mgr, SAMPLE_DESCRIPTIONS)
        detector.detect("テストコンテンツ")
        # detect後にembeddingが生成されている
        assert detector._context_embeddings is not None
        assert len(detector._context_embeddings) == 4


class TestContextAutoDetectorDetect:
    """detect()メソッドのテスト。"""

    def test_returns_best_matching_context(self):
        mgr = _make_mock_embedding_manager()
        similarities = {
            "planning": 0.8,
            "implementation": 0.3,
            "debugging": 0.2,
            "research": 0.1,
        }

        def mock_similarity(a: bytes, b: bytes) -> float:
            for name, emb in detector._context_embeddings.items():
                if b == emb:
                    return similarities.get(name, 0.0)
            return 0.0

        mgr.cosine_similarity = mock_similarity
        detector = ContextAutoDetector(mgr, SAMPLE_DESCRIPTIONS)
        result = detector.detect("設計方針を策定する")
        assert result == "planning"

    def test_returns_implementation_for_code_content(self):
        mgr = _make_mock_embedding_manager()
        similarities = {
            "planning": 0.2,
            "implementation": 0.9,
            "debugging": 0.4,
            "research": 0.1,
        }

        def mock_similarity(a: bytes, b: bytes) -> float:
            for name, emb in detector._context_embeddings.items():
                if b == emb:
                    return similarities.get(name, 0.0)
            return 0.0

        mgr.cosine_similarity = mock_similarity
        detector = ContextAutoDetector(mgr, SAMPLE_DESCRIPTIONS)
        result = detector.detect("Pythonでクラスを実装した")
        assert result == "implementation"

    def test_returns_some_context_always(self):
        """常にいずれかのContextを返す（Noneにならない）。"""
        mgr = _make_mock_embedding_manager()
        mgr.cosine_similarity = MagicMock(return_value=0.1)
        detector = ContextAutoDetector(mgr, SAMPLE_DESCRIPTIONS)
        result = detector.detect("完全に関係ない文章")
        assert result is not None
        assert result in SAMPLE_DESCRIPTIONS


class TestContextAutoDetectorDetectWithScores:
    """detect_with_scores()メソッドのテスト。"""

    def test_returns_all_contexts_sorted(self):
        mgr = _make_mock_embedding_manager()
        similarities = {
            "planning": 0.3,
            "implementation": 0.9,
            "debugging": 0.5,
            "research": 0.7,
        }

        def mock_similarity(a: bytes, b: bytes) -> float:
            for name, emb in detector._context_embeddings.items():
                if b == emb:
                    return similarities.get(name, 0.0)
            return 0.0

        mgr.cosine_similarity = mock_similarity
        detector = ContextAutoDetector(mgr, SAMPLE_DESCRIPTIONS)
        scores = detector.detect_with_scores("テストコンテンツ")

        assert len(scores) == 4
        # 降順にソートされている
        assert scores[0][0] == "implementation"
        assert scores[0][1] == 0.9
        assert scores[1][0] == "research"
        assert scores[1][1] == 0.7
        assert scores[-1][0] == "planning"

    def test_scores_are_float(self):
        mgr = _make_mock_embedding_manager()
        mgr.cosine_similarity = MagicMock(return_value=0.42)
        detector = ContextAutoDetector(mgr, SAMPLE_DESCRIPTIONS)
        scores = detector.detect_with_scores("テスト")
        for name, sim in scores:
            assert isinstance(name, str)
            assert isinstance(sim, float)


class TestContextAutoDetectorFromYaml:
    """from_yaml()ファクトリメソッドのテスト。"""

    def test_loads_from_yaml(self, tmp_path):
        yaml_data = {
            "contexts": {
                "planning": {
                    "weight": 1.0,
                    "description": "計画策定",
                    "keywords": ["計画"],
                },
                "implementation": {
                    "weight": 1.0,
                    "description": "コード実装",
                    "keywords": ["実装"],
                },
            }
        }
        yaml_file = tmp_path / "contexts.yaml"
        yaml_file.write_text(yaml.dump(yaml_data, allow_unicode=True))

        mgr = _make_mock_embedding_manager()
        detector = ContextAutoDetector.from_yaml(yaml_file, mgr)
        assert detector is not None
        assert len(detector._context_descriptions) == 2

    def test_returns_none_for_no_descriptions(self, tmp_path):
        yaml_data = {
            "contexts": {
                "planning": {"weight": 1.0, "keywords": ["計画"]},
            }
        }
        yaml_file = tmp_path / "contexts.yaml"
        yaml_file.write_text(yaml.dump(yaml_data))

        mgr = _make_mock_embedding_manager()
        detector = ContextAutoDetector.from_yaml(yaml_file, mgr)
        assert detector is None

    def test_returns_none_for_missing_file(self):
        mgr = _make_mock_embedding_manager()
        detector = ContextAutoDetector.from_yaml("/nonexistent/path.yaml", mgr)
        assert detector is None

    def test_returns_none_for_empty_yaml(self, tmp_path):
        yaml_file = tmp_path / "contexts.yaml"
        yaml_file.write_text("")

        mgr = _make_mock_embedding_manager()
        detector = ContextAutoDetector.from_yaml(yaml_file, mgr)
        assert detector is None

    def test_partial_descriptions(self, tmp_path):
        """一部のContextにのみdescriptionがある場合。"""
        yaml_data = {
            "contexts": {
                "planning": {
                    "weight": 1.0,
                    "description": "計画策定",
                    "keywords": ["計画"],
                },
                "implementation": {
                    "weight": 1.0,
                    "keywords": ["実装"],  # descriptionなし
                },
            }
        }
        yaml_file = tmp_path / "contexts.yaml"
        yaml_file.write_text(yaml.dump(yaml_data, allow_unicode=True))

        mgr = _make_mock_embedding_manager()
        detector = ContextAutoDetector.from_yaml(yaml_file, mgr)
        assert detector is not None
        assert len(detector._context_descriptions) == 1
        assert "planning" in detector._context_descriptions
