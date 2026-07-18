"""core/layer.py — Layer階層管理

実装計画書§4.2: 5層抽象度軸の正本定義とバリデーション。
§8 #1 確定: Layer 4 = strategy（planning → strategy に統一）。
"""

from __future__ import annotations

from cyclegen.models import LayerKey


# Layer番号 ↔ キー名の正本マッピング
LAYER_DEFINITIONS: dict[int, dict] = {
    5: {
        "key": LayerKey.METACOGNITION,
        "name": "メタ認知・学習パターン層",
        "max_memories": 10,
        "cognitive_load": 0.15,
    },
    4: {
        "key": LayerKey.STRATEGY,
        "name": "戦略・設計方針層",
        "max_memories": 15,
        "cognitive_load": 0.20,
    },
    3: {
        "key": LayerKey.EXPERTISE,
        "name": "専門知識・技術詳細層",
        "max_memories": 20,
        "cognitive_load": 0.35,
    },
    2: {
        "key": LayerKey.IMPLEMENTATION,
        "name": "実装パターン・手順層",
        "max_memories": 12,
        "cognitive_load": 0.25,
    },
    1: {
        "key": LayerKey.FOUNDATION,
        "name": "基盤・トラブルシュート層",
        "max_memories": 7,
        "cognitive_load": 0.40,
    },
}


class LayerHierarchy:
    """5層抽象度軸の管理クラス。

    Layer番号(1-5)でアクセスし、各層の定義情報を返す。
    """

    def validate(self, layer: int) -> bool:
        """1-5の範囲チェック。"""
        return layer in LAYER_DEFINITIONS

    def get_info(self, layer: int) -> dict:
        """Layer定義を返す。

        Raises:
            ValueError: layer が 1-5 の範囲外の場合
        """
        if not self.validate(layer):
            raise ValueError(f"Layer must be 1-5, got {layer}")
        return LAYER_DEFINITIONS[layer]

    def get_max_memories(self, layer: int) -> int:
        """指定Layerの容量上限を返す。"""
        return self.get_info(layer)["max_memories"]

    def get_key(self, layer: int) -> LayerKey:
        """指定Layerのキー名を返す。"""
        return self.get_info(layer)["key"]

    def get_layer_by_key(self, key: LayerKey) -> int:
        """キー名からLayer番号を逆引きする。"""
        for layer_num, definition in LAYER_DEFINITIONS.items():
            if definition["key"] == key:
                return layer_num
        raise ValueError(f"Unknown layer key: {key}")
