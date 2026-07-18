"""search/context_detector.py — embedding類似度ベースのContext自動判定

CYCLE12.7.8: store時およびreclassify時に、記憶の内容から最適なContextを判定する。
キーワードベースのContextSelector.detect()より精度が高い。
EmbeddingManager未使用時はNoneを返し、呼び出し元がキーワードベースにフォールバックする。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from cyclegen.search.embedding import EmbeddingManager

logger = logging.getLogger(__name__)


class ContextAutoDetector:
    """embedding類似度でContextを自動判定する。

    enterprise_contexts.yamlの各Context description文をembeddingし、
    入力テキストとのコサイン類似度で最適Contextを決定する。
    """

    def __init__(
        self,
        embedding_manager: "EmbeddingManager",
        context_descriptions: dict[str, str],
    ):
        self._embedding_manager = embedding_manager
        self._context_descriptions = context_descriptions
        self._context_embeddings: dict[str, bytes] | None = None

    def _ensure_embeddings(self) -> None:
        """Context説明文のembeddingを遅延初期化する。"""
        if self._context_embeddings is not None:
            return
        texts = list(self._context_descriptions.values())
        names = list(self._context_descriptions.keys())
        embeddings = self._embedding_manager.embed_batch(texts)
        self._context_embeddings = dict(zip(names, embeddings))
        logger.info(
            "ContextAutoDetector: %d Context説明文のembeddingを生成しました",
            len(self._context_embeddings),
        )

    def detect(self, content: str) -> str | None:
        """内容からContextをembedding類似度で判定する。

        Returns:
            最も類似度が高いContext名。判定不能時はNone。
        """
        self._ensure_embeddings()
        assert self._context_embeddings is not None

        content_embedding = self._embedding_manager.embed(content)

        best_context: str | None = None
        best_similarity = -1.0

        for ctx_name, ctx_embedding in self._context_embeddings.items():
            similarity = self._embedding_manager.cosine_similarity(
                content_embedding, ctx_embedding
            )
            if similarity > best_similarity:
                best_similarity = similarity
                best_context = ctx_name

        logger.debug(
            "ContextAutoDetector: best=%s (similarity=%.3f)",
            best_context,
            best_similarity,
        )
        return best_context

    def detect_with_scores(self, content: str) -> list[tuple[str, float]]:
        """全Contextの類似度スコアを降順で返す（reclassify用）。"""
        self._ensure_embeddings()
        assert self._context_embeddings is not None

        content_embedding = self._embedding_manager.embed(content)

        scores: list[tuple[str, float]] = []
        for ctx_name, ctx_embedding in self._context_embeddings.items():
            similarity = self._embedding_manager.cosine_similarity(
                content_embedding, ctx_embedding
            )
            scores.append((ctx_name, similarity))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    @staticmethod
    def from_yaml(
        yaml_path: str | Path,
        embedding_manager: "EmbeddingManager",
    ) -> ContextAutoDetector | None:
        """enterprise_contexts.yamlからDetectorを構築する。

        descriptionフィールドが1つもなければNoneを返す。
        """
        path = Path(yaml_path)
        if not path.exists():
            return None

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if data is None:
            return None

        contexts = data.get("contexts", {})
        descriptions: dict[str, str] = {}
        for name, definition in contexts.items():
            desc = definition.get("description")
            if desc:
                descriptions[name] = desc

        if not descriptions:
            return None

        return ContextAutoDetector(
            embedding_manager=embedding_manager,
            context_descriptions=descriptions,
        )
