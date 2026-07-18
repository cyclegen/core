"""search/engine.py — 2段構造検索エンジン

CYCLE12.7.3: テキスト関連度 × 3次元評価の2段構造。

新パイプライン（embedding_manager + affinity_resolver あり）:
  Stage1: archived除外のみ（context/layerでフィルタしない）
  Stage2: テキスト関連度（semantic + keyword）→ 関連度0を除外
  Stage3: 3次元評価（× context_affinity × layer_weight × priority）
  Stage4: ソート + Miller's 7制限

フォールバック（embedding_manager=None かつ affinity_resolver=None）:
  旧3段パイプライン（context/layerフィルタ + 4要素100点スコアリング）
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from cyclegen.models import Memory, SearchResult, SearchResponse, ScoringWeights

if TYPE_CHECKING:
    from cyclegen.search.context_affinity import ContextAffinityResolver
    from cyclegen.search.embedding import EmbeddingManager


# Phase1 ストップワード（空白・句読点区切り後に除去）
_STOP_WORDS_JA = frozenset([
    "の", "は", "が", "を", "に", "で", "と", "も", "や", "な",
    "する", "した", "して", "から", "まで", "より", "ため", "こと",
    "これ", "それ", "あれ", "この", "その", "ある", "いる", "れる",
    "られる", "です", "ます",
])

_STOP_WORDS_EN = frozenset([
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "in", "on", "at", "to", "for", "of", "with", "and", "or",
    "not", "it", "this", "that", "by", "from", "as",
])

_STOP_WORDS = _STOP_WORDS_JA | _STOP_WORDS_EN

# クエリ分割パターン（空白・句読点・記号）
_SPLIT_PATTERN = re.compile(r"[\s、。,.\-:;!?（）()「」\[\]]+")


class SearchEngine:
    """2段構造検索エンジン（CYCLE12.7.3）。

    新モード: テキスト関連度 × 3次元評価
    フォールバック: 旧4要素100点スコアリング
    """

    def __init__(
        self,
        weights: ScoringWeights | None = None,
        embedding_manager: EmbeddingManager | None = None,
        affinity_resolver: ContextAffinityResolver | None = None,
    ):
        self.weights = weights or ScoringWeights()
        self._embedding_manager = embedding_manager
        self._affinity_resolver = affinity_resolver

    @property
    def is_new_mode(self) -> bool:
        """新モード（2段構造）が有効かどうか。"""
        return self._embedding_manager is not None or self._affinity_resolver is not None

    def search(
        self,
        query: str,
        memories: list[Memory],
        context: str | None = None,
        layer_filter: list[int] | None = None,
        priority_threshold: float = 0.0,
        max_items: int = 7,
    ) -> SearchResponse:
        start = time.monotonic()

        if self.is_new_mode:
            results, total_candidates = self._search_new(
                query, memories, context, layer_filter, priority_threshold,
            )
        else:
            results, total_candidates = self._search_legacy(
                query, memories, context, layer_filter, priority_threshold,
            )

        # Stage4 / Stage3(legacy): ソート + 制限
        results.sort(key=lambda r: r.score, reverse=True)
        results = results[:max_items]

        elapsed_ms = (time.monotonic() - start) * 1000

        return SearchResponse(
            memories=results,
            total_candidates=total_candidates,
            search_time_ms=round(elapsed_ms, 2),
        )

    # ================================================================
    # 新モード: 2段構造（テキスト関連度 × 3次元評価）
    # ================================================================

    def _search_new(
        self,
        query: str,
        memories: list[Memory],
        context: str | None,
        layer_filter: list[int] | None,
        priority_threshold: float,
    ) -> tuple[list[SearchResult], int]:
        # Stage1: archived + priority_threshold のみ（context/layerでフィルタしない）
        candidates = []
        for m in memories:
            if m.archived:
                continue
            if m.coordinates.priority < priority_threshold:
                continue
            # layer_filterは明示指定時のみ適用（ユーザーが意図的に絞り込んだ場合）
            if layer_filter and m.coordinates.layer not in layer_filter:
                continue
            candidates.append(m)
        total_candidates = len(candidates)

        # クエリembedding（1回だけ）
        query_embedding = None
        if self._embedding_manager:
            query_embedding = self._embedding_manager.embed(query)

        keywords = self._extract_keywords(query)
        results = []

        for memory in candidates:
            # Stage2: テキスト関連度
            text_score, text_reasons = self._text_relevance(
                query, keywords, memory, query_embedding,
            )
            if text_score <= 0:
                continue

            # Stage3: 3次元評価
            ctx_aff = self._context_affinity(context, memory)
            lyr_w = self._layer_weight(context, memory)
            pri = memory.coordinates.priority

            three_d = ctx_aff * lyr_w * pri
            final_score = text_score * three_d
            final_score = min(round(final_score, 2), 100.0)

            if final_score <= 0:
                continue

            # reason
            reason_parts = list(text_reasons)
            reason_parts.append(
                f"C:{memory.coordinates.context}({ctx_aff:.2f})"
            )
            reason_parts.append(f"L{memory.coordinates.layer}({lyr_w:.2f})")
            reason_parts.append(f"P:{pri:.2f}")
            reason = " / ".join(reason_parts)

            results.append(SearchResult(
                memory=memory,
                score=final_score,
                source="personal",
                reason=reason,
            ))

        return results, total_candidates

    def _text_relevance(
        self,
        query: str,
        keywords: list[str],
        memory: Memory,
        query_embedding: bytes | None,
    ) -> tuple[float, list[str]]:
        """テキスト関連度を計算する（最大100点）。

        新モード:
          semantic_similarity × 70（セマンティック有効時）
          + keyword_count × 5（上限20）+ 完全一致ボーナス10
        セマンティック無効時:
          keyword_count × 5（上限40）+ 完全一致ボーナス30
        """
        content_lower = memory.content.lower()
        reasons: list[str] = []

        semantic_score = 0.0
        keyword_max = 40  # セマンティック無効時のキーワード配点
        exact_bonus = 30

        # セマンティック類似度
        if query_embedding is not None and memory.embedding is not None:
            from cyclegen.search.embedding import EmbeddingManager

            sim = EmbeddingManager.cosine_similarity(query_embedding, memory.embedding)
            semantic_score = max(sim, 0.0) * 70
            if sim > 0.3:
                reasons.append(f"意味的類似度{sim:.2f}")
            keyword_max = 20  # セマンティック有効時はキーワード配点を下げる
            exact_bonus = 10

        # キーワード一致
        keyword_count = sum(1 for kw in keywords if kw in content_lower)
        kw_score = min(keyword_count * 5, keyword_max)
        if keyword_count > 0:
            reasons.append(f"キーワード{keyword_count}件一致")

        # 完全一致ボーナス
        query_lower = query.lower()
        exact_score = exact_bonus if query_lower in content_lower else 0
        if exact_score > 0:
            reasons.append("完全一致")

        total = semantic_score + kw_score + exact_score
        return total, reasons

    def _context_affinity(self, query_context: str | None, memory: Memory) -> float:
        if self._affinity_resolver:
            return self._affinity_resolver.get_context_affinity(
                query_context, memory.coordinates.context,
            )
        # affinity_resolver なし → context未指定は1.0、指定時は一致1.0/不一致0.5
        if query_context is None:
            return 1.0
        return 1.0 if query_context == memory.coordinates.context else 0.5

    def _layer_weight(self, query_context: str | None, memory: Memory) -> float:
        if self._affinity_resolver:
            return self._affinity_resolver.get_layer_weight(
                query_context, memory.coordinates.layer,
            )
        # affinity_resolver なし → 全Layer均等
        return 1.0

    # ================================================================
    # フォールバック: 旧スコアリング（既存テスト互換）
    # ================================================================

    def _search_legacy(
        self,
        query: str,
        memories: list[Memory],
        context: str | None,
        layer_filter: list[int] | None,
        priority_threshold: float,
    ) -> tuple[list[SearchResult], int]:
        """旧3段パイプライン（CYCLE12.7.2以前互換）。"""
        candidates = self._filter_candidates(
            memories, context, layer_filter, priority_threshold,
        )
        total_candidates = len(candidates)

        keywords = self._extract_keywords(query)
        results = []
        for memory in candidates:
            score, reason = self._score(query, keywords, memory)
            if score > 0:
                results.append(SearchResult(
                    memory=memory,
                    score=score,
                    source="personal",
                    reason=reason,
                ))

        return results, total_candidates

    def _filter_candidates(
        self,
        memories: list[Memory],
        context: str | None,
        layer_filter: list[int] | None,
        priority_threshold: float,
    ) -> list[Memory]:
        """Stage1（旧）: archived=Falseかつ条件一致の候補を返す。"""
        result = []
        for m in memories:
            if m.archived:
                continue
            if m.coordinates.priority < priority_threshold:
                continue
            if context and m.coordinates.context != context:
                continue
            if layer_filter and m.coordinates.layer not in layer_filter:
                continue
            result.append(m)
        return result

    def _score(
        self, query: str, keywords: list[str], memory: Memory
    ) -> tuple[float, str]:
        """Stage2（旧）: スコア計算（0-100）+ 理由テキスト生成。"""
        content_lower = memory.content.lower()
        reasons = []

        keyword_count = sum(1 for kw in keywords if kw in content_lower)
        kw_score = min(keyword_count * 5, self.weights.keyword_frequency)
        if keyword_count > 0:
            reasons.append(f"キーワード{keyword_count}件一致")

        query_lower = query.lower()
        exact_score = self.weights.exact_match if query_lower in content_lower else 0
        if exact_score > 0:
            reasons.append("完全一致")

        priority_score = memory.coordinates.priority * self.weights.priority
        if memory.coordinates.priority >= 0.8:
            reasons.append(f"高Priority({memory.coordinates.priority:.1f})")

        access_score = min(memory.access_count * 2, self.weights.access_count)

        total = kw_score + exact_score + priority_score + access_score
        total = min(total, 100.0)

        reason = " / ".join(reasons) if reasons else "低関連度"

        return round(total, 2), reason

    # ================================================================
    # 共通ユーティリティ
    # ================================================================

    def _extract_keywords(self, query: str) -> list[str]:
        """クエリからキーワード抽出。ストップワード除去。"""
        tokens = _SPLIT_PATTERN.split(query.lower())
        return [t for t in tokens if t and t not in _STOP_WORDS]
