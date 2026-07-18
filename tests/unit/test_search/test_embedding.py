"""test_embedding.py — EmbeddingManager のユニットテスト

CYCLE12.7.1: embed→bytes→cosine_similarity ラウンドトリップ、
embed_batch、同一テキスト→1.0、fastembed未インストール時のcreate()。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from cyclegen.search.embedding import EmbeddingManager


# === cosine_similarity テスト（numpy実装、モック不要） ===


class TestCosineSimilarity:
    def test_identical_vectors(self):
        """同一ベクトル → 類似度1.0"""
        vec = np.array([1.0, 2.0, 3.0], dtype="float32").tobytes()
        assert EmbeddingManager.cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        """直交ベクトル → 類似度0.0"""
        a = np.array([1.0, 0.0, 0.0], dtype="float32").tobytes()
        b = np.array([0.0, 1.0, 0.0], dtype="float32").tobytes()
        assert EmbeddingManager.cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        """逆向きベクトル → 類似度-1.0"""
        a = np.array([1.0, 0.0], dtype="float32").tobytes()
        b = np.array([-1.0, 0.0], dtype="float32").tobytes()
        assert EmbeddingManager.cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_similar_vectors(self):
        """近いベクトル → 高い類似度"""
        a = np.array([1.0, 2.0, 3.0], dtype="float32").tobytes()
        b = np.array([1.1, 2.1, 2.9], dtype="float32").tobytes()
        sim = EmbeddingManager.cosine_similarity(a, b)
        assert sim > 0.99

    def test_zero_vector(self):
        """ゼロベクトル → 類似度0.0（ゼロ除算回避）"""
        zero = np.zeros(3, dtype="float32").tobytes()
        nonzero = np.array([1.0, 2.0, 3.0], dtype="float32").tobytes()
        assert EmbeddingManager.cosine_similarity(zero, nonzero) == 0.0


# === embed / embed_batch テスト（FastEmbedモック） ===


def _make_mock_text_embedding():
    """FastEmbedのTextEmbeddingモック。384次元のランダムベクトルを返す。"""
    mock_model = MagicMock()

    def mock_embed(texts):
        np.random.seed(42)
        for text in texts:
            # テキストのハッシュをシードにして再現性を確保
            seed = sum(ord(c) for c in text) % (2**31)
            rng = np.random.RandomState(seed)
            yield rng.randn(384).astype("float32")

    mock_model.embed = mock_embed
    return mock_model


class TestEmbed:
    @patch("cyclegen.search.embedding.TextEmbedding", create=True)
    def test_embed_returns_bytes(self, mock_cls):
        """embed()はfloat32のbytesを返す"""
        # fastembed.TextEmbeddingのimportをパッチ
        mock_cls.return_value = _make_mock_text_embedding()
        with patch.dict("sys.modules", {"fastembed": MagicMock(TextEmbedding=mock_cls)}):
            mgr = EmbeddingManager()
            mgr._model = _make_mock_text_embedding()
            result = mgr.embed("テスト文")

        assert isinstance(result, bytes)
        assert len(result) == 384 * 4  # float32 × 384次元

    def test_embed_roundtrip(self):
        """embed結果をnp.frombufferで復元できる"""
        mgr = EmbeddingManager()
        mgr._model = _make_mock_text_embedding()
        result = mgr.embed("テスト文")

        restored = np.frombuffer(result, dtype="float32")
        assert restored.shape == (384,)

    def test_embed_same_text_same_result(self):
        """同一テキスト → 同一embedding"""
        mgr = EmbeddingManager()
        mgr._model = _make_mock_text_embedding()
        a = mgr.embed("テスト文")
        b = mgr.embed("テスト文")
        assert a == b

    def test_embed_different_text_different_result(self):
        """異なるテキスト → 異なるembedding"""
        mgr = EmbeddingManager()
        mgr._model = _make_mock_text_embedding()
        a = mgr.embed("テスト文A")
        b = mgr.embed("テスト文B")
        assert a != b

    def test_cosine_similarity_same_text(self):
        """同一テキストのembedding間のコサイン類似度が1.0"""
        mgr = EmbeddingManager()
        mgr._model = _make_mock_text_embedding()
        a = mgr.embed("テスト文")
        b = mgr.embed("テスト文")
        assert EmbeddingManager.cosine_similarity(a, b) == pytest.approx(1.0)


class TestEmbedBatch:
    def test_embed_batch_returns_list_of_bytes(self):
        """embed_batch()はbytesのリストを返す"""
        mgr = EmbeddingManager()
        mgr._model = _make_mock_text_embedding()
        results = mgr.embed_batch(["テスト1", "テスト2", "テスト3"])

        assert len(results) == 3
        for r in results:
            assert isinstance(r, bytes)
            assert len(r) == 384 * 4

    def test_embed_batch_consistent_with_single(self):
        """embed_batch()の結果はembed()と一致する"""
        mgr = EmbeddingManager()
        mgr._model = _make_mock_text_embedding()
        single = mgr.embed("テスト1")
        batch = mgr.embed_batch(["テスト1"])[0]
        assert single == batch


# === create() ファクトリメソッド テスト ===


class TestCreate:
    def test_create_returns_none_without_fastembed(self):
        """fastembed未インストール時はNoneを返す"""
        with patch.dict("sys.modules", {"fastembed": None}):
            # importlibのキャッシュをバイパスするためにImportError直接発生
            with patch("builtins.__import__", side_effect=_import_error_for_fastembed):
                result = EmbeddingManager.create()
                assert result is None

    def test_create_returns_manager_with_fastembed(self):
        """fastembed有効時はEmbeddingManagerを返す"""
        mock_fastembed = MagicMock()
        with patch.dict("sys.modules", {"fastembed": mock_fastembed}):
            result = EmbeddingManager.create()
            assert isinstance(result, EmbeddingManager)


def _import_error_for_fastembed(name, *args, **kwargs):
    if name == "fastembed":
        raise ImportError("No module named 'fastembed'")
    return __builtins__.__import__(name, *args, **kwargs) if hasattr(__builtins__, '__import__') else None
