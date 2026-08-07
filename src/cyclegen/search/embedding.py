"""search/embedding.py — FastEmbedベースのembedding生成・類似度計算

CYCLE12.7.1: セマンティック検索の基盤。
遅延初期化でfastembed未インストール時もエラーなく動作する。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class EmbeddingManager:
    """FastEmbedを使ったembedding生成・類似度計算。

    遅延初期化: 初回のembed()呼び出し時にモデルをダウンロード・ロードする。
    fastembed未インストール時はcreate()がNoneを返す。
    """

    def __init__(self, model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"):
        self._model_name = model_name
        self._model = None  # 遅延初期化

    @property
    def model_id(self) -> str:
        """このmanagerが作るembeddingの出所を表す識別子（CYCLE19.2 / A8）。

        `<model_name>@fastembed<version>` の形。記憶に一緒に保存し、
        あとから「このembeddingは何で作られたか」を問い合わせられるようにする。

        なぜ版まで含めるか:
        fastembedは同じmodel_nameのままプーリング方式を変えたことがある
        （0.5.1→0.6 で paraphrase-multilingual-MiniLM-L12-v2 が CLS → mean pooling）。
        model_nameだけでは「同じモデル」に見えてしまい、
        保存済みembeddingとクエリembeddingが別空間になったことを検知できない。

        fastembedが入っていない環境では版を "unknown" とする（呼び出し側は
        embeddingを作れないので、この値が記憶に載ることは通常ない）。
        """
        try:
            from importlib.metadata import version

            fe_version = version("fastembed")
        except Exception:
            fe_version = "unknown"
        return f"{self._model_name}@fastembed{fe_version}"

    def _ensure_model(self) -> None:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self._model_name)
            logger.info("EmbeddingManager: モデル '%s' をロードしました", self._model_name)

    def embed(self, text: str) -> bytes:
        """テキストをembeddingベクトル（float32 bytes）に変換する。"""
        self._ensure_model()
        embedding = list(self._model.embed([text]))[0]
        return embedding.astype("float32").tobytes()

    def embed_batch(self, texts: list[str]) -> list[bytes]:
        """複数テキストを一括でembeddingに変換する。"""
        self._ensure_model()
        embeddings = list(self._model.embed(texts))
        return [e.astype("float32").tobytes() for e in embeddings]

    @staticmethod
    def cosine_similarity(a: bytes, b: bytes) -> float:
        """2つのembedding（float32 bytes）間のコサイン類似度を計算する。"""
        import numpy as np

        va = np.frombuffer(a, dtype="float32")
        vb = np.frombuffer(b, dtype="float32")
        dot = np.dot(va, vb)
        norm = np.linalg.norm(va) * np.linalg.norm(vb)
        if norm == 0:
            return 0.0
        return float(dot / norm)

    @staticmethod
    def create(model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2") -> EmbeddingManager | None:
        """ファクトリメソッド。fastembed未インストール時はNoneを返す。"""
        try:
            import fastembed  # noqa: F401

            return EmbeddingManager(model_name=model_name)
        except ImportError:
            logger.info(
                "fastembed未インストール。セマンティック検索は無効です。"
                " `pip install cyclegen[semantic]` でインストールしてください。"
            )
            return None
