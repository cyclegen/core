#!/bin/bash
# Doフェーズ完了処理のリマインド
# hooks/PreToolUse (matcher: Write) から呼び出される
# CYCLE14.13: exit0 plain stdoutはモデル非到達(finding#4)→JSON additionalContextで注入
# 出力形式: hookSpecificOutput.additionalContext（PreToolUseのplain stdoutはモデルに届かないため）
#
# 2つの場面でリマインドを出す:
# 1. ダイジェスト_承認前を作成しようとしている → 知見提案済みか確認
# 2. CYCLE進行ディレクトリにCYCLE番号付きファイルを書いたがダイジェストではない → ダイジェスト別途作成を促す
#    CYCLE進行ディレクトリ = docs/cycles/（開発構造）／ドキュメント/91_サイクル進行/・docs/91_cycles/（標準構造）

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# additionalContext JSON を stdout に出す
emit() {
  jq -n --arg ctx "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",additionalContext:$ctx}}'
}

# ケース1: ダイジェスト_承認前の作成
if echo "$FILE_PATH" | grep -q "ダイジェスト_承認前"; then
  emit "$(cat <<'EOF'
[知見提案リマインド]
ダイジェスト_承認前を作成しようとしています。
Doフェーズの手順を確認してください:

1. 知見の抽出・提案 → JAYに提示済みですか？
2. ダイジェスト_承認前を作成 ← 今ここ
3. 「Checkフェーズに入ります」と宣言して停止

知見の提案がまだの場合は、先にJAYに提示してください。
EOF
)"
  exit 0
fi

# ケース2: CYCLE進行ディレクトリにCYCLE番号付きファイルだがダイジェストではない
# 開発構造(docs/cycles/)・標準構造(ドキュメント/91_サイクル進行/・docs/91_cycles/)の両方を吸収する
if echo "$FILE_PATH" | grep -qE "(docs/cycles/|91_サイクル進行/|91_cycles/)CYCLE" && ! echo "$FILE_PATH" | grep -qiE "ダイジェスト|digest"; then
  emit "$(cat <<'EOF'
[ダイジェスト作成リマインド]
CYCLE進行ディレクトリにCYCLE関連ファイルを作成しようとしていますが、ダイジェストではありません。
このファイルとは別に、CYCLE完了時にダイジェスト_承認前を作成する必要があります。

Doフェーズの完了処理:
1. 知見の抽出・提案
2. ダイジェスト_承認前を作成（この作業成果物とは別ファイル）
3. 「Checkフェーズに入ります」と宣言して停止
EOF
)"
fi

exit 0
