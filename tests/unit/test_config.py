"""test_config.py — config モジュールのユニットテスト"""

from pathlib import Path

import pytest
import yaml

from cyclegen.config import (
    DEFAULT_CONTEXTS,
    load_config,
    load_contexts,
    resolve_home,
)
from cyclegen.models import ContextDefinition, CycleGenConfig


class TestLoadConfig:
    def test_default_when_no_file(self, tmp_path):
        """ファイルなしの場合はデフォルト値"""
        config = load_config(tmp_path / "nonexistent.yaml")
        assert config.home == "~/.cyclegen"
        assert config.default_max_items == 7

    def test_load_from_file(self, tmp_path):
        """yamlファイルから読み込み"""
        config_file = tmp_path / "cyclegen_config.yaml"
        config_file.write_text(
            yaml.dump({"home": str(tmp_path), "default_max_items": 5}),
            encoding="utf-8",
        )
        config = load_config(config_file)
        assert config.home == str(tmp_path)
        assert config.default_max_items == 5

    def test_partial_override(self, tmp_path):
        """部分的なオーバーライド"""
        config_file = tmp_path / "cyclegen_config.yaml"
        config_file.write_text(
            yaml.dump({"org_server_enabled": True}),
            encoding="utf-8",
        )
        config = load_config(config_file)
        assert config.org_server_enabled is True
        assert config.home == "~/.cyclegen"  # デフォルト維持

    def test_env_var_fallback(self, tmp_path, monkeypatch):
        """$CYCLEGEN_HOME からの読み込み"""
        config_file = tmp_path / "cyclegen_config.yaml"
        config_file.write_text(
            yaml.dump({"default_max_items": 9}),
            encoding="utf-8",
        )
        monkeypatch.setenv("CYCLEGEN_HOME", str(tmp_path))
        config = load_config()
        assert config.default_max_items == 9


class TestLoadContexts:
    def test_default_contexts_when_no_file(self, tmp_path):
        """ファイルなしの場合はデフォルト7種類"""
        config = CycleGenConfig(home=str(tmp_path))
        contexts = load_contexts(config)
        assert len(contexts) == 7
        assert "planning" in contexts
        assert isinstance(contexts["planning"], ContextDefinition)

    def test_load_from_file(self, tmp_path):
        """yamlファイルから読み込み"""
        config = CycleGenConfig(home=str(tmp_path))
        contexts_file = tmp_path / "enterprise_contexts.yaml"
        contexts_file.write_text(
            yaml.dump({
                "contexts": {
                    "custom": {
                        "weight": 1.5,
                        "keywords": ["custom", "test"],
                        "layer_priority": [1, 2, 3, 4, 5],
                    }
                }
            }),
            encoding="utf-8",
        )
        contexts = load_contexts(config)
        assert "custom" in contexts
        assert contexts["custom"].weight == 1.5


class TestResolveHome:
    def test_creates_directory(self, tmp_path):
        target = tmp_path / "new_dir"
        config = CycleGenConfig(home=str(target))
        result = resolve_home(config)
        assert result.exists()
        assert result == target

    def test_expands_tilde(self):
        config = CycleGenConfig(home="~/.cyclegen")
        result = resolve_home(config)
        assert "~" not in str(result)
        assert result.is_absolute()


class TestDefaultContexts:
    def test_seven_contexts(self):
        assert len(DEFAULT_CONTEXTS) == 7

    def test_all_have_required_fields(self):
        for name, defn in DEFAULT_CONTEXTS.items():
            assert "weight" in defn
            assert "keywords" in defn
            assert "layer_priority" in defn
            assert len(defn["layer_priority"]) == 5
