#!/bin/bash
# cycle_complete呼び出し前にCYCLEドキュメント（ダイジェスト等）の存在を検証するガードレール
# hooks/PreToolUse (cycle_complete) から呼び出される
#
# 設計思想（FR036-B）: 真の承認ゲートはHITL（ツール実行許可）であり、本ガードはspeed-bump。
#   目的は「正しく置かれたダイジェストを誤ブロックしないこと」であって、破られなさは追わない。
#
# CYCLEドキュメント判定: CYCLE番号を含み、かつ以下のいずれかを含むファイル名（多言語対応）
#   サイクル / メモ / 完了 / 報告 / ダイジェスト / completion / report / digest
# 探索: プロジェクトルート配下を再帰探索し、標準構造（ドキュメント/91_サイクル進行/・docs/91_cycles/）も
#   開発構造（docs/cycles/）も1ロジックで吸収する。ルートはenv非依存で特定（CC/Codex両対応）。

# 共通のJSON関数（jq非依存・CYCLE20.4 / F-6）
_HOOK_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ ! -f "$_HOOK_DIR/_json.sh" ]; then
  echo "CycleGen: hooks/_json.sh が見つかりません（CYCLEドキュメントの確認は行われません）" >&2
  exit 0
fi
. "$_HOOK_DIR/_json.sh"

INPUT=$(cat)
CYCLE_ID=$(json_get_string "$INPUT" cycle_id)

if [ -z "$CYCLE_ID" ]; then
  # cycle_idが指定されていない場合はチェックをスキップ
  exit 0
fi

# "CYCLE" プレフィックスを除去して番号部分を取得
CYCLE_NUM=$(echo "$CYCLE_ID" | sed 's/^CYCLE//')

# --- プロジェクトルート特定（env非依存・CC/Codex両対応） ---
# 1) CLAUDE_PROJECT_DIR（CC提供）があれば最優先で採用
# 2) 無ければ stdin JSON の cwd（無ければ現在のcwd）から、マーカー
#    （CLAUDE.md / AGENTS.md / .git）を上方探索してプロジェクトルートを確定する。
#    マーカーが見つからなければ開始ディレクトリをそのままルートとする（"/"まで遡らない）。
if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then
  ROOT="$CLAUDE_PROJECT_DIR"
else
  CWD_FROM_JSON=$(json_get_string "$INPUT" cwd 2>/dev/null)
  START_DIR="${CWD_FROM_JSON:-$PWD}"
  ROOT="$START_DIR"
  d="$START_DIR"
  while [ -n "$d" ] && [ "$d" != "/" ]; do
    if [ -e "$d/CLAUDE.md" ] || [ -e "$d/AGENTS.md" ] || [ -e "$d/.git" ]; then
      ROOT="$d"
      break
    fi
    d=$(dirname "$d")
  done
fi

# --- CYCLEドキュメントの再帰探索（探索範囲はROOT配下に限定・重量ディレクトリはprune） ---
FOUND=false
while IFS= read -r f; do
  fname=$(basename "$f")
  if echo "$fname" | grep -qE "サイクル|メモ|完了|報告|ダイジェスト|completion|report|digest"; then
    FOUND=true
    break
  fi
done < <(find "$ROOT" \
  \( -name node_modules -o -name .git -o -name .venv -o -name __pycache__ \) -prune -o \
  -type f -name "CYCLE${CYCLE_NUM}_*" -print 2>/dev/null)

if [ "$FOUND" = false ]; then
  echo "CYCLEドキュメントが見つかりません。cycle_complete の前に、標準のCYCLE進行ディレクトリ（日本語: ドキュメント/91_サイクル進行/ ・英語: docs/91_cycles/）に CYCLE${CYCLE_NUM}_ダイジェスト.md を作成してください。" >&2
  exit 2
fi

exit 0
