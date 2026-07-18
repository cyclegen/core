"""core/context.py — Context軸管理

実装計画書§4.4: enterprise_contexts.yaml から読み込んだContext定義による
自動検出・バリデーション・Layer検索優先順の提供。
"""

from __future__ import annotations

from cyclegen.models import ContextDefinition


# detectの閾値（スコアがこれ未満なら "implementation" にフォールバック）
_DETECTION_THRESHOLD = 0.5
_DEFAULT_CONTEXT = "implementation"


class ContextSelector:
    """Context軸の管理クラス。

    enterprise_contexts.yaml から読み込んだContext定義で初期化し、
    テキスト内容からContextを自動検出する。
    """

    def __init__(self, contexts: dict[str, ContextDefinition]):
        """Context定義で初期化する。"""
        self.contexts = contexts

    def detect(self, content: str) -> str:
        """内容からContextを自動検出する。

        各Contextのkeywordsとマッチングし、weightで重み付けして
        最高スコアのContextを返す。
        閾値未満の場合は "implementation"（デフォルト）。
        """
        best_context = _DEFAULT_CONTEXT
        best_score = 0.0

        for context_name, definition in self.contexts.items():
            match_count = sum(
                1 for keyword in definition.keywords if keyword in content
            )
            if match_count > 0:
                score = match_count * definition.weight
                if score > best_score:
                    best_score = score
                    best_context = context_name

        if best_score < _DETECTION_THRESHOLD:
            return _DEFAULT_CONTEXT

        return best_context

    def validate(self, context: str) -> bool:
        """定義済みContextかチェック。"""
        return context in self.contexts

    def get_layer_priority(self, context: str) -> list[int]:
        """指定ContextのLayer検索優先順を返す。

        未定義Contextの場合はデフォルト順（3, 4, 2, 5, 1）を返す。
        """
        if context in self.contexts:
            return self.contexts[context].layer_priority
        return [3, 4, 2, 5, 1]

    def list_contexts(self) -> list[str]:
        """定義済みContext名の一覧を返す。"""
        return list(self.contexts.keys())
