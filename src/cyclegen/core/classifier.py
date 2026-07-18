"""core/classifier.py — 自動Layer分類

実装計画書§4.5: テキスト内容とContextからLayerを自動判定する。
キーワードパターンマッチで判定し、デフォルトは Layer 3（expertise）。
"""

from __future__ import annotations


# Layer自動判定キーワードパターン
# 各タプル: (layer_number, keywords)
_LAYER_PATTERNS: list[tuple[int, list[str]]] = [
    (5, [
        "メタ認知", "学習パターン", "振り返り", "反省", "成長",
        "metacognition", "learning pattern", "reflection",
    ]),
    (4, [
        "戦略", "設計方針", "アーキテクチャ", "計画", "ロードマップ",
        "strategy", "architecture", "roadmap", "design principle",
    ]),
    (3, [
        "技術", "専門", "アルゴリズム", "パターン", "ベストプラクティス",
        "technical", "expertise", "algorithm", "best practice",
    ]),
    (2, [
        "実装", "コード", "手順", "ステップ", "コマンド", "設定",
        "implementation", "code", "step", "command", "config",
    ]),
    (1, [
        "トラブル", "エラー対応", "障害", "復旧", "基盤", "インフラ",
        "trouble", "error handling", "incident", "infrastructure",
    ]),
]

_DEFAULT_LAYER = 3  # expertise


class AutoLayerClassifier:
    """テキスト内容からLayerを自動判定するクラス。"""

    def classify(self, content: str, context: str | None = None) -> int:
        """内容とContextからLayerを自動判定する。

        判定ロジック:
          1. キーワードパターンマッチ（最多一致のLayerを選択）
          2. 同点の場合は上位Layer（より抽象的）を優先
          3. 一致なしの場合: デフォルト Layer 3（expertise）

        Args:
            content: 記憶の内容テキスト
            context: Contextヒント（将来拡張用）

        Returns:
            Layer番号 (1-5)
        """
        scores: dict[int, int] = {}

        for layer_num, keywords in _LAYER_PATTERNS:
            match_count = sum(1 for kw in keywords if kw in content)
            if match_count > 0:
                scores[layer_num] = match_count

        if not scores:
            return _DEFAULT_LAYER

        max_score = max(scores.values())
        candidates = [layer for layer, score in scores.items() if score == max_score]

        # 同点なら上位Layer（より抽象的 = 番号が大きい）を優先
        return max(candidates)
