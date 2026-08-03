"""cli/setup_codex.py — Codex CLI/Desktop への配線（`cyclegen setup codex`）

CYCLE15.12.2（設計・論点B/C/D）→ CYCLE15.12.4（実装）。

配布導線の位置づけ:
    Claude Code はプラグイン機構（marketplace → install）が正規の導線であり、
    そちらには `cyclegen setup claude` を**作らない**（二重導線を避ける・15.12.2 判断E4）。
    Codex には拡張機構が無いため、PyPI で既に届いている本パッケージから配線する（B案）。

設計上の要点（すべて実測に裏付けられている）:

1. payload は **必ず `~/.cyclegen/plugin/` へコピーする**。
   インストール済みパッケージ内の payload は uvx のキャッシュ配下
   （`~/.cache/uv/archive-v0/.../site-packages/cyclegen/_payload`）に解決されるため
   （CYCLE15.12.3 F15 実測）、設定ファイルからそこを直接指すと
   `uv cache clean` で配線が丸ごと壊れる。

2. 配置先は **フラット**（版数をパスに含めない）。`config.toml` と `hooks.json` は
   絶対パスを保持するため、版数を含めると更新のたびに配線が無効化される。
   Claude Code 側はキャッシュのパスに版数が入るのが正しく、ここは**正解が反転する**
   （15.12.2 §4-1）。

3. `~/.codex/config.toml` は**全面書き換えをしない**。`tomllib` は読み取り専用で、
   書き戻すと利用者のコメントと書式が壊れる。→ 読んで判定し、無ければ
   テキストブロックを追記する。`[mcp_servers.cyclegen.tools.*]` のような
   **利用者のサブテーブルは温存する**。

4. `~/.codex/hooks.json` のトップレベルは **`hooks` キーのみ**。`_comment` があると
   Codex 0.142.5 は hook 全体のロードに失敗する（CYCLE14.16 実機）。

5. MCP の `command` は **`uvx` 直指定**（15.12.2 §4-2 C-c）。
   `uvx cyclegen setup codex` で実行された場合、`cyclegen-mcp` はエフェメラル環境に
   しか存在せず setup 完了後に消えるため、PATH 前提や絶対パスを書くと
   **壊れた設定を書き込んでしまう**。pip / uv tool で恒久導入した利用者向けにのみ
   `--use-path` を用意する。
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# uvx で引くパッケージ指定。extras を省くと memory_search が縮退し finish 系ツールも出ない
# （CYCLE15.3 実発火で確認）。
PACKAGE_SPEC = "cyclegen[semantic,docx]"

# 規律層 hook の実体ファイル名。hooks.json のマージ時に「自分のエントリ」を識別する鍵。
# 旧来の手動配線（`~/.cyclegen/hooks/` 直置き・CYCLE14.17 期）も同名ゆえ拾えるので、
# 二重登録（＝二重発火）を構造的に防げる。
HOOK_SCRIPTS = (
    "remind-primer.sh",
    "remind-cycle-memory.sh",
    "check-cycle-complete.sh",
    "remind-context-judgment.sh",
    "remind-knowledge-proposal.sh",
    "remind-profile-update.sh",
)

# (payload内のスキルdir, Codex側の配置名, 明示専用サイドカーのpayload内パス or None)
# ※ init の配置dir名は cyclegen-init だが、呼び出しは frontmatter の `name: init` に従い
#   `$init`（CYCLE14.21 finding#2 / 14.24 訂正）。
SKILL_MAP: tuple[tuple[str, str, str | None], ...] = (
    ("skills/cyclegen-cycle", "cyclegen-cycle", None),
    ("skills/cyclegen-memory", "cyclegen-memory", None),
    ("skills/cyclegen-glossary", "cyclegen-glossary", None),
    ("skills/cyclegen-ops", "cyclegen-ops", None),
    (
        "skills/cyclegen",
        "cyclegen",
        "manifests/codex/skills-explicit/cyclegen/agents/openai.yaml",
    ),
    (
        "skills/init",
        "cyclegen-init",
        "manifests/codex/skills-explicit/cyclegen-init/agents/openai.yaml",
    ),
    # サイクル0（FR052・CYCLE15.16設計 / 17.2実装）。自動起動するのでサイドカーは無い。
    # 配置dir名に接頭辞を付けるのは cyclegen-init と同じ理由＝`onboarding` は総称的で、
    # 利用者や他ツールが同名dirを既に置いている可能性がある。その場合 apply_skills は
    # 「管理外」と判定して**配置せず警告で終わる**ため、最も不慣れな初回利用者ほど
    # サイクル0に到達できなくなる。呼び出しは frontmatter の `name: onboarding` に従い
    # `$onboarding`（配置dir名ではない・CYCLE14.21 finding#2 の型）。
    ("skills/onboarding", "cyclegen-onboarding", None),
)

# init の SKILL.md が参照する人格雛形。Codex の skill は自己完結ディレクトリのため
# スキル配下へ同梱しないとフォールバック動作になる（CYCLE14.17 finding#5）。
PERSONA_SRC = "agents/cyclegen-persona.md"
PERSONA_DEST_SKILL = "cyclegen-init"

# CycleGen が管理しているスキルディレクトリの目印。--remove / 再実行で
# 「利用者が自分で置いたもの」を巻き込まないために使う。
MANAGED_MARKER = ".cyclegen-managed"
VERSION_MARKER = "VERSION"

BLOCK_BEGIN = "# >>> cyclegen setup codex >>>"
BLOCK_END = "# <<< cyclegen setup codex <<<"

# `[mcp_servers.cyclegen]` の直下テーブルのみに一致する（サブテーブルには一致しない）。
MCP_TABLE_RE = re.compile(r"^\s*\[mcp_servers\.cyclegen\]\s*$")

UV_INSTALL_HINT = (
    "  macOS / Linux : curl -LsSf https://astral.sh/uv/install.sh | sh\n"
    '  Windows       : powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
)


class SetupError(RuntimeError):
    """配線処理を中断すべきエラー。"""


# --------------------------------------------------------------------------
# パス解決
# --------------------------------------------------------------------------


@dataclass
class Paths:
    codex_home: Path
    config_toml: Path
    hooks_json: Path
    skills_dir: Path
    plugin_dir: Path


def resolve_paths() -> Paths:
    """配線先のパスを解決する。

    環境変数で上書きできるのは、検証を隔離環境で回すため（受入は必ず
    まっさら相当の環境で行う＝15.12 計画 §7-3）。
    """
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser()
    cyclegen_home = Path(
        os.environ.get("CYCLEGEN_HOME") or home / ".cyclegen"
    ).expanduser()
    skills_dir = Path(
        os.environ.get("CYCLEGEN_SKILLS_DIR") or home / ".agents" / "skills"
    ).expanduser()
    return Paths(
        codex_home=codex_home,
        config_toml=codex_home / "config.toml",
        hooks_json=codex_home / "hooks.json",
        skills_dir=skills_dir,
        plugin_dir=cyclegen_home / "plugin",
    )


def resolve_payload_source() -> Path:
    """配布された規律層ペイロード（プラグイン本体）の在り処を返す。

    優先順位:
      1. $CYCLEGEN_PAYLOAD_DIR（検証・開発用の明示指定）
      2. インストール済みパッケージ内の `cyclegen/_payload`（wheel 同梱・CYCLE15.12.3 A-1）
      3. 開発リポジトリの `plugins/cyclegen-core`（editable install 時のフォールバック）
    """
    override = os.environ.get("CYCLEGEN_PAYLOAD_DIR")
    if override:
        cand = Path(override).expanduser()
        if not _is_payload(cand):
            raise SetupError(
                f"CYCLEGEN_PAYLOAD_DIR が指す場所に配布物がありません: {cand}"
            )
        return cand

    try:
        from importlib.resources import files as _files

        cand = Path(str(_files("cyclegen"))) / "_payload"
        if _is_payload(cand):
            return cand
    except (ImportError, ModuleNotFoundError, TypeError):  # pragma: no cover
        pass

    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "plugins" / "cyclegen-core"
        if _is_payload(cand):
            return cand

    raise SetupError(
        "CycleGen の規律層ペイロードが見つかりません。\n"
        "  パッケージが壊れている可能性があります。次を試してください:\n"
        f'    uvx --from "{PACKAGE_SPEC}" cyclegen setup codex'
    )


def _is_payload(path: Path) -> bool:
    return (path / ".claude-plugin" / "plugin.json").is_file()


def payload_version(payload: Path) -> str:
    try:
        data = json.loads(
            (payload / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        return str(data.get("version", "unknown"))
    except (OSError, json.JSONDecodeError):  # pragma: no cover
        return "unknown"


# --------------------------------------------------------------------------
# レポート
# --------------------------------------------------------------------------


@dataclass
class Report:
    dry_run: bool = False
    lines: list[tuple[str, str]] = field(default_factory=list)

    def did(self, msg: str) -> None:
        self.lines.append(("do", msg))

    def skipped(self, msg: str) -> None:
        self.lines.append(("skip", msg))

    def warned(self, msg: str) -> None:
        self.lines.append(("warn", msg))

    def render(self) -> str:
        icons = {"do": "✔", "skip": "・", "warn": "⚠"}
        verb = "書き込む予定" if self.dry_run else "実施"
        out = [f"--- {verb}した内容 ---"]
        for status, msg in self.lines:
            out.append(f"  {icons[status]} {msg}")
        return "\n".join(out)

    @property
    def warnings(self) -> list[str]:
        return [m for s, m in self.lines if s == "warn"]

    @property
    def changes(self) -> list[str]:
        return [m for s, m in self.lines if s == "do"]


# --------------------------------------------------------------------------
# 書き込みユーティリティ
# --------------------------------------------------------------------------


def write_text_safely(path: Path, text: str, validate) -> Path | None:
    """バックアップを取ってから書き、検証に失敗したら復元する。

    「壊れた設定を書き残さない」ことを機構で担保する（設定破壊は利用者から見て
    最も回復しにくい失敗のため）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if path.is_file():
        backup = path.with_name(path.name + ".cyclegen-bak")
        shutil.copy2(path, backup)
    path.write_text(text, encoding="utf-8")
    try:
        validate(path)
    except Exception as exc:  # noqa: BLE001 — 復元して原因を伝える
        if backup is not None:
            shutil.copy2(backup, path)
        else:
            path.unlink(missing_ok=True)
        raise SetupError(
            f"{path} の書き込み結果が不正だったため元に戻しました: {exc}"
        ) from exc
    return backup


