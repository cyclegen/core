"""test_layer.py — LayerHierarchy のユニットテスト"""

import pytest

from cyclegen.core.layer import LAYER_DEFINITIONS, LayerHierarchy
from cyclegen.models import LayerKey


class TestLayerDefinitions:
    def test_five_layers(self):
        assert len(LAYER_DEFINITIONS) == 5

    def test_layer_numbers(self):
        assert set(LAYER_DEFINITIONS.keys()) == {1, 2, 3, 4, 5}

    def test_all_keys_are_layer_key(self):
        for defn in LAYER_DEFINITIONS.values():
            assert isinstance(defn["key"], LayerKey)

    def test_strategy_is_layer4(self):
        """§8 #1 確定: Layer 4 = strategy"""
        assert LAYER_DEFINITIONS[4]["key"] == LayerKey.STRATEGY

    def test_cognitive_load_sum_reasonable(self):
        total = sum(d["cognitive_load"] for d in LAYER_DEFINITIONS.values())
        # 各Layerの認知負荷合計は1.0以上（全Layer同時参照は過負荷を意味する）
        assert total > 1.0


class TestLayerHierarchy:
    def test_validate_valid(self, layer_hierarchy: LayerHierarchy):
        for i in range(1, 6):
            assert layer_hierarchy.validate(i) is True

    def test_validate_invalid(self, layer_hierarchy: LayerHierarchy):
        assert layer_hierarchy.validate(0) is False
        assert layer_hierarchy.validate(6) is False
        assert layer_hierarchy.validate(-1) is False

    def test_get_info(self, layer_hierarchy: LayerHierarchy):
        info = layer_hierarchy.get_info(3)
        assert info["key"] == LayerKey.EXPERTISE
        assert "name" in info
        assert "max_memories" in info
        assert "cognitive_load" in info

    def test_get_info_invalid_raises(self, layer_hierarchy: LayerHierarchy):
        with pytest.raises(ValueError, match="Layer must be 1-5"):
            layer_hierarchy.get_info(0)

    def test_get_max_memories(self, layer_hierarchy: LayerHierarchy):
        assert layer_hierarchy.get_max_memories(5) == 10
        assert layer_hierarchy.get_max_memories(4) == 15
        assert layer_hierarchy.get_max_memories(3) == 20
        assert layer_hierarchy.get_max_memories(2) == 12
        assert layer_hierarchy.get_max_memories(1) == 7

    def test_get_key(self, layer_hierarchy: LayerHierarchy):
        assert layer_hierarchy.get_key(5) == LayerKey.METACOGNITION
        assert layer_hierarchy.get_key(1) == LayerKey.FOUNDATION

    def test_get_layer_by_key(self, layer_hierarchy: LayerHierarchy):
        assert layer_hierarchy.get_layer_by_key(LayerKey.METACOGNITION) == 5
        assert layer_hierarchy.get_layer_by_key(LayerKey.FOUNDATION) == 1

    def test_get_layer_by_key_roundtrip(self, layer_hierarchy: LayerHierarchy):
        """全キーで番号→キー→番号のラウンドトリップ確認"""
        for layer_num in range(1, 6):
            key = layer_hierarchy.get_key(layer_num)
            assert layer_hierarchy.get_layer_by_key(key) == layer_num
