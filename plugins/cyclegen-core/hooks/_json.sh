#!/bin/bash
# CYCLE20.4 (F-6): 規律層hookのjq依存を外すための共通関数。
#
# 背景: hook 6本すべてが jq に依存していた。jq を同梱するOSと同梱しないOSがあり
#   （macOS 15 以降は /usr/bin/jq を同梱・14以下とWindowsは非同梱＝CYCLE18 知見1）、
#   jq が無い機体では規律層が丸ごと落ちる。エラーは出ず、ただ静かに何も注入されない。
#
# 設計方針（CYCLE20.3 §5 / JAY確定③）:
#   ★ jq があっても使わない。経路を1本にする。
#     「jqがあれば使い、無ければbash」にすると、開発機では常にjq側が走るので
#     bash側が壊れていても誰も気づかない。F-6という欠陥そのものが
#     「環境によって経路が分かれ、分かれた先が検証されない」形で生まれている。
#     原因と同じ構造をした対処は、同じ場所で同じように壊れる。
#
#   ★ jqを外すのに新しい外部依存を持ち込まない。
#     使ってよいのは bash 組み込みと、既存hookが既に使っている sed / grep だけ。
#     awk・tr・python は「Windowsで確実にある」ことが未確認なので使わない。
#
#   ★ bash 3.2（macOS の /bin/bash）で動く書き方に限る。連想配列・${var^^} 等は使わない。

# --- JSON文字列エスケープ用の制御文字テーブル（読み込み時に1回だけ組み立てる） ---
# \b \t \n \f \r は個別に変換するので、ここには含めない。残りのC0制御文字は
# JSON文字列にそのまま置くと不正になるため、取り除く（リマインド文に意味を持たないため）。
_JSON_CTL=$(printf '\001\002\003\004\005\006\007\013\016\017\020\021\022\023\024\025\026\027\030\031\032\033\034\035\036\037')

