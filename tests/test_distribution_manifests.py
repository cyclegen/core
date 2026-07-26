"""配布マニフェスト（marketplace.json / plugin.json / pyproject.toml）の整合テスト。

初出: CYCLE15.12.1（プラグイン配布導線の修復）。

背景:
    版数は pyproject.toml / plugin.json / gitタグ の3箇所で独立に動くため、
    リリース時にズレる。CYCLE15.11.4 の finding M-0d（plugin.json が 0.1.0-alpha の
    まま、タグ/PyPI は 0.1.0）が実例。ここで機械的に止める。

設計（リリース時に自動で有効化される）:
    pyproject の version が開発版（.devN / rcN 等のプレリリース）である間は
    plugin.json との一致を要求しない（開発中は先行して当然のため）。
    pyproject が正式版になった瞬間に一致が必須になる＝リリースの直前でだけ効く。
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# 公式ドキュメント（code.claude.com/docs/en/plugin-marketplaces）で
# Anthropic 公式用に予約され、サードパーティが使えない marketplace 名。
RESERVED_MARKETPLACE_NAMES = {
    "claude-code-marketplace", "claude-code-plugins", "claude-plugins-official",
    "claude-plugins-community", "claude-community", "anthropic-marketplace",
    "anthropic-plugins", "agent-skills", "anthropic-agent-skills",
    "knowledge-work-plugins", "life-sciences", "claude-for-legal",
    "claude-for-financial-services", "financial-services-plugins",
    "first-party-plugins", "healthcare",
}


def _marketplace() -> dict:
    return json.loads(MARKETPLACE.read_text(encoding="utf-8"))


def _entries() -> list[dict]:
    return _marketplace()["plugins"]


def _plugin_manifest(entry: dict) -> dict:
    source = entry["source"]
    if not isinstance(source, str):
        pytest.skip(f"{entry['name']}: リモート source はローカル検証の対象外")
    path = (REPO_ROOT / source).resolve() / ".claude-plugin" / "plugin.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _is_prerelease(version: str) -> bool:
    """0.1.1.dev0 / 0.1.0-alpha / 1.0.0rc1 のような開発版か。"""
    return not version.replace(".", "").isdigit()


def test_marketplace_manifest_exists_at_repo_root() -> None:
    """目録がリポジトリルートに無いと /plugin marketplace add が成立しない（M-0）。"""
    assert MARKETPLACE.is_file(), f"{MARKETPLACE} が存在しない"


def test_marketplace_name_is_not_reserved() -> None:
    name = _marketplace()["name"]
    assert name not in RESERVED_MARKETPLACE_NAMES, f"marketplace 名 '{name}' は予約語"
    assert name == name.lower() and " " not in name, "marketplace 名は kebab-case・空白不可"


def test_plugin_sources_resolve() -> None:
    """相対 source が実在し、その先に plugin.json があること。"""
    for entry in _entries():
        source = entry["source"]
        if not isinstance(source, str):
            continue
        assert not source.startswith("../"), f"{entry['name']}: marketplace root の外は不可"
        manifest = (REPO_ROOT / source).resolve() / ".claude-plugin" / "plugin.json"
        assert manifest.is_file(), f"{entry['name']}: {manifest} が無い"


def test_entry_name_matches_plugin_manifest() -> None:
    for entry in _entries():
        assert entry["name"] == _plugin_manifest(entry)["name"]


def test_marketplace_entry_does_not_declare_version() -> None:
    """公式が「plugin.json と marketplace entry の両方に version を書くな」と警告。

    Claude Code は plugin.json 側を無警告で優先するため、marketplace 側の version は
    古いまま気づかれずに残る。書かないことで整合対象を1つ減らす。
    """
    for entry in _entries():
        assert "version" not in entry, (
            f"{entry['name']}: marketplace entry に version を書かない（plugin.json が正）"
        )


def test_plugin_version_is_a_release_version() -> None:
    """plugin.json の version にプレリリース接尾辞を残さない（M-0d の再発防止）。"""
    for entry in _entries():
        version = _plugin_manifest(entry).get("version")
        assert version, f"{entry['name']}: plugin.json に version が無い"
        assert not _is_prerelease(version), (
            f"{entry['name']}: plugin.json の version '{version}' がプレリリースのまま"
        )


def test_plugin_version_matches_pyproject_at_release() -> None:
    """pyproject が正式版になったら plugin.json と一致していること。

    開発中（.devN）は先行を許す。リリース直前にだけ効くゲート。
    """
    pyproject_version = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    if _is_prerelease(pyproject_version):
        pytest.skip(f"pyproject は開発版 ({pyproject_version}) のため一致は要求しない")
    for entry in _entries():
        assert _plugin_manifest(entry)["version"] == pyproject_version, (
            f"{entry['name']}: plugin.json と pyproject.toml の version 不一致"
        )
