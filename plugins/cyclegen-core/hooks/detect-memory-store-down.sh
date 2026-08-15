#!/bin/bash
# CYCLE20.7（案a）: 記憶ストア（MCPサーバ）が起動しなくなったことを検知して知らせる。
#   対象の find: F-25（ホストが「要認証」と誤判定しディスクに焼く）／F-23（落ちると
#   セッション内に復帰の道が無い）／F-24（障害時の手順が配布物に0件）／F-16・M-17
#   （状態を確かめる手段がAIに聞くことしかない）。
#
# ★なぜ hook なのか＝診断は診断対象の外側に置かないと機能しない。
#   記憶ストアが落ちているとき、記憶ストア自身（memory_diagnostics）は返事ができない。
#   MCPが死んでも hooks は動く（CYCLE17.6.4 で実証）。ここが唯一の観測点。
#
# ★何を見ているか
#   ホストは「認証が要るMCPサーバ」の一覧を ~/.claude/mcp-needs-auth-cache.json に
#   書き、以後の起動でその判断を再利用する。CycleGenのMCPサーバはローカルstdioで
#   認証の概念が無いので、ここに載っていること自体が誤判定である（WIN-01実測）。
#   焼かれると、アプリを新品で起動し直してもサーバが二度と起動されない。
#
# ★捕まえられる範囲は半分（正直に書いておく）
#   捕まるのは F-25 の形（起動されない）だけ。F-22 の形（起動はしているが最初の呼び出しが
#   返らない）は、このファイルに何も書かれないので捕まらない。F-22 は 20.7 の
#   preimport（server.py）で根から潰した側。
#
# ★このファイルを CycleGen 側から消さない（案d を採らない）。
#   相手が能動管理している設定を製品が勝手に書き換えるのは筋が悪い（M-12の裏返し）。
#   検知して知らせるところまでにする。消すかどうかは利用者が決める。

# 共通のJSON関数（jq非依存・CYCLE20.4 / F-6）
_HOOK_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ ! -f "$_HOOK_DIR/_json.sh" ]; then
  exit 0
fi
. "$_HOOK_DIR/_json.sh"

# stdin を読み捨てる（パイプの後段がブロックしないように）
cat > /dev/null

# ホームの求めかたを2通り持つ。Windowsのbashでは $HOME が
# %USERPROFILE% と違う場所を指すことがあり、片方だけだと静かに空振りする。
_CANDIDATES="$HOME/.claude/mcp-needs-auth-cache.json"
if [ -n "$USERPROFILE" ]; then
  _CANDIDATES="$_CANDIDATES
$USERPROFILE/.claude/mcp-needs-auth-cache.json"
fi

_CACHE=""
while IFS= read -r _path; do
  [ -n "$_path" ] || continue
  if [ -f "$_path" ]; then
    _CACHE=$_path
    break
  fi
done <<EOF
$_CANDIDATES
EOF

# ファイルが無いのが正常。何も言わずに終わる。
[ -n "$_CACHE" ] || exit 0

# キー名だけを取り出す（値の中にたまたま現れる文字列を拾わないよう、`{` が続くものに限る）。
_KEYS=$(grep -oE '"[^"]+"[[:space:]]*:[[:space:]]*\{' "$_CACHE" 2>/dev/null)

case $_KEYS in
  *cyclegen*) ;;
  *) exit 0 ;;
esac

NOTICE=$(cat <<'EOF'
[CycleGen ⚠ 記憶ストアが起動できていない可能性があります]
AIクライアントが CycleGen のMCPサーバーを「認証が必要なサーバー」として記録しています。
CycleGen のサーバーはこのパソコンの中で動くもので、認証は使いません。誤った記録です。
この記録が残っているあいだ、アプリを起動し直しても記憶ツールだけが動きません
（スキルや規律は動き続けるので、一見すると正常に見えます）。

■ 記憶ツール（memory_search など）が今このセッションで使えているなら、この案内は無視してよい。
■ 使えないなら、次の順に試す:
  1. アプリを「終了」する。★ウィンドウの × で閉じるだけでは終了しない。
     Windows: 画面右下の「^」からアプリのアイコンを右クリック →「終了」
     macOS  : メニューバーのアプリ名 →「終了」（Cmd+Q）
  2. アプリを起動し直す。
  3. それでも戻らないとき: ホームフォルダの中の「.claude」フォルダにある
     mcp-needs-auth-cache.json を削除して、もう一度アプリを起動する。
     Windows: %USERPROFILE%\.claude\mcp-needs-auth-cache.json
     macOS  : ~/.claude/mcp-needs-auth-cache.json
     ※このファイルは削除しても問題ない（消えた分は使うときに作り直される）。

※この案内は、要約せずそのまま利用者に伝えること。利用者が自分で操作する必要がある。
※AIがターミナルで uvx を叩いて起動を試す、といった開発者向けの手順は提案しないこと。
EOF
)

emit_context "UserPromptSubmit" "$NOTICE"
