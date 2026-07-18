"""test_classifier.py — AutoLayerClassifier のユニットテスト"""

import pytest

from cyclegen.core.classifier import AutoLayerClassifier


class TestClassify:
    def test_metacognition_layer5(self, classifier: AutoLayerClassifier):
        result = classifier.classify("メタ認知パターンの振り返り")
        assert result == 5

    def test_strategy_layer4(self, classifier: AutoLayerClassifier):
        result = classifier.classify("アーキテクチャの設計方針を戦略的に決める")
        assert result == 4

    def test_expertise_layer3(self, classifier: AutoLayerClassifier):
        result = classifier.classify("アルゴリズムのベストプラクティス")
        assert result == 3

    def test_implementation_layer2(self, classifier: AutoLayerClassifier):
        result = classifier.classify("実装のステップとコマンド")
        assert result == 2

    def test_foundation_layer1(self, classifier: AutoLayerClassifier):
        result = classifier.classify("インフラのトラブルシューティング")
        assert result == 1

    def test_default_layer3(self, classifier: AutoLayerClassifier):
        """マッチなしはデフォルト Layer 3"""
        result = classifier.classify("何の手がかりもないテキスト")
        assert result == 3

    def test_tie_breaks_to_higher_layer(self, classifier: AutoLayerClassifier):
        """同点時は上位Layer（より抽象的 = 番号が大きい）を優先"""
        # "戦略"(L4) + "コード"(L2) → 各1マッチ → 同点 → L4が勝つ
        result = classifier.classify("戦略的なコード")
        assert result == 4

    def test_multiple_matches_highest_count_wins(self, classifier: AutoLayerClassifier):
        """マッチ数が多いLayerが勝つ"""
        # Layer 2: "実装" + "コード" + "手順" = 3マッチ
        result = classifier.classify("実装のコード手順を書く")
        assert result == 2

    def test_english_keywords(self, classifier: AutoLayerClassifier):
        result = classifier.classify("architecture and strategy decisions")
        assert result == 4

    def test_context_parameter_accepted(self, classifier: AutoLayerClassifier):
        """context引数は受け付けるが現在は未使用（将来拡張用）"""
        result = classifier.classify("普通のテキスト", context="planning")
        assert result == 3  # contextは現在影響しない
