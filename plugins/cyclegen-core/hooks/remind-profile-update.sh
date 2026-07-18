#!/bin/bash
# PostToolUse hook: cycle_complete後に利用者指示ファイル（CLAUDE.md / AGENTS.md）末尾の「プロジェクト状態」更新リマインド
# CYCLE14.13: exit0 plain stdoutはモデル非到達(finding#4)→JSON additionalContextで注入
# 出力形式: hookSpecificOutput.additionalContext（PostToolUseのplain stdoutはモデルに届かないため）

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

jq -n --arg ctx "$MSG" '{hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:$ctx}}'
