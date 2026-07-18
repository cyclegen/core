"""search/cognitive_load.py — 認知負荷制御

実装計画書§5.2 / DNA-002 / IP-012:
Miller's 7±2 法則に基づき、検索結果を最大N件に制限する。
"""

from __future__ import annotations

from cyclegen.models import SearchResult


class CognitiveLoadManager:
    """Miller's 7±2 法則に基づく認知負荷制御。"""

    def __init__(self, default_max_items: int = 7):
        self.default_max_items = default_max_items  # PoC検証リファイン対象

    def apply_limit(
        self,
        results: list[SearchResult],
        max_items: int | None = None,
    ) -> list[SearchResult]:
        """上位N件に制限する。"""
        limit = max_items or self.default_max_items
        return results[:limit]
