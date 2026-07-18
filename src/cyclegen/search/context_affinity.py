"""search/context_affinity.py — Context距離マッチ + Layer適合マッチ

CYCLE12.7.2: 3次元座標を検索に活かす。
Context間の親和度とLayer適合度を乗数（0.5-1.0）として提供する。
"""

from __future__ import annotations

from pathlib import Path

import yaml


class ContextAffinityResolver:
    """Context間の親和度とLayer適合度を解決する。

    検索時に3次元評価の乗数として使用:
      最終スコア = テキスト関連度 × context_affinity × layer_weight × priority
    """

    def __init__(
        self,
        affinity_map: dict[str, dict[str, float]],
        layer_weight_map: dict[str, dict[str, float]],
        default_affinity: float = 0.5,
        default_layer_weight: float = 0.5,
    ):
        self._affinity = affinity_map
        self._layer_weight = layer_weight_map
        self._default_affinity = default_affinity
        self._default_layer_weight = default_layer_weight

    def get_context_affinity(
        self, query_context: str | None, memory_context: str
    ) -> float:
        """検索コンテキストと記憶コンテキスト間の親和度を返す。

        - query_context=None → 1.0（フィルタなし）
        - 同一context → 1.0
        - 定義あり → 定義値（0.5-1.0）
        - 定義なし → default_affinity（0.5）
        """
        if query_context is None:
            return 1.0
        if query_context == memory_context:
            return 1.0
        return self._affinity.get(query_context, {}).get(
            memory_context, self._default_affinity
        )

    def get_layer_weight(
        self, query_context: str | None, memory_layer: int
    ) -> float:
        """検索コンテキストに対する記憶Layerの適合度を返す。

        - query_context=None → 1.0（フラット）
        - 定義あり → 定義値（0.5-1.0）
        - 定義なし → default_layer_weight（0.5）
        """
        if query_context is None:
            return 1.0
        layer_key = f"L{memory_layer}"
        return self._layer_weight.get(query_context, {}).get(
            layer_key, self._default_layer_weight
        )

    @staticmethod
    def from_yaml(yaml_path: str | Path) -> ContextAffinityResolver | None:
        """enterprise_contexts.yamlからResolverを構築する。

        context_affinityまたはlayer_weightセクションがなければNoneを返す。
        """
        path = Path(yaml_path)
        if not path.exists():
            return None

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if data is None:
            return None

        affinity_map = data.get("context_affinity", {})
        layer_weight_map = data.get("layer_weight", {})

        if not affinity_map and not layer_weight_map:
            return None

        return ContextAffinityResolver(
            affinity_map=affinity_map,
            layer_weight_map=layer_weight_map,
        )
