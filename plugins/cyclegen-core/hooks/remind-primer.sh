#!/bin/bash
# CYCLE14.3: FR034 F2タスク2 — UserPromptSubmit 薄い常時プライマー
#
# UserPromptSubmit hook: 毎ターン無条件に [P] の常時規律を注入する。
# SessionStart（初ターンのみ）では保てない「ターン跨ぎの規律」を毎ターン担保する。
#
# 設計: docs/design/CYCLE14.3_UserPromptSubmitプライマー設計案.md（案B）
# 出典: docs/design/CYCLE14_FR034-F1_配布棚卸し設計.md §7・§8・§9.2・§10
#
# 内容（4ブロック・案B）: 承認ゲート(P4) / 方針ファースト(P3-3) / 品質(P9) / 7±2(P11)
# ※Nサイクル見積(P5)はCYCLE開始時専用のため remind-cycle-memory.sh 側に置く（常時から分離）
#
# 出力形式（CYCLE14.17 finding#3対応）: JSON additionalContext。
#   {"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"..."}}
#   ＝Claude Code / Codex 共通形式（両ツールが受理）。plain stdout は Codex 0.142.5 が
#   "invalid user prompt submit JSON output" で拒否するためJSON化した（単一ソース維持）。
#
# 注: stdin（ユーザーメッセージ）は条件分岐に使わない。常時無条件で注入する。

# 共通のJSON関数（jq非依存・CYCLE20.4 / F-6）
_HOOK_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ ! -f "$_HOOK_DIR/_json.sh" ]; then
  echo "CycleGen: hooks/_json.sh が見つかりません（規律層は注入されません）" >&2
  exit 0
fi
. "$_HOOK_DIR/_json.sh"

# stdin を読み捨てる（パイプの後段がブロックしないように）
cat > /dev/null

PRIMER=$(cat <<'EOF'
[CycleGen 常時規律]
■承認ゲート: Planで合意したDo群が完了したら、Doの最後で必ず「Checkフェーズに入ります」と明示宣言して停止（成果物提示で暗黙終了しない／まだDo群が残るなら宣言しない＝早閉じしない）。人間の承認なしに Action（ダイジェスト確定・git commit・cycle_complete）を実行しない。※セットアップ・初期化（init）はCYCLE番号を持たず、ダイジェスト/承認ゲートの対象外（実作業CYCLEはCYCLE1から）。
■方針ファースト: すぐ手を動かさず、まず方針と全体像を整理してから着手する。
■品質: 不明点は推測せず確認。推測する場合は「推測ですが」と明記。成果物は常にファイルに残す。
■認知負荷: 一度に扱う論点は7±2に制限。超えたらグループ化。
詳細プロトコルは Skill `cyclegen-cycle` を参照。
EOF
)

# --- 緩和策③（CYCLE15.12.2 判断E2） ---
# .mcp.json が uvx を直接起動する（判断D-1）ため、uv が無いと MCP サーバーが起動せず
# 記憶ツール（memory_search 等）が一切出てこない。この状態は利用者から見えにくく、
# 「CycleGenが壊れている」としか映らないので、原因と対処を規律層から明示する。
# 規律層のhookはOS分岐できる（CC: shell / Codex: command_windows）が、MCPプロセス起動は
# 分岐できない（.mcp.json に OS 変種が無い）＝ここが案内を出せる唯一の層。
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uvx >/dev/null 2>&1 && ! command -v uv >/dev/null 2>&1; then
    PRIMER="${PRIMER}
■⚠ uv が見つかりません — CycleGenの記憶ツールは起動できません（他の機能は動きます）。
  導入 macOS/Linux : curl -LsSf https://astral.sh/uv/install.sh | sh
  導入 Windows     : powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"
  導入後、AIクライアントを再起動すると記憶ツールが有効になります。
  ※この案内は利用者にそのまま伝えること。"
fi

emit_context "UserPromptSubmit" "$PRIMER"
