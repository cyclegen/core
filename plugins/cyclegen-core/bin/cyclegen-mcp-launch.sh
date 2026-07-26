#!/bin/bash
# CycleGen MCP bootstrap launcher (POSIX / macOS / Linux)
#
# ★CYCLE15.12.2 判断D-1 以降、これは .mcp.json の既定の起動経路では *ない*。
#   既定は `.mcp.json` が `uvx` を直接起動する（Windowsを含む全OS・全サーフェスで同一の
#   1本の経路にするため。.mcp.json には OS 分岐機構が無く「Windowsでも動く1つのコマンド」
#   を選ぶしかない ＝ CYCLE15.12.2 §5-2）。
#   本スクリプトは「uvの自動導入まで面倒を見る」経路として *残してある*（判断E2の緩和策）。
#   POSIX環境で導入摩擦をゼロにしたい場合は、.mcp.json の command をこのパスに戻せばよい。
#
# 呼ばれた場合の動作: uvx/uv を解決して cyclegen-mcp を起動する。
# 解決順（フォールバック階段・CYCLE15.2 §3.2）:
#   1. uvx が PATH にあれば         → uvx --from <PKG> cyclegen-mcp
#   2. uv があれば                  → uv tool run --from <PKG> cyclegen-mcp
#   3. どちらも無ければ uv を自動導入 → 再試行
#   4. 導入失敗                     → stderr に手動導入手順を明示して終了（silent failure 禁止）
#
# 環境変数:
#   CYCLEGEN_PKG   起動対象パッケージ指定子（デフォルト: cyclegen[semantic,docx]）。
#                  semantic=意味検索の埋め込み(fastembed)、docx=document_finish。
#                  これらを省くと memory_search が縮退し finish 系ツールも出ない
#                  （CYCLE15.3 実発火で確認）。開発・検証時はローカル wheel を指せる
#                  （例: /path/to/cyclegen-0.1.0-py3-none-any.whl[semantic,docx]）。
set -euo pipefail

PKG="${CYCLEGEN_PKG:-cyclegen[semantic,docx]}"
# uv がユーザーローカル導入される既定の場所も PATH に含めておく
export PATH="$HOME/.local/bin:$PATH"

log() { echo "[cyclegen-mcp-launch] $*" >&2; }

run_with_uvx() { exec uvx --from "$PKG" cyclegen-mcp "$@"; }
run_with_uv()  { exec uv tool run --from "$PKG" cyclegen-mcp "$@"; }

if command -v uvx >/dev/null 2>&1; then
    run_with_uvx "$@"
elif command -v uv >/dev/null 2>&1; then
    run_with_uv "$@"
else
    log "uv / uvx が見つかりません。uv を自動導入します（https://astral.sh/uv）..."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh >&2 || true
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh >&2 || true
    fi
    export PATH="$HOME/.local/bin:$PATH"
    if command -v uvx >/dev/null 2>&1; then
        run_with_uvx "$@"
    elif command -v uv >/dev/null 2>&1; then
        run_with_uv "$@"
    else
        log "ERROR: uv の自動導入に失敗しました。"
        log "手動導入してください:"
        log "  macOS/Linux : curl -LsSf https://astral.sh/uv/install.sh | sh"
        log "  または       : pip install uv"
        log "導入後、AIクライアントを再起動すると CycleGen が有効になります。"
        exit 1
    fi
fi
