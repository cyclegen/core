"""cli/main.py — `cyclegen` コマンドの入口（CYCLE15.12.4）

MS1 で提供するサブコマンドは 1 つだけ:

    cyclegen setup codex [--dry-run] [--force] [--remove] [--use-path]

Claude Code 向けの `cyclegen setup claude` は**作らない**。Claude Code はプラグイン機構
（`/plugin marketplace add` → `/plugin install`）が正規の導線であり、二重の導線を持つと
「どちらが正か」が利用者に伝わらなくなるため（CYCLE15.12.2 判断E4）。
"""

from __future__ import annotations

import argparse
import sys

from cyclegen.cli import setup_codex

DESCRIPTION = """CycleGen — 1時間1サイクルの人間-AI協働プロトコル。

Claude Code へはプラグインとして導入します:
  /plugin marketplace add cyclegen/core
  /plugin install cyclegen-core@cyclegen
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cyclegen",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="store_true", help="バージョンを表示する")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    setup = sub.add_parser("setup", help="AI ツールへ CycleGen を配線する")
    setup_sub = setup.add_subparsers(dest="target", metavar="<tool>")

    codex = setup_sub.add_parser(
        "codex",
        help="Codex CLI / Desktop へ配線する",
        description=(
            "Codex の設定（~/.codex/config.toml・hooks.json）と スキル（~/.agents/skills/）に "
            "CycleGen を配線します。既存の設定は書き換えず、バックアップを取ります。"
        ),
    )
    setup_codex.add_arguments(codex)
    codex.set_defaults(func=setup_codex.run)

    return parser


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("cyclegen")
    except Exception:  # pragma: no cover - インストールされていない実行経路
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "version", False) and args.command is None:
        print(f"cyclegen {_version()}")
        return 0

    func = getattr(args, "func", None)
    if func is None:
        if args.command == "setup":
            # `cyclegen setup` だけで止まった場合
            parser.parse_args(["setup", "--help"])
        parser.print_help()
        return 1

    try:
        return func(args)
    except setup_codex.SetupError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
