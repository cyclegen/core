"""test_hooks_no_jq.py — 規律層hookのjq非依存（CYCLE20.4 / F-6）

背景（CYCLE17.4 F-6 の実測 → CYCLE18 知見1で分類訂正）:
配布物の規律層hook 6本すべてが `jq` に依存していた。jq を同梱するOSと
しないOSがあり（macOS 15 以降は /usr/bin/jq を同梱・14以下とWindowsは非同梱）、
jq が無い機体では規律層が丸ごと落ちる。**エラーは出ず、静かに何も注入されない。**

設計方針（CYCLE20.3 §5 / JAY確定③）: jq があっても使わない。経路を1本にする。
「jqがあれば使い、無ければbash」にすると、開発機では常にjq側が走るので
bash側が壊れていても誰も気づかない。

★ このテストの主役は「jqを1度も呼ばずに、正しいJSONを出すこと」の実行時確認である。
  静的なgrepは補助でしかない（`jq` という語はコメントに正当に現れる＝
  CYCLE19.1 発見3「引用と主張を機械は区別しない」）。

hookの発火はモデル不要で bash 直接実行（stdin→stdout）で実証できる
（CYCLE14.20 cheap check）。ここではその方法をそのまま使う。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

def _find_hooks() -> Path | None:
    """`plugins/cyclegen-core/hooks` を上方探索する（CYCLE20.6）。

    母艦（`_CycleGen_Ent/cyclegen/tests/...`）と公開Core（`<repo>/tests/...`）で
    **リポジトリルートまでの深さが1つ違う**。固定の `parents[n]` で書くと、
    母艦では正しく、公開Coreでは**リポジトリの外側**を指す。
    `test_setup_codex.py` が17.3で同じ落とし穴を記録しているので、そこに合わせる。
    """
    for parent in Path(__file__).resolve().parents:
        cand = parent / "plugins" / "cyclegen-core" / "hooks"
        if (cand / "hooks.json").is_file():
            return cand
    return None


HOOKS = _find_hooks()

# stdinを読み捨てて必ず出力する4本と、入力に応じて出し分ける2本
ALWAYS_EMIT = {
    "remind-primer.sh": "UserPromptSubmit",
    "remind-context-judgment.sh": "PreToolUse",
    "remind-profile-update.sh": "PostToolUse",
}
ALL_HOOKS = (
    sorted(p.name for p in HOOKS.glob("*.sh") if not p.name.startswith("_"))
    if HOOKS is not None
    else []
)

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or HOOKS is None,
    reason="bash が無い環境、または plugins/cyclegen-core を含まないチェックアウトではhookを検証できない",
)


@pytest.fixture()
def jq_trap(tmp_path):
    """jq を「呼んだら分かる」偽物に差し替えた PATH を返す。

    単にPATHからjqを消すのではなく、呼ばれたら痕跡を残す実行ファイルを置く。
    「jqが無くても動く」だけでなく「jqを呼んでいない」ことまで見たいため。
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "jq_was_called"
    trap = bindir / "jq"
    trap.write_text(
        "#!/bin/sh\n"
        f'echo "called" >> "{marker}"\n'
        'echo "jq should not be called" >&2\n'
        "exit 127\n",
        encoding="utf-8",
    )
    trap.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env.pop("CLAUDE_PROJECT_DIR", None)
    return env, marker


