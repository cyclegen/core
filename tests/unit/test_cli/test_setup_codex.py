"""`cyclegen setup codex` の単体テスト（CYCLE15.12.4）

受入の中心は「既存の設定を壊さない」「2回実行しても壊れない」の2点なので、
その2つを最も手厚く固定する（15.12.2 §6-2）。
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from cyclegen.cli import setup_codex
from cyclegen.cli.main import main

def _find_payload() -> Path | None:
    """`plugins/cyclegen-core` を上方探索する。

    母艦（`_CycleGen_Ent/cyclegen/tests/...`）と公開Core（`<repo>/tests/...`）で
    リポジトリルートまでの深さが違うため、固定の parents[n] で書くと
    **公開Coreでだけ黙ってスキップされる**（15.12.1 の条件付きゲートと同じ落とし穴）。
    """
    for parent in Path(__file__).resolve().parents:
        cand = parent / "plugins" / "cyclegen-core"
        if (cand / ".claude-plugin" / "plugin.json").is_file():
            return cand
    return None


PAYLOAD_SRC = _find_payload()

pytestmark = pytest.mark.skipif(
    PAYLOAD_SRC is None,
    reason="plugins/cyclegen-core を含まないチェックアウトではスキップ",
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔離された配線先。実機の ~/.codex・~/.agents には一切触れない。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("CYCLEGEN_HOME", str(home / ".cyclegen"))
    monkeypatch.setenv("CYCLEGEN_SKILLS_DIR", str(home / ".agents" / "skills"))
    monkeypatch.setenv("CYCLEGEN_PAYLOAD_DIR", str(PAYLOAD_SRC))
    return setup_codex.resolve_paths()


def run(*argv: str) -> int:
    return main(["setup", "codex", *argv])


# --------------------------------------------------------------------------
# 基本の配線
# --------------------------------------------------------------------------


def test_setup_wires_config_hooks_and_skills(env):
    assert run() == 0

    config = env.config_toml.read_text(encoding="utf-8")
    assert "[mcp_servers.cyclegen]" in config
    # setup の実行方法に依存しない書き方であること（15.12.2 §4-2 の落とし穴）
    assert 'command = "uvx"' in config
    assert "cyclegen[semantic,docx]" in config

    hooks = json.loads(env.hooks_json.read_text(encoding="utf-8"))
    # Codex 0.142.5 はトップレベルに未知のキーがあると hook 全体のロードに失敗する
    assert set(hooks) == {"hooks"}
    commands = [
        entry["command"]
        for groups in hooks["hooks"].values()
        for group in groups
        for entry in group["hooks"]
    ]
    assert len(commands) == 6
    for command in commands:
        # ${CLAUDE_PLUGIN_ROOT} は Codex に無いので絶対パスへ展開済みであること
        assert "${CLAUDE_PLUGIN_ROOT}" not in command
        assert Path(command).is_file()

    for name in ("cyclegen-cycle", "cyclegen-memory", "cyclegen-glossary",
                 "cyclegen-ops", "cyclegen", "cyclegen-init",
                 "cyclegen-onboarding"):
        assert (env.skills_dir / name / "SKILL.md").is_file()

    # ★SKILL_MAP と実配置の一致を機構で固定する（CYCLE17.2）。
    # onboarding は payload に入っていたのに SKILL_MAP へ足し忘れていたため
    # Codex 面にだけ配置されない状態だった。上の名前リストだけだと同じ
    # 追加漏れをテストも一緒に見落とす（配布物は静的検証を回すまで信じない・14.11）。
    assert {p.name for p in env.skills_dir.iterdir() if p.is_dir()} == {
        name for _, name, _ in setup_codex.SKILL_MAP
    }

    # 明示専用サイドカーと人格雛形（CYCLE14.17 finding#5）
    assert (env.skills_dir / "cyclegen" / "agents" / "openai.yaml").is_file()
    assert (env.skills_dir / "cyclegen-init" / "agents" / "openai.yaml").is_file()
    assert (env.skills_dir / "cyclegen-init" / "agents" / "cyclegen-persona.md").is_file()


def test_payload_is_copied_not_referenced(env):
    """配布物は必ずコピーされる（uvx キャッシュを指さない・CYCLE15.12.3 F15）。"""
    assert run() == 0
    assert (env.plugin_dir / ".claude-plugin" / "plugin.json").is_file()
    assert (env.plugin_dir / "VERSION").read_text(encoding="utf-8").strip()
    # hook の参照先が配置先の中にあること（配布元＝キャッシュを指していないこと）
    hooks = json.loads(env.hooks_json.read_text(encoding="utf-8"))
    command = hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert command.startswith(str(env.plugin_dir))


def test_hook_scripts_are_executable(env):
    assert run() == 0
    scripts = list((env.plugin_dir / "hooks").glob("*.sh"))
    assert len(scripts) == 6
    for script in scripts:
        assert script.stat().st_mode & 0o111


def test_use_path_writes_path_command(env):
    assert run("--use-path") == 0
    config = env.config_toml.read_text(encoding="utf-8")
    assert 'command = "cyclegen-mcp"' in config
    assert "uvx" not in config


# --------------------------------------------------------------------------
# 冪等性 — 2回実行しても壊れない
# --------------------------------------------------------------------------


def test_running_twice_is_idempotent(env):
    assert run() == 0
    first_config = env.config_toml.read_text(encoding="utf-8")
    first_hooks = env.hooks_json.read_text(encoding="utf-8")

    assert run() == 0
    assert env.config_toml.read_text(encoding="utf-8") == first_config
    assert env.hooks_json.read_text(encoding="utf-8") == first_hooks

    hooks = json.loads(first_hooks)
    commands = [
        entry["command"]
        for groups in hooks["hooks"].values()
        for group in groups
        for entry in group["hooks"]
    ]
    # 二重登録＝二重発火が起きていないこと
    assert len(commands) == len(set(commands)) == 6


def test_force_reruns_without_duplicating(env):
    assert run() == 0
    assert run("--force") == 0
    config = env.config_toml.read_text(encoding="utf-8")
    assert config.count("[mcp_servers.cyclegen]") == 1
    tomllib.loads(config)  # 壊れていない


# --------------------------------------------------------------------------
# 既存設定の保護
# --------------------------------------------------------------------------


def test_existing_config_is_preserved(env):
    env.config_toml.parent.mkdir(parents=True, exist_ok=True)
    env.config_toml.write_text(
        "# 利用者のコメント\n"
        "model = \"gpt-5\"\n"
        "\n"
        '[projects."/Users/x/work"]\n'
        'trust_level = "trusted"\n',
        encoding="utf-8",
    )
    assert run() == 0
    config = env.config_toml.read_text(encoding="utf-8")
    assert "# 利用者のコメント" in config
    assert 'model = "gpt-5"' in config
    assert '[projects."/Users/x/work"]' in config
    assert "[mcp_servers.cyclegen]" in config
    tomllib.loads(config)
    assert env.config_toml.with_name("config.toml.cyclegen-bak").is_file()


def test_existing_cyclegen_table_is_not_overwritten_without_force(env):
    env.config_toml.parent.mkdir(parents=True, exist_ok=True)
    original = '[mcp_servers.cyclegen]\ncommand = "/my/own/path"\n'
    env.config_toml.write_text(original, encoding="utf-8")
    assert run() == 0
    assert env.config_toml.read_text(encoding="utf-8") == original


def test_force_replaces_table_but_keeps_user_subtables(env):
    """`--force` でも `[mcp_servers.cyclegen.tools.*]`（利用者の承認設定）は残す。"""
    env.config_toml.parent.mkdir(parents=True, exist_ok=True)
    env.config_toml.write_text(
        '[mcp_servers.cyclegen]\ncommand = "/old/binary"\n'
        "\n"
        "[mcp_servers.cyclegen.tools.memory_search]\n"
        'approval_mode = "approve"\n',
        encoding="utf-8",
    )
    assert run("--force") == 0
    config = env.config_toml.read_text(encoding="utf-8")
    assert "/old/binary" not in config
    assert 'command = "uvx"' in config
    assert "[mcp_servers.cyclegen.tools.memory_search]" in config
    data = tomllib.loads(config)
    assert data["mcp_servers"]["cyclegen"]["command"] == "uvx"
    assert (
        data["mcp_servers"]["cyclegen"]["tools"]["memory_search"]["approval_mode"]
        == "approve"
    )


def test_other_hooks_are_untouched(env):
    env.hooks_json.parent.mkdir(parents=True, exist_ok=True)
    env.hooks_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [{"type": "command", "command": "/other/tool.sh"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    assert run() == 0
    hooks = json.loads(env.hooks_json.read_text(encoding="utf-8"))
    commands = [
        entry["command"]
        for groups in hooks["hooks"].values()
        for group in groups
        for entry in group["hooks"]
    ]
    assert "/other/tool.sh" in commands
    assert len(commands) == 7


def test_stale_manual_wiring_is_replaced(env):
    """旧来の手動配線（~/.cyclegen/hooks/ 直置き）は掃除される＝二重発火を防ぐ。"""
    env.hooks_json.parent.mkdir(parents=True, exist_ok=True)
    env.hooks_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/Users/x/.cyclegen/hooks/remind-primer.sh",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    assert run() == 0
    hooks = json.loads(env.hooks_json.read_text(encoding="utf-8"))
    commands = [
        entry["command"]
        for groups in hooks["hooks"].values()
        for group in groups
        for entry in group["hooks"]
    ]
    assert "/Users/x/.cyclegen/hooks/remind-primer.sh" not in commands
    assert len(commands) == 6


def test_user_owned_skill_is_not_replaced(env):
    target = env.skills_dir / "cyclegen-cycle"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("私のスキル\n", encoding="utf-8")
    assert run() == 0
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "私のスキル\n"


def test_broken_hooks_json_aborts_with_message(env, capsys):
    env.hooks_json.parent.mkdir(parents=True, exist_ok=True)
    env.hooks_json.write_text("{ これは JSON ではない", encoding="utf-8")
    assert run() == 1
    assert "JSON" in capsys.readouterr().err


# --------------------------------------------------------------------------
# --dry-run / --remove
# --------------------------------------------------------------------------


def test_dry_run_writes_nothing(env):
    assert run("--dry-run") == 0
    assert not env.config_toml.exists()
    assert not env.hooks_json.exists()
    assert not env.plugin_dir.exists()
    assert not env.skills_dir.exists()


def test_dry_run_matches_actual_write(env, capsys):
    run("--dry-run")
    planned = [line for line in capsys.readouterr().out.splitlines() if "✔" in line]
    run()
    actual = [line for line in capsys.readouterr().out.splitlines() if "✔" in line]
    assert planned == actual


def test_remove_undoes_wiring_but_keeps_memory_data(env):
    assert run() == 0
    memory_dir = env.plugin_dir.parent / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "store.db").write_text("記憶データ", encoding="utf-8")

    assert run("--remove") == 0

    config = env.config_toml.read_text(encoding="utf-8")
    assert "[mcp_servers.cyclegen]" not in config
    tomllib.loads(config)
    hooks = json.loads(env.hooks_json.read_text(encoding="utf-8"))
    assert hooks["hooks"] == {}
    assert not env.plugin_dir.exists()
    for name in ("cyclegen-cycle", "cyclegen-init"):
        assert not (env.skills_dir / name).exists()
    # 記憶ストアのデータには触れない
    assert (memory_dir / "store.db").read_text(encoding="utf-8") == "記憶データ"


def test_remove_keeps_other_hooks(env):
    env.hooks_json.parent.mkdir(parents=True, exist_ok=True)
    env.hooks_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [{"type": "command", "command": "/other/tool.sh"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    run()
    assert run("--remove") == 0
    hooks = json.loads(env.hooks_json.read_text(encoding="utf-8"))
    commands = [
        entry["command"]
        for groups in hooks["hooks"].values()
        for group in groups
        for entry in group["hooks"]
    ]
    assert commands == ["/other/tool.sh"]


def test_remove_on_clean_environment_is_safe(env):
    assert run("--remove") == 0


def test_remove_does_not_delete_user_owned_skill(env):
    target = env.skills_dir / "cyclegen-ops"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("私のスキル\n", encoding="utf-8")
    assert run("--remove") == 0
    assert (target / "SKILL.md").is_file()


# --------------------------------------------------------------------------
# 補助関数
# --------------------------------------------------------------------------


def test_strip_keeps_subtables():
    text = (
        "[other]\nx = 1\n\n"
        '[mcp_servers.cyclegen]\ncommand = "a"\n'
        "[mcp_servers.cyclegen.tools.t]\ny = 2\n"
    )
    stripped, removed = setup_codex.strip_cyclegen_table(text)
    assert removed is True
    assert "[mcp_servers.cyclegen]" not in stripped
    assert "[mcp_servers.cyclegen.tools.t]" in stripped
    assert "[other]" in stripped


def test_uv_absence_is_reported(env, monkeypatch, capsys):
    monkeypatch.setattr(setup_codex.shutil, "which", lambda name: None)
    assert run("--dry-run") == 0
    out = capsys.readouterr().out
    assert "uvx が見つかりません" in out
    assert "astral.sh/uv/install" in out
