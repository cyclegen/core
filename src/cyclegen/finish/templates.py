"""テンプレート読み込み・一覧取得"""

from __future__ import annotations

import importlib.resources

import yaml


def _templates_dir():
    """パッケージ同梱テンプレートのディレクトリパスを返す"""
    return importlib.resources.files("cyclegen.finish") / "templates"


def load_template(template_name: str) -> dict:
    """テンプレートYAMLを読み込む"""
    templates_dir = _templates_dir()
    template_file = templates_dir / f"{template_name}.yaml"
    try:
        content = template_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        available = [
            f.name.removesuffix(".yaml")
            for f in templates_dir.iterdir()
            if f.name.endswith(".yaml")
        ]
        raise FileNotFoundError(
            f"テンプレート '{template_name}' が見つかりません。"
            f"利用可能: {', '.join(sorted(available))}"
        )
    return yaml.safe_load(content)


def list_templates() -> list[dict]:
    """利用可能なテンプレート一覧を返す"""
    templates_dir = _templates_dir()
    result = []
    for path in sorted(templates_dir.iterdir()):
        if not path.name.endswith(".yaml"):
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        result.append({
            "name": path.name.removesuffix(".yaml"),
            "display_name": data.get("name", path.name.removesuffix(".yaml")),
            "description": data.get("description", ""),
        })
    return result
