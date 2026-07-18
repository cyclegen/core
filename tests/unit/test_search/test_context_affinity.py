"""test_context_affinity.py — ContextAffinityResolver のユニットテスト

CYCLE12.7.2: Context距離マッチ + Layer適合マッチ。
同一context→1.0、近いcontext→高値、遠い→低値、未定義→デフォルト、
Layer適合度、context未指定時→1.0。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cyclegen.search.context_affinity import ContextAffinityResolver


# === テスト用データ ===

SAMPLE_AFFINITY = {
    "planning": {
        "design": 0.9,
        "strategy": 0.85,
        "implementation": 0.7,
        "review": 0.7,
        "research": 0.6,
    },
    "implementation": {
        "design": 0.8,
        "debugging": 0.8,
        "review": 0.75,
        "planning": 0.65,
    },
}

SAMPLE_LAYER_WEIGHT = {
    "implementation": {
        "L1": 0.6,
        "L2": 1.0,
        "L3": 0.85,
        "L4": 0.6,
        "L5": 0.5,
    },
    "planning": {
        "L1": 0.5,
        "L2": 0.6,
        "L3": 0.85,
        "L4": 1.0,
        "L5": 0.7,
    },
    "research": {
        "L1": 0.55,
        "L2": 0.6,
        "L3": 0.75,
        "L4": 0.85,
        "L5": 1.0,
    },
}


@pytest.fixture
def resolver() -> ContextAffinityResolver:
    return ContextAffinityResolver(
        affinity_map=SAMPLE_AFFINITY,
        layer_weight_map=SAMPLE_LAYER_WEIGHT,
    )


# === Context Affinity テスト ===


class TestContextAffinity:
    def test_same_context_returns_1(self, resolver):
        """同一context → 1.0"""
        assert resolver.get_context_affinity("planning", "planning") == 1.0

    def test_close_context_high_value(self, resolver):
        """近いcontext → 高い親和度"""
        assert resolver.get_context_affinity("planning", "design") == 0.9

    def test_moderate_context(self, resolver):
        """中程度の距離 → 中程度の親和度"""
        assert resolver.get_context_affinity("planning", "implementation") == 0.7

    def test_far_context(self, resolver):
        """遠いcontext → 低い親和度"""
        assert resolver.get_context_affinity("planning", "research") == 0.6

    def test_undefined_pair_returns_default(self, resolver):
        """未定義ペア → デフォルト0.5"""
        assert resolver.get_context_affinity("planning", "operations") == 0.5

    def test_undefined_query_context_returns_default(self, resolver):
        """未定義のquery context → デフォルト0.5"""
        assert resolver.get_context_affinity("unknown_context", "planning") == 0.5

    def test_none_query_context_returns_1(self, resolver):
        """query_context=None → 1.0（フィルタなし）"""
        assert resolver.get_context_affinity(None, "planning") == 1.0
        assert resolver.get_context_affinity(None, "implementation") == 1.0

    def test_asymmetric_affinity(self, resolver):
        """親和度は非対称（planning→impl ≠ impl→planning）"""
        p_to_i = resolver.get_context_affinity("planning", "implementation")
        i_to_p = resolver.get_context_affinity("implementation", "planning")
        assert p_to_i == 0.7
        assert i_to_p == 0.65
        assert p_to_i != i_to_p

    def test_custom_default_affinity(self):
        """カスタムデフォルト親和度"""
        resolver = ContextAffinityResolver(
            affinity_map={},
            layer_weight_map={},
            default_affinity=0.6,
        )
        assert resolver.get_context_affinity("any", "other") == 0.6


# === Layer Weight テスト ===


class TestLayerWeight:
    def test_implementation_l2_highest(self, resolver):
        """implementation時にL2が最高（1.0）"""
        assert resolver.get_layer_weight("implementation", 2) == 1.0

    def test_implementation_l5_lowest(self, resolver):
        """implementation時にL5が最低（0.5）"""
        assert resolver.get_layer_weight("implementation", 5) == 0.5

    def test_planning_l4_highest(self, resolver):
        """planning時にL4が最高（1.0）"""
        assert resolver.get_layer_weight("planning", 4) == 1.0

    def test_research_l5_highest(self, resolver):
        """research時にL5が最高（1.0）"""
        assert resolver.get_layer_weight("research", 5) == 1.0

    def test_research_l1_low(self, resolver):
        """research時にL1は低め（0.55）"""
        assert resolver.get_layer_weight("research", 1) == 0.55

    def test_none_context_returns_1(self, resolver):
        """query_context=None → 全Layer均等（1.0）"""
        for layer in range(1, 6):
            assert resolver.get_layer_weight(None, layer) == 1.0

    def test_undefined_context_returns_default(self, resolver):
        """未定義context → デフォルト0.5"""
        assert resolver.get_layer_weight("unknown_context", 3) == 0.5

    def test_custom_default_layer_weight(self):
        """カスタムデフォルトLayer重み"""
        resolver = ContextAffinityResolver(
            affinity_map={},
            layer_weight_map={},
            default_layer_weight=0.7,
        )
        assert resolver.get_layer_weight("any", 3) == 0.7


# === from_yaml テスト ===


class TestFromYaml:
    def test_load_from_yaml(self, tmp_path):
        """YAMLファイルからResolverを構築"""
        yaml_data = {
            "context_affinity": SAMPLE_AFFINITY,
            "layer_weight": SAMPLE_LAYER_WEIGHT,
        }
        yaml_path = tmp_path / "contexts.yaml"
        with open(yaml_path, "w") as f:
            yaml.dump(yaml_data, f)

        resolver = ContextAffinityResolver.from_yaml(yaml_path)
        assert resolver is not None
        assert resolver.get_context_affinity("planning", "design") == 0.9
        assert resolver.get_layer_weight("implementation", 2) == 1.0

    def test_nonexistent_file_returns_none(self):
        """存在しないファイル → None"""
        assert ContextAffinityResolver.from_yaml("/nonexistent/path.yaml") is None

    def test_no_affinity_sections_returns_none(self, tmp_path):
        """context_affinityもlayer_weightもないYAML → None"""
        yaml_path = tmp_path / "empty.yaml"
        with open(yaml_path, "w") as f:
            yaml.dump({"contexts": {"planning": {}}}, f)

        assert ContextAffinityResolver.from_yaml(yaml_path) is None

    def test_partial_yaml_ok(self, tmp_path):
        """context_affinityのみでもResolverを構築可能"""
        yaml_data = {"context_affinity": SAMPLE_AFFINITY}
        yaml_path = tmp_path / "partial.yaml"
        with open(yaml_path, "w") as f:
            yaml.dump(yaml_data, f)

        resolver = ContextAffinityResolver.from_yaml(yaml_path)
        assert resolver is not None
        assert resolver.get_context_affinity("planning", "design") == 0.9
        # layer_weightは空なのでデフォルト
        assert resolver.get_layer_weight("implementation", 2) == 0.5

    def test_load_real_config(self):
        """実際のenterprise_contexts.yamlからロード可能"""
        real_path = Path(__file__).parent.parent.parent.parent / "configs" / "enterprise_contexts.yaml"
        if not real_path.exists():
            pytest.skip("enterprise_contexts.yaml not found")

        resolver = ContextAffinityResolver.from_yaml(real_path)
        assert resolver is not None
        # 実設定の基本的な妥当性チェック
        assert resolver.get_context_affinity("planning", "design") == 0.9
        assert resolver.get_layer_weight("implementation", 2) == 1.0
        assert resolver.get_layer_weight("debugging", 1) == 1.0


# === 乗数の下限チェック ===


class TestBounds:
    def test_all_affinity_values_at_least_0_5(self):
        """実設定の全affinity値が0.5以上"""
        real_path = Path(__file__).parent.parent.parent.parent / "configs" / "enterprise_contexts.yaml"
        if not real_path.exists():
            pytest.skip("enterprise_contexts.yaml not found")

        with open(real_path) as f:
            data = yaml.safe_load(f)

        for ctx, targets in data.get("context_affinity", {}).items():
            for target, value in targets.items():
                assert value >= 0.5, f"context_affinity[{ctx}][{target}] = {value} < 0.5"

    def test_all_layer_weight_values_at_least_0_5(self):
        """実設定の全layer_weight値が0.5以上"""
        real_path = Path(__file__).parent.parent.parent.parent / "configs" / "enterprise_contexts.yaml"
        if not real_path.exists():
            pytest.skip("enterprise_contexts.yaml not found")

        with open(real_path) as f:
            data = yaml.safe_load(f)

        for ctx, layers in data.get("layer_weight", {}).items():
            for layer, value in layers.items():
                assert value >= 0.5, f"layer_weight[{ctx}][{layer}] = {value} < 0.5"

    def test_each_context_has_one_layer_at_1_0(self):
        """各contextのlayer_weightに少なくとも1つ1.0がある"""
        real_path = Path(__file__).parent.parent.parent.parent / "configs" / "enterprise_contexts.yaml"
        if not real_path.exists():
            pytest.skip("enterprise_contexts.yaml not found")

        with open(real_path) as f:
            data = yaml.safe_load(f)

        for ctx, layers in data.get("layer_weight", {}).items():
            max_val = max(layers.values())
            assert max_val == 1.0, f"layer_weight[{ctx}] has no 1.0 (max={max_val})"
