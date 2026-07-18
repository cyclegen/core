"""設定ファイル読み込み

実装計画書§3: cyclegen_config.yaml + enterprise_contexts.yaml の読み込み。
優先順位: 引数指定 > $CYCLEGEN_HOME > ~/.cyclegen > デフォルト値
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml

from cyclegen.models import ContextDefinition, CycleGenConfig


# デフォルト7種類のContext定義（enterprise_contexts.yamlが存在しない場合）
DEFAULT_CONTEXTS: dict[str, dict] = {
    "planning": {
        "weight": 1.0,
        "keywords": ["計画", "設計", "方針", "戦略", "plan", "design", "strategy"],
        "layer_priority": [4, 5, 3, 2, 1],
    },
    "implementation": {
        "weight": 1.0,
        "keywords": ["実装", "コード", "開発", "build", "implement", "code", "develop"],
        "layer_priority": [2, 3, 1, 4, 5],
    },
    "debugging": {
        "weight": 1.0,
        "keywords": ["バグ", "エラー", "修正", "debug", "error", "fix", "issue"],
        "layer_priority": [1, 2, 3, 4, 5],
    },
    "review": {
        "weight": 1.0,
        "keywords": ["レビュー", "確認", "検証", "review", "verify", "check"],
        "layer_priority": [3, 4, 2, 5, 1],
    },
    "learning": {
        "weight": 1.0,
        "keywords": ["学習", "理解", "調査", "learn", "understand", "research"],
        "layer_priority": [3, 5, 4, 2, 1],
    },
    "documentation": {
        "weight": 0.8,
        "keywords": ["文書", "ドキュメント", "記録", "doc", "document", "record"],
        "layer_priority": [3, 4, 5, 2, 1],
    },
    "operations": {
        "weight": 0.8,
        "keywords": ["運用", "デプロイ", "監視", "ops", "deploy", "monitor"],
        "layer_priority": [1, 2, 3, 4, 5],
    },
}


def load_config(config_path: Optional[Path] = None) -> CycleGenConfig:
    """cyclegen_config.yaml を読み込む。

    優先順位:
    1. 引数で指定されたパス
    2. $CYCLEGEN_HOME/cyclegen_config.yaml
    3. ~/.cyclegen/cyclegen_config.yaml
    4. デフォルト値
    """
    paths_to_try: list[Path] = []

    if config_path is not None:
        paths_to_try.append(Path(config_path))

    cyclegen_home = os.environ.get("CYCLEGEN_HOME")
    if cyclegen_home:
        paths_to_try.append(Path(cyclegen_home) / "cyclegen_config.yaml")

    paths_to_try.append(Path.home() / ".cyclegen" / "cyclegen_config.yaml")

    for path in paths_to_try:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return CycleGenConfig(**data)

    return CycleGenConfig()


def load_contexts(config: CycleGenConfig) -> dict[str, ContextDefinition]:
    """enterprise_contexts.yaml を読み込む。

    Context定義が動的拡張可能であることを保証する。
    ファイルが存在しない場合はデフォルト7種類を返す。
    """
    home = resolve_home(config)
    contexts_path = home / config.contexts_file

    if contexts_path.exists():
        with open(contexts_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        contexts = data.get("contexts", {})
        return {
            name: ContextDefinition(**definition)
            for name, definition in contexts.items()
        }

    return {
        name: ContextDefinition(**definition)
        for name, definition in DEFAULT_CONTEXTS.items()
    }


def resolve_home(config: CycleGenConfig) -> Path:
    """$CYCLEGEN_HOME を展開し、ディレクトリを作成する。"""
    home = os.path.expandvars(config.home)
    home = Path(home).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    return home


# 3d-eval.yaml のデフォルトファイル（パッケージ同梱）
_3D_EVAL_DEFAULT = Path(__file__).parent / "3d_eval_default.yaml"


def load_3d_eval(config: CycleGenConfig) -> dict:
    """3d-eval.yaml を読み込む。

    優先順位:
    1. $CYCLEGEN_HOME/3d-eval.yaml（利用者カスタマイズ）
    2. パッケージ同梱のデフォルト

    Returns:
        3軸評価基準の辞書
    """
    home = resolve_home(config)
    user_path = home / "3d-eval.yaml"

    if user_path.exists():
        with open(user_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    # デフォルト
    with open(_3D_EVAL_DEFAULT, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def format_3d_eval_prompt(eval_criteria: dict, content_preview: str) -> str:
    """2軸評価基準をAIエディタ向けプロンプトに整形する。

    memory_storeでLayer/Contextが省略された場合にレスポンスとして返すテキスト。
    CYCLE12: Priorityは0.3固定（利用実績で動的変動）のため、2軸判定に簡略化。
    """
    lines = [
        "【2軸評価リクエスト】",
        "以下の内容を3次元記憶に保存します。Layer/Contextを評価してください。",
        "（Priorityは0.3固定で自動設定されます。利用実績で動的に変動します。）",
        "",
        "--- 保存する内容（プレビュー） ---",
        content_preview[:500] + ("..." if len(content_preview) > 500 else ""),
        "",
    ]

    # Layer基準
    layer_info = eval_criteria.get("layer", {})
    lines.append(f"--- Layer（{layer_info.get('description', '')}） ---")
    for level, desc in sorted(layer_info.get("criteria", {}).items(), reverse=True):
        lines.append(f"  L{level}: {desc}")
    if layer_info.get("instruction"):
        lines.append(f"  判定指針: {layer_info['instruction'].strip()}")
    lines.append("")

    # Context基準
    context_info = eval_criteria.get("context", {})
    lines.append(f"--- Context（{context_info.get('description', '')}） ---")
    for name, desc in context_info.get("values", {}).items():
        lines.append(f"  {name}: {desc}")
    if context_info.get("instruction"):
        lines.append(f"  判定指針: {context_info['instruction'].strip()}")
    lines.append("")

    lines.append("→ 上記の基準で評価し、memory_store を layer/context 付きで再度呼んでください。")

    return "\n".join(lines)