# json_escape <文字列>
#   JSON文字列リテラルの中身としてそのまま埋め込める形に変換して stdout に出す。
#   （前後のダブルクォートは付けない）
#   日本語・絵文字などの非ASCIIは、UTF-8のまま出す（JSONの仕様上そのままでよい）。
json_escape() {
  local s=$1 i n c

  # ★ バックスラッシュを最初に処理する。順序を逆にすると、
  #   この後で自分が入れたバックスラッシュを二重にエスケープしてしまう。
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\b'/\\b}
  s=${s//$'\f'/\\f}
  s=${s//$'\r'/\\r}
  s=${s//$'\n'/\\n}
  s=${s//$'\t'/\\t}

  # 残ったC0制御文字を落とす（通常のリマインド文には現れない・保険）
  n=${#_JSON_CTL}
  i=0
  while [ "$i" -lt "$n" ]; do
    c=${_JSON_CTL:$i:1}
    case $s in
      *"$c"*) s=${s//"$c"/} ;;
    esac
    i=$((i + 1))
  done

  printf '%s' "$s"
}

# emit_context <hookEventName> <本文>
#   AIクライアントに文脈を注入するJSONを1行で stdout に出す。
#   形式は CYCLE14.17 で実機確定したもの（Claude Code / Codex 共通）:
#     {"hookSpecificOutput":{"hookEventName":"...","additionalContext":"..."}}
emit_context() {
  printf '{"hookSpecificOutput":{"hookEventName":"%s","additionalContext":"%s"}}\n' \
    "$(json_escape "$1")" "$(json_escape "$2")"
}

# _utf8_from_cp <コードポイント(10進)>
#   コードポイントをUTF-8のバイト列にして stdout に出す。
#   bash 3.2（macOSの /bin/bash）には printf '\uXXXX' が無いので、自分で組み立てる。
_utf8_from_cp() {
  local cp=$1 fmt
  if [ "$cp" -lt 128 ]; then
    fmt=$(printf '\\%03o' "$cp")
  elif [ "$cp" -lt 2048 ]; then
    fmt=$(printf '\\x%02x\\x%02x' \
      $((192 | (cp >> 6))) $((128 | (cp & 63))))
  elif [ "$cp" -lt 65536 ]; then
    fmt=$(printf '\\x%02x\\x%02x\\x%02x' \
      $((224 | (cp >> 12))) $((128 | ((cp >> 6) & 63))) $((128 | (cp & 63))))
  else
    fmt=$(printf '\\x%02x\\x%02x\\x%02x\\x%02x' \
      $((240 | (cp >> 18))) $((128 | ((cp >> 12) & 63))) \
      $((128 | ((cp >> 6) & 63))) $((128 | (cp & 63))))
  fi
  printf "$fmt"
}

# _json_unescape <JSON文字列リテラルの中身>
#   \" \\ \/ \n \t \r \b \f を元に戻す。左から1回だけ走査する。
#   ★ 逐次置換（\\ を先に戻してから \n を戻す等）では正しくない。
#     Windowsのパス "C:\\Users\\new" は \\ を戻すと C:\Users\new になるが、
#     先に \n を改行に変えると \Users\new の "\n" を壊す。走査は1回でなければならない。
_json_unescape() {
  local s=$1 out= i=0 n=${#1} c d cp lo ch
  while [ "$i" -lt "$n" ]; do
    c=${s:$i:1}
    if [ "$c" = '\' ]; then
      i=$((i + 1))
      d=${s:$i:1}
      case $d in
        n) out=$out$'\n' ;;
        t) out=$out$'\t' ;;
        r) out=$out$'\r' ;;
        b) out=$out$'\b' ;;
        f) out=$out$'\f' ;;
        u)
          # ★ \uXXXX の変換は省略できない。
          #   クライアントが非ASCIIを \u エスケープして渡してくると、
          #   日本語のパス（標準構造は ドキュメント/91_サイクル進行/）だけが
          #   静かに一致しなくなる。エラーは出ないので気づけない。
          cp=$((16#${s:$((i + 1)):4}))
          i=$((i + 4))
          # サロゲートペア（絵文字などBMP外の文字）を1つのコードポイントに戻す
          if [ "$cp" -ge 55296 ] && [ "$cp" -le 56319 ] && [ "${s:$((i + 1)):2}" = '\u' ]; then
            lo=$((16#${s:$((i + 3)):4}))
            if [ "$lo" -ge 56320 ] && [ "$lo" -le 57343 ]; then
              cp=$(((cp - 55296) * 1024 + (lo - 56320) + 65536))
              i=$((i + 6))
            fi
          fi
          # 末尾が改行だと $() に落とされるので、番人の x を付けてから外す
          ch=$(
            _utf8_from_cp "$cp"
            printf 'x'
          )
          out=$out${ch%x}
          ;;
        *) out=$out$d ;;
      esac
    else
      out=$out$c
    fi
    i=$((i + 1))
  done
  printf '%s' "$out"
}

# json_get_string <JSON全体> <キー名>
#   JSONから文字列の値を1つ取り出して stdout に出す。見つからなければ何も出さない。
#   jq でいう `.<キー> // empty` および `.tool_input.<キー> // empty` に相当する。
#
#   ★ キー名だけで探すと、書き込もうとしているファイルの中身に同じ語が入っていた場合に
#     そちらを拾ってしまう（PreToolUse:Write の入力には content が丸ごと入っている）。
#     ただしJSONでは、値の中のダブルクォートは \" にエスケープされている。
#     したがって「キーの開き引用符の直前が { か , か行頭であること」を条件にすると、
#     本物のキーと、値の中にたまたま現れた文字列を区別できる。
json_get_string() {
  local json=$1 key=$2 raw pat

  # (^|[{,]) 空白* "キー" 空白* : 空白* "エスケープを考慮した文字列"
  pat='(^|[{,])[[:space:]]*"'"$key"'"[[:space:]]*:[[:space:]]*"([^"\\]|\\.)*"'

  raw=$(printf '%s' "$json" | grep -oE "$pat" | head -n 1)
  [ -n "$raw" ] || return 0

  # 先頭の `{`/`,`・空白・キー名・コロンを落とし、値の前後のダブルクォートを外す
  raw=$(printf '%s' "$raw" | sed -E 's/^[^:]*:[[:space:]]*//')
  raw=${raw#\"}
  raw=${raw%\"}

  _json_unescape "$raw"
}
