#!/bin/bash
# CYCLE13.1: FR028 CYCLE開始時・完了時のシステムリマインドhook
#
# UserPromptSubmit hook: ユーザーのメッセージ内容に応じてリマインドを注入
#
# 1. CYCLE開始パターン → memory_search リマインド
# 2. CYCLE完了パターン → memory_mark_used リマインド
#
# 出力形式（CYCLE14.17 finding#3対応）: 該当時のみ JSON additionalContext を出力。
#   {"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"..."}}
#   ＝Claude Code / Codex 共通。非該当時は無出力（exit 0 の no-op＝両ツールが受理）。
#   plain stdout は Codex 0.142.5 が拒否するためJSON化した（単一ソース維持）。

# 共通のJSON関数（jq非依存・CYCLE20.4 / F-6）
_HOOK_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ ! -f "$_HOOK_DIR/_json.sh" ]; then
  echo "CycleGen: hooks/_json.sh が見つかりません（規律層は注入されません）" >&2
  exit 0
fi
. "$_HOOK_DIR/_json.sh"

# ユーザーのメッセージを取得（stdinから読み取り）
USER_MESSAGE=$(cat)

REMIND=""

# CYCLE開始パターン: 「CYCLE」かつ「始め|はじめ|開始|start|再開|さいかい」
# FR038(CYCLE14.19): ひらがな・活用形の表記ゆれに対応（例「はじめて」「はじめる」）。
# 過検出は「CYCLE」併用条件（上のgrep -qi "CYCLE"とのAND）で緩和される。
if echo "$USER_MESSAGE" | grep -qi "CYCLE" && echo "$USER_MESSAGE" | grep -qiE "始め|はじめ|開始|start|再開|さいかい"; then
  REMIND="[CYCLE開始リマインド]
memory_searchを呼んで、作業に関連する記憶を検索してください。
検索結果のreason（なぜこの記憶が返されたか）を利用者に伝えてから作業を開始すること。
作業量を Nサイクル（約N×60分）形式で見積もり、提示してから着手すること。"
fi

# CYCLE完了パターン: 「CYCLE」かつ「完了|終了|おわり|終わ|finish|complete」、
# または「Check|Action|承認|しょうにん」かつ「フェーズ|phase」
# FR038(CYCLE14.19): ひらがな・活用形に対応（例「おわり」「終わって」「しょうにん」）。
COMPLETE=""
if echo "$USER_MESSAGE" | grep -qi "CYCLE" && echo "$USER_MESSAGE" | grep -qiE "完了|終了|おわり|終わ|finish|complete"; then
  COMPLETE="[CYCLE完了リマインド]
このCYCLEで参照・活用した記憶に memory_mark_used を呼んでください。
mark_usedはMemory Precisionの測定データになります。呼び忘れると計測精度が下がります。"
elif echo "$USER_MESSAGE" | grep -qiE "Check|Action|承認|しょうにん" && echo "$USER_MESSAGE" | grep -qiE "フェーズ|phase"; then
  COMPLETE="[CYCLE完了リマインド]
このCYCLEで参照・活用した記憶に memory_mark_used を呼んでください。
mark_usedはMemory Precisionの測定データになります。呼び忘れると計測精度が下がります。"
fi

# 該当したリマインドを結合
if [ -n "$REMIND" ] && [ -n "$COMPLETE" ]; then
  REMIND="${REMIND}
${COMPLETE}"
elif [ -n "$COMPLETE" ]; then
  REMIND="$COMPLETE"
fi

# 該当があればJSON出力、なければ無出力（no-op）
if [ -n "$REMIND" ]; then
  emit_context "UserPromptSubmit" "$REMIND"
fi
