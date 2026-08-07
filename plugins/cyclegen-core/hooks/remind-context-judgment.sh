#!/bin/bash
# CYCLE12.7.8: memory_store時のContext判定リマインド
# CYCLE12.8.2: 定義済みContext一覧を追加
# CYCLE14.13: exit0 plain stdoutはモデル非到達(finding#4)→JSON additionalContextで注入
#
# PreToolUse hook: memory_store呼び出し時に発動
# AIエディタに「Contextは記憶の内容で判断せよ」とリマインドする
# 出力形式: hookSpecificOutput.additionalContext（PreToolUseのplain stdoutはモデルに届かないため）

# 共通のJSON関数（jq非依存・CYCLE20.4 / F-6）
_HOOK_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ ! -f "$_HOOK_DIR/_json.sh" ]; then
  echo "CycleGen: hooks/_json.sh が見つかりません（規律層は注入されません）" >&2
  exit 0
fi
. "$_HOOK_DIR/_json.sh"

cat > /dev/null  # stdin(JSON)を読み捨て（EPIPE回避）

MSG=$(cat <<'EOF'
[Context判定リマインド]
memory_storeのcontext引数は「記憶の内容が属するContext」で判断すること。
「今のセッション/作業のContext」ではない。

例: 設計CYCLEで実装に関する知見を保存する場合 → context=implementation
例: 実装CYCLEで設計判断を保存する場合 → context=planning

定義済みContext（この中から選ぶこと）:
  planning / implementation / debugging / review / learning
  documentation / operations / research / strategy

確信がなければcontext引数を省略してよい（サーバー側でembedding類似度による自動判定が行われる）。
未定義のContextを指定した場合も自動判定に切り替わる。
EOF
)

emit_context "PreToolUse" "$MSG"