def _run(hook: str, stdin: str, env: dict, cwd: Path | None = None):
    return subprocess.run(
        ["bash", str(HOOKS / hook)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
        timeout=30,
    )


# --------------------------------------------------------------------------
# 1. 静的チェック（補助）— jqを「実行する」行が無いこと
# --------------------------------------------------------------------------

# 行頭・パイプ・;・&&・$( の直後に現れる jq = 実行。コメント中の "jq" は対象外。
_JQ_CALL = re.compile(r"(?:^|[|;&(]|\$\()\s*jq\b")


def test_no_hook_invokes_jq():
    offenders = []
    for p in sorted(HOOKS.glob("*.sh")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            code = line.split("#", 1)[0]
            if _JQ_CALL.search(code):
                offenders.append(f"{p.name}:{i}: {line.strip()}")
    assert offenders == [], "hookがjqを実行している:\n" + "\n".join(offenders)


def test_json_helper_is_shipped():
    """_json.sh が配布物に含まれていること（無いと6本すべてが黙って何も出さない）。"""
    assert (HOOKS / "_json.sh").is_file()


# --------------------------------------------------------------------------
# 2. 実行時 — jqが無くても正しいJSONを出す
# --------------------------------------------------------------------------


@pytest.mark.parametrize("hook,event", sorted(ALWAYS_EMIT.items()))
def test_always_emitting_hooks_produce_valid_json_without_jq(hook, event, jq_trap):
    env, marker = jq_trap
    r = _run(hook, '{"session_id":"s1"}', env)

    assert r.returncode == 0, r.stderr
    assert not marker.exists(), f"{hook} が jq を呼んだ"

    payload = json.loads(r.stdout)
    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == event
    assert out["additionalContext"].strip(), "本文が空"


def test_primer_uv_warning_survives_escaping(tmp_path):
    """uv不在の案内文には `"irm ... | iex"` が入る＝実データで最も引用符が多い経路。

    ここが壊れるとJSONが不正になり、additionalContext が丸ごと落ちる。
    しかも落ちるのは「uv が無い利用者」＝いちばん案内が要る人だけなので、
    開発機では絶対に再現しない。
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "jq_was_called"
    (bindir / "jq").write_text(
        f'#!/bin/sh\necho called >> "{marker}"\nexit 127\n', encoding="utf-8"
    )
    (bindir / "jq").chmod(0o755)

    env = dict(os.environ)
    # uv / uvx が引けない PATH を作る（hook は $HOME/.local/bin も足すのでHOMEも移す）
    env["PATH"] = f"{bindir}{os.pathsep}/usr/bin{os.pathsep}/bin"
    env["HOME"] = str(tmp_path)
    env.pop("CLAUDE_PROJECT_DIR", None)

    r = _run("remind-primer.sh", "{}", env)
    assert r.returncode == 0, r.stderr
    assert not marker.exists()

    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "uv が見つかりません" in ctx
    assert 'powershell -c "irm https://astral.sh/uv/install.ps1 | iex"' in ctx


def test_cycle_memory_hook_emits_on_cycle_start_without_jq(jq_trap):
    env, marker = jq_trap
    r = _run("remind-cycle-memory.sh", "CYCLE20.4を始めてください", env)

    assert r.returncode == 0, r.stderr
    assert not marker.exists()
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit"
    assert "memory_search" in out["additionalContext"]


def test_cycle_memory_hook_is_silent_when_not_matching(jq_trap):
    """非該当時は無出力（no-op）。これも jq を使わずに保たれること。"""
    env, marker = jq_trap
    r = _run("remind-cycle-memory.sh", "こんにちは", env)

    assert r.returncode == 0
    assert not marker.exists()
    assert r.stdout.strip() == ""


def test_knowledge_proposal_hook_reads_nested_file_path_without_jq(jq_trap):
    env, marker = jq_trap
    stdin = json.dumps(
        {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/tmp/docs/cycles/CYCLE20.4_ダイジェスト_承認前.md",
                "content": "本文",
            },
        }
    )
    r = _run("remind-knowledge-proposal.sh", stdin, env)

    assert r.returncode == 0, r.stderr
    assert not marker.exists()
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert "知見提案リマインド" in out["additionalContext"]


def test_knowledge_proposal_hook_ignores_key_appearing_inside_content(jq_trap):
    """書き込む中身に "file_path" と書かれていても、そちらを拾わないこと。

    PreToolUse:Write の入力には content が丸ごと入る。キー名だけで探すと、
    利用者が書こうとしている文章の中の文字列を本物のキーと取り違える。
    JSONでは値の中の引用符は \\" になるので、「開き引用符の直前が { か , か行頭」
    という条件で区別できる。
    """
    env, marker = jq_trap
    stdin = json.dumps(
        {
            "tool_name": "Write",
            "tool_input": {
                "content": 'サンプル: "file_path": "/tmp/わな_ダイジェスト_承認前.md"',
                "file_path": "/tmp/notes/memo.md",
            },
        }
    )
    r = _run("remind-knowledge-proposal.sh", stdin, env)

    assert r.returncode == 0, r.stderr
    assert not marker.exists()
    # 本物の file_path は memo.md なので、どちらのリマインドにも該当しない
    assert r.stdout.strip() == "", f"content内の文字列を拾った: {r.stdout!r}"


def test_check_cycle_complete_skips_without_cycle_id(jq_trap):
    env, marker = jq_trap
    r = _run("check-cycle-complete.sh", '{"tool_input":{}}', env)

    assert r.returncode == 0
    assert not marker.exists()


def test_check_cycle_complete_blocks_when_digest_missing(jq_trap, tmp_path):
    env, marker = jq_trap
    root = tmp_path / "proj"
    (root / "docs" / "cycles").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("# proj", encoding="utf-8")

    stdin = json.dumps({"cwd": str(root), "tool_input": {"cycle_id": "CYCLE99.9"}})
    r = _run("check-cycle-complete.sh", stdin, env, cwd=root)

    assert not marker.exists()
    assert r.returncode == 2, f"ブロックされなかった: {r.stdout!r} {r.stderr!r}"
    assert "CYCLEドキュメントが見つかりません" in r.stderr


def test_check_cycle_complete_passes_when_digest_exists(jq_trap, tmp_path):
    env, marker = jq_trap
    root = tmp_path / "proj"
    (root / "docs" / "cycles").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("# proj", encoding="utf-8")
    (root / "docs" / "cycles" / "CYCLE99.9_ダイジェスト.md").write_text("x", encoding="utf-8")

    stdin = json.dumps({"cwd": str(root), "tool_input": {"cycle_id": "CYCLE99.9"}})
    r = _run("check-cycle-complete.sh", stdin, env, cwd=root)

    assert not marker.exists()
    assert r.returncode == 0, f"誤ブロック: {r.stderr!r}"


# --------------------------------------------------------------------------
# 3. エスケープ — 出力が壊れると additionalContext が黙って落ちる
# --------------------------------------------------------------------------

_ESCAPE_CASES = [
    pytest.param('ダブルクォート "引用" を含む', id="quote"),
    pytest.param(r"バックスラッシュ \ を含む", id="backslash"),
    pytest.param('powershell -c \\"irm https://astral.sh/uv/install.ps1 | iex\\"', id="primer-uv"),
    pytest.param("改行\nを含む", id="newline"),
    pytest.param("タブ\tを含む", id="tab"),
    pytest.param("復帰\rを含む", id="carriage-return"),
    pytest.param("日本語と絵文字 🎉 ✅ ★", id="unicode"),
    pytest.param('全部盛り "a\\b"\n\tc 🎉', id="mixed"),
]


@pytest.mark.parametrize("text", _ESCAPE_CASES)
def test_emit_context_round_trips(text, tmp_path):
    """json_escape を通した文字列が、JSONとして読み戻したとき元に戻ること。"""
    script = tmp_path / "run.sh"
    script.write_text(
        f'. "{HOOKS}/_json.sh"\nemit_context "UserPromptSubmit" "$1"\n', encoding="utf-8"
    )
    r = subprocess.run(
        ["bash", str(script), text], capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 0, r.stderr

    payload = json.loads(r.stdout)
    assert payload["hookSpecificOutput"]["additionalContext"] == text


def test_emit_context_drops_stray_control_characters(tmp_path):
    """JSON文字列に置けないC0制御文字は落とす（出力を壊すよりよい）。"""
    script = tmp_path / "run.sh"
    script.write_text(
        f'. "{HOOKS}/_json.sh"\nemit_context "UserPromptSubmit" "$(printf \'a\\001b\\002c\')"\n',
        encoding="utf-8",
    )
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] == "abc"


# --------------------------------------------------------------------------
# 4. 取り出し — Windowsのパスなど
# --------------------------------------------------------------------------


def _get(tmp_path: Path, payload: dict, key: str, *, ensure_ascii: bool = False) -> str:
    script = tmp_path / "get.sh"
    script.write_text(
        f'. "{HOOKS}/_json.sh"\njson_get_string "$1" "$2"\n', encoding="utf-8"
    )
    r = subprocess.run(
        ["bash", str(script), json.dumps(payload, ensure_ascii=ensure_ascii), key],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_json_get_string_unescapes_windows_path(tmp_path):
    """C:\\Users\\jay\\newdir — \\\\ を戻す前に \\n を改行にすると壊れる並び。"""
    payload = {"cwd": r"C:\Users\jay\newdir"}
    assert _get(tmp_path, payload, "cwd") == r"C:\Users\jay\newdir"


def test_json_get_string_returns_empty_for_missing_key(tmp_path):
    assert _get(tmp_path, {"cwd": "/tmp"}, "cycle_id") == ""


def test_json_get_string_returns_empty_for_null(tmp_path):
    """jq の `// empty` と同じ振る舞い（null は空として扱う）。"""
    assert _get(tmp_path, {"cwd": None}, "cwd") == ""


def test_json_get_string_handles_quotes_in_value(tmp_path):
    payload = {"tool_input": {"file_path": '/tmp/a"b.md'}}
    assert _get(tmp_path, payload, "file_path") == '/tmp/a"b.md'


def test_json_get_string_handles_japanese_path(tmp_path):
    payload = {"tool_input": {"file_path": "/tmp/ドキュメント/91_サイクル進行/CYCLE1_メモ.md"}}
    assert (
        _get(tmp_path, payload, "file_path")
        == "/tmp/ドキュメント/91_サイクル進行/CYCLE1_メモ.md"
    )


def test_json_get_string_decodes_unicode_escapes(tmp_path):
    r"""\uXXXX を実際の文字に戻すこと（jq -r と同じ振る舞い）。

    クライアントが非ASCIIを \u エスケープして渡してくると、
    標準構造（ドキュメント/91_サイクル進行/）の日本語パスだけが静かに一致しなくなる。
    エラーは出ないので気づけない＝F-6 と同じ「静かに落ちる」種類の欠陥。
    """
    payload = {"tool_input": {"file_path": "/tmp/ドキュメント/91_サイクル進行/CYCLE1_メモ.md"}}
    assert (
        _get(tmp_path, payload, "file_path", ensure_ascii=True)
        == "/tmp/ドキュメント/91_サイクル進行/CYCLE1_メモ.md"
    )


def test_json_get_string_decodes_surrogate_pair(tmp_path):
    r"""BMP外の文字（絵文字）は 🎉 のペアで来る。1文字に戻すこと。"""
    payload = {"cwd": "/tmp/🎉done"}
    assert _get(tmp_path, payload, "cwd", ensure_ascii=True) == "/tmp/🎉done"


def test_json_get_string_decodes_two_byte_range(tmp_path):
    """2バイト長になる範囲（ラテン拡張・ギリシャ文字など）も戻せること。"""
    payload = {"cwd": "/tmp/café-Ω"}
    assert _get(tmp_path, payload, "cwd", ensure_ascii=True) == "/tmp/café-Ω"


def test_all_hooks_are_covered():
    """hookが増えたらこのテストに追加すること（黙って未検証のhookが増えないように）。"""
    covered = set(ALWAYS_EMIT) | {
        "remind-cycle-memory.sh",
        "remind-knowledge-proposal.sh",
        "check-cycle-complete.sh",
    }
    assert set(ALL_HOOKS) == covered, f"未検証のhookがある: {set(ALL_HOOKS) - covered}"