def _validate_toml(path: Path) -> None:
    with path.open("rb") as fh:
        tomllib.load(fh)


def _validate_hooks_json(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("トップレベルがオブジェクトではありません")
    extra = set(data) - {"hooks"}
    if extra:
        # Codex 0.142.5 は厳格パーサで、未知のキーがあると hook 全体がロードに失敗する
        # （CYCLE14.16 実機 finding#1）。
        raise ValueError(f"トップレベルに未知のキーがあります: {sorted(extra)}")


# --------------------------------------------------------------------------
# 各ステップ
# --------------------------------------------------------------------------


def check_uv(report: Report, *, use_path: bool) -> None:
    """uv / uvx の導入を検査する（15.12.2 §5-2 緩和策②）。

    MCP 起動には OS 分岐の機構が無いため `uvx` の1本に寄せた。その代償として
    uv 不在が単一障害点になるので、**案内を出せる層でだけ出す**。
    """
    if use_path:
        if shutil.which("cyclegen-mcp") is None:
            report.warned(
                "--use-path を指定しましたが PATH 上に cyclegen-mcp がありません。"
                "`pip install \"cyclegen[semantic,docx]\"` などで恒久導入してください。"
            )
        return
    if shutil.which("uvx") is None:
        report.warned(
            "uvx が見つかりません。CycleGen の MCP サーバーは uvx で起動します。\n"
            "    先に uv を導入してください:\n" + UV_INSTALL_HINT
        )


def deploy_payload(
    payload: Path, paths: Paths, *, dry_run: bool, force: bool, report: Report
) -> None:
    version = payload_version(payload)
    dest = paths.plugin_dir
    marker = dest / VERSION_MARKER
    if dest.is_dir():
        current = marker.read_text(encoding="utf-8").strip() if marker.is_file() else "不明"
        if current == version and not force:
            report.skipped(f"配布物: {dest} は既に version {version}（--force で再配置）")
            return
        report.did(f"配布物: {dest} を更新（{current} → {version}）")
    else:
        report.did(f"配布物: {dest} へ配置（version {version}）")
    if dry_run:
        return

    staging = dest.parent / f"{dest.name}.tmp"
    shutil.rmtree(staging, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(payload, staging)
    shutil.rmtree(dest, ignore_errors=True)
    staging.rename(dest)
    marker.write_text(version + "\n", encoding="utf-8")
    _ensure_executable(dest)


def _ensure_executable(root: Path) -> None:
    """`.sh` に実行ビットを立てる（保険）。

    CYCLE15.12.3 F14 の実測で、実行ビットは wheel に保存されインストール後も
    `0o755` で復元されることが確認されている。したがってこれは必須処理ではないが、
    zip 展開系のツールを経由した場合に備えて残す。
    """
    for sub in ("hooks", "bin"):
        for script in (root / sub).glob("*.sh"):
            mode = script.stat().st_mode
            script.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def render_config_block(*, use_path: bool) -> str:
    if use_path:
        body = 'command = "cyclegen-mcp"\nargs = []\n'
        note = "# PATH 上の cyclegen-mcp を使う（pip / uv tool で恒久導入済みの利用者向け）。\n"
    else:
        body = f'command = "uvx"\nargs = ["--from", "{PACKAGE_SPEC}", "cyclegen-mcp"]\n'
        note = (
            "# uvx 直指定。setup の実行方法（uvx / pip）に依存せず、OS も問わない。\n"
            "# extras の semantic / docx は省かないこと（省くと memory_search が縮退する）。\n"
        )
    return (
        f"{BLOCK_BEGIN}\n"
        "# CycleGen の MCP サーバー設定（`cyclegen setup codex` が生成）。\n"
        f"{note}"
        "[mcp_servers.cyclegen]\n"
        f"{body}"
        f"{BLOCK_END}\n"
    )


def has_cyclegen_table(text: str) -> bool:
    return any(MCP_TABLE_RE.match(line) for line in text.splitlines())


def strip_cyclegen_table(text: str) -> tuple[str, bool]:
    """`[mcp_servers.cyclegen]` の直下テーブル（とマーカーブロック）だけを取り除く。

    `[mcp_servers.cyclegen.tools.*]` のようなサブテーブルは**利用者の設定**なので残す。
    TOML はサブテーブルを先に書いた後で親テーブルを定義してよいため、
    取り除いた後に末尾へ追記しても壊れない。
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    removed = False
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == BLOCK_BEGIN:
            removed = True
            while i < len(lines) and lines[i].strip() != BLOCK_END:
                i += 1
            i += 1  # BLOCK_END 自身を飛ばす
            continue
        if MCP_TABLE_RE.match(lines[i]):
            removed = True
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("["):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out), removed


def apply_config(
    paths: Paths, *, use_path: bool, dry_run: bool, force: bool, remove: bool, report: Report
) -> None:
    path = paths.config_toml
    original = path.read_text(encoding="utf-8") if path.is_file() else ""
    present = has_cyclegen_table(original) or BLOCK_BEGIN in original

    if remove:
        if not present:
            report.skipped(f"config.toml: {path} に CycleGen の設定はありません")
            return
        new_text, _ = strip_cyclegen_table(original)
        report.did(f"config.toml: {path} から [mcp_servers.cyclegen] を削除")
        if not dry_run:
            write_text_safely(path, new_text, _validate_toml)
        return

    if present and not force:
        report.skipped(
            f"config.toml: {path} には既に [mcp_servers.cyclegen] があります"
            "（変更しません。置き換えるなら --force）"
        )
        return

    base, replaced = strip_cyclegen_table(original) if present else (original, False)
    if base and not base.endswith("\n"):
        base += "\n"
    if base:
        base += "\n"
    new_text = base + render_config_block(use_path=use_path)
    verb = "を置換" if replaced else "を追記"
    report.did(f"config.toml: {path} に [mcp_servers.cyclegen]{verb}")
    if not dry_run:
        write_text_safely(path, new_text, _validate_toml)


def build_hook_config(payload: Path, plugin_dir: Path) -> dict:
    """プラグイン正本の hooks.json から Codex 用の設定を生成する。

    ペイロード（共通）とツール固有の配線（ここ）を分ける規律の実装。
    `${CLAUDE_PLUGIN_ROOT}` は Codex に無いので絶対パスへ展開する。
    """
    raw = json.loads((payload / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    events = copy.deepcopy(raw.get("hooks", {}))
    for groups in events.values():
        for group in groups:
            for entry in group.get("hooks", []):
                command = str(entry.get("command", ""))
                command = command.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_dir))
                # 14.17 の実機で通った形＝引用符なしの絶対パス
                entry["command"] = command.strip('"')
    return {"hooks": events}


def _is_cyclegen_hook(entry: dict) -> bool:
    command = str(entry.get("command", ""))
    return any(name in command for name in HOOK_SCRIPTS)


def merge_hooks(existing: dict, ours: dict | None) -> tuple[dict, int]:
    """既存の hooks.json から CycleGen のエントリだけを抜き、必要なら入れ直す。

    他者の hook には触れない。返り値の int は取り除いた自分のエントリ数
    （＝旧配線の掃除件数。二重発火の防止）。
    """
    merged = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    events = merged.get("hooks")
    if not isinstance(events, dict):
        events = {}
    removed = 0
    cleaned: dict = {}
    for event, groups in events.items():
        if not isinstance(groups, list):
            cleaned[event] = groups
            continue
        kept_groups = []
        for group in groups:
            entries = group.get("hooks", []) if isinstance(group, dict) else []
            survivors = [e for e in entries if not _is_cyclegen_hook(e)]
            removed += len(entries) - len(survivors)
            if survivors:
                new_group = copy.deepcopy(group)
                new_group["hooks"] = survivors
                kept_groups.append(new_group)
        if kept_groups:
            cleaned[event] = kept_groups
    if ours:
        for event, groups in ours.get("hooks", {}).items():
            cleaned.setdefault(event, []).extend(copy.deepcopy(groups))
    merged["hooks"] = cleaned
    return merged, removed


def apply_hooks(
    payload: Path | None, paths: Paths, *, dry_run: bool, remove: bool, report: Report
) -> None:
    path = paths.hooks_json
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SetupError(
                f"{path} が JSON として読めません（{exc}）。"
                "手で直すか退避してから再実行してください。"
            ) from exc
        if isinstance(existing, dict):
            extra = set(existing) - {"hooks"}
            if extra:
                report.warned(
                    f"hooks.json のトップレベルに {sorted(extra)} があります。"
                    "Codex は未知のキーがあると hook 全体のロードに失敗します（要手当）。"
                )
    else:
        existing = {"hooks": {}}

    if remove or payload is None:
        ours = None
    else:
        ours = build_hook_config(payload, paths.plugin_dir)
    merged, removed_count = merge_hooks(existing, ours)

    if remove:
        if removed_count == 0:
            report.skipped(f"hooks.json: {path} に CycleGen の hook はありません")
            return
        report.did(f"hooks.json: {path} から CycleGen の hook {removed_count} 件を削除")
    else:
        count = sum(
            len(g.get("hooks", []))
            for groups in ours["hooks"].values()
            for g in groups
        )
        detail = f"（旧配線 {removed_count} 件を置き換え）" if removed_count else ""
        report.did(f"hooks.json: {path} へ CycleGen の hook {count} 件を登録{detail}")

    if not dry_run:
        write_text_safely(
            path, json.dumps(merged, ensure_ascii=False, indent=2) + "\n", _validate_hooks_json
        )


def apply_skills(
    payload: Path | None,
    paths: Paths,
    *,
    dry_run: bool,
    force: bool,
    remove: bool,
    report: Report,
) -> None:
    version = payload_version(payload) if payload is not None else "unknown"
    for src_rel, name, sidecar_rel in SKILL_MAP:
        dest = paths.skills_dir / name
        managed = (dest / MANAGED_MARKER).is_file()

        if remove:
            if managed:
                report.did(f"スキル: {dest} を削除")
                if not dry_run:
                    shutil.rmtree(dest, ignore_errors=True)
            elif dest.exists():
                report.skipped(f"スキル: {dest} は CycleGen 管理外のため残します")
            continue

        if dest.exists() and not managed and not force:
            report.warned(
                f"スキル: {dest} が既にあります（CycleGen 管理外）。"
                "変更しません。置き換えるなら --force"
            )
            continue

        report.did(f"スキル: {dest}（{name}）")
        if dry_run:
            continue

        src = payload / src_rel
        if not (src / "SKILL.md").is_file():
            raise SetupError(f"配布物にスキルがありません: {src}")
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest)
        if sidecar_rel:
            sidecar = payload / sidecar_rel
            if sidecar.is_file():
                (dest / "agents").mkdir(parents=True, exist_ok=True)
                shutil.copy2(sidecar, dest / "agents" / "openai.yaml")
        if name == PERSONA_DEST_SKILL:
            persona = payload / PERSONA_SRC
            if persona.is_file():
                (dest / "agents").mkdir(parents=True, exist_ok=True)
                shutil.copy2(persona, dest / "agents" / persona.name)
        (dest / MANAGED_MARKER).write_text(version + "\n", encoding="utf-8")


def remove_payload(paths: Paths, *, dry_run: bool, report: Report) -> None:
    dest = paths.plugin_dir
    if not dest.exists():
        report.skipped(f"配布物: {dest} はありません")
        return
    report.did(f"配布物: {dest} を削除")
    if not dry_run:
        shutil.rmtree(dest, ignore_errors=True)


# --------------------------------------------------------------------------
# エントリポイント
# --------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    dry_run = bool(args.dry_run)
    force = bool(args.force)
    remove = bool(args.remove)
    use_path = bool(getattr(args, "use_path", False))

    paths = resolve_paths()
    report = Report(dry_run=dry_run)

    if remove:
        header = "CycleGen の Codex 配線を撤去します" + ("（--dry-run）" if dry_run else "")
        print(header)
        print()
        apply_config(
            paths, use_path=use_path, dry_run=dry_run, force=force, remove=True, report=report
        )
        # 撤去は配布物を必要としない（配布物が既に消えていても撤去できる）
        apply_hooks(None, paths, dry_run=dry_run, remove=True, report=report)
        apply_skills(
            None, paths, dry_run=dry_run, force=force, remove=True, report=report
        )
        remove_payload(paths, dry_run=dry_run, report=report)
        print(report.render())
        print()
        print(
            "記憶ストア（~/.cyclegen 配下のデータ）には触れていません。"
            "蓄積した記憶はそのまま残ります。"
        )
        return 0

    payload = resolve_payload_source()
    version = payload_version(payload)

    print(f"CycleGen {version} を Codex へ配線します" + ("（--dry-run）" if dry_run else ""))
    print(f"  配布物の取得元 : {payload}")
    print(f"  配置先         : {paths.plugin_dir}")
    print(f"  Codex 設定     : {paths.codex_home}")
    print(f"  スキル         : {paths.skills_dir}")
    print()

    check_uv(report, use_path=use_path)
    deploy_payload(payload, paths, dry_run=dry_run, force=force, report=report)
    apply_config(
        paths, use_path=use_path, dry_run=dry_run, force=force, remove=False, report=report
    )
    apply_hooks(payload, paths, dry_run=dry_run, remove=False, report=report)
    apply_skills(payload, paths, dry_run=dry_run, force=force, remove=False, report=report)

    print(report.render())
    print()
    if dry_run:
        print("--dry-run のため何も書き込んでいません。実行するには --dry-run を外してください。")
        return 0

    print("次の手順:")
    print("  1. Codex を再起動する（設定の読み直し）")
    print("  2. `/mcp` 相当で cyclegen が接続され、19 ツールが見えることを確認する")
    print("  3. プロジェクトのディレクトリで `$init ja` を実行して CYCLE を始める")
    if report.warnings:
        print()
        print("⚠ 未解決の注意があります（上記）。")
    return 0


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="何を書き換えるかだけ表示して終了する（書き込みは行わない）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="既にある CycleGen 以外の設定・スキルも置き換える（既定はスキップして報告）",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="配線を撤去する（記憶ストアのデータは削除しない）",
    )
    parser.add_argument(
        "--use-path",
        action="store_true",
        help="MCP の command に PATH 上の cyclegen-mcp を書く（pip などで恒久導入済みの場合）",
    )
