#!/bin/bash
# PostToolUse hook: cycle_complete後に利用者指示ファイル（CLAUDE.md / AGENTS.md）末尾の「プロジェクト状態」更新リマインド
# CYCLE14.13: exit0 plain stdoutはモデル非到達(finding#4)→JSON additionalContextで注入
# 出力形式: hookSpecificOutput.additionalContext（PostToolUseのplain stdoutはモデルに届かないため）

# 共通のJSON関数（jq非依存・CYCLE20.4 / F-6）
_HOOK_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ ! -f "$_HOOK_DIR/_json.sh" ]; then
  echo "CycleGen: hooks/_json.sh が見つかりません（規律層は注入されません）" >&2
  exit 0
fi
. "$_HOOK_DIR/_json.sh"

cat > /dev/null  # stdin(JSON)を読み捨て（EPIPE回避）

MSG=$(cat <<'EOF'
[プロジェクト状態 更新リマインド]
cycle_completeが完了しました。利用者指示ファイル（Claude Code: CLAUDE.md ／ Codex等: AGENTS.md）末尾の「プロジェクト状態」セクションを更新してください:
- 現在のCYCLE
- 最終更新日
- 直近の活動（先頭にエントリ追加）
- 次のアクション（必要に応じて）
EOF
)

emit_context "PostToolUse" "$MSG"
