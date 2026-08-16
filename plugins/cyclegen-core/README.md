# cyclegen-core プラグイン

CycleGenプロトコル（1時間1サイクルの人間-AI協働・PDCA承認ゲート・スキル記憶ストア）を
プラグインとして配布する。検証ゲートは **「CLAUDE.md 手編集ゼロでサイクルがターンをまたいで回る」**。

- 暫定ターゲット: **Claude Code（フル対応）＋ Codex CLI**。残り3ツール（Cursor/Antigravity/Cowork）は層1+2で劣化許容。
- 設計出典: `docs/design/CYCLE14_FR034-F1_配布棚卸し設計.md`（F1）／`CYCLE14.7_FR034-F4_配布層設計.md`（F4）／`CYCLE14.9_FR034-F4-3_Codex調査とmanifest.md`（Codex読み替え）

## 構成
```
cyclegen-core/
├── .claude-plugin/plugin.json   マニフェスト
├── .mcp.json                    ★ローカル cyclegen-mcp(stdio) 直結（設計原則13「1入口方式」）
├── hooks/
│   ├── hooks.json               配線（UserPromptSubmit / Pre・PostToolUse）
│   └── *.sh                     6本（remind-primer / remind-cycle-memory / check-cycle-complete /
│                                remind-context-judgment / remind-knowledge-proposal / remind-profile-update）
├── skills/
│   ├── cyclegen/SKILL.md         ★明示フロントドア（disable-model-invocation・start/finish/memory/mode<x>）
│   ├── init/SKILL.md             ★明示init（標準ディレクトリ＋薄い層2 CLAUDE.md を生成）
│   ├── cyclegen-cycle/SKILL.md   〔自動起動〕PDCA・承認ゲート・完了処理・git・標準ディレクトリ構造
│   ├── cyclegen-memory/SKILL.md  〔自動起動〕Layer/Context判定・よい記憶の書き方・トリガー語
│   ├── cyclegen-glossary/SKILL.md 〔自動起動〕用語定義・思考の枠組み
│   ├── cyclegen-ops/SKILL.md     〔自動起動〕ツール固有操作（/clear・状態更新・Finish）
│   └── onboarding/SKILL.md       〔自動起動〕サイクル0＝初回3サイクルの伴走（init直後の初日体験）
├── agents/
│   └── cyclegen-persona.md       人格テンプレ雛形（デフォルト非起動・{{AI_NAME}}変数化）
└── manifests/
    └── codex/                    ★Codex CLI 用の配線読み替えテンプレート（F4-3）
```

スキルは**二重起動**: 5スキル（cycle/memory/glossary/ops/onboarding）は description で**自動起動**、
フロントドア/init は `disable-model-invocation:true` の**明示起動専用**。
※ `onboarding` は初回限定の助走（サイクル0）。既存利用者の環境で自動起動しても、本文冒頭のガードで抜ける。

## 前提: uv の導入（★OSごとに1行）

CycleGen の MCP サーバーは `uvx` で起動する。**先に uv を入れておくこと。**

```
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```
# Windows（PowerShell）
winget install --id=astral-sh.uv -e
```

Python の事前導入は不要（uv が必要な版を自動で用意する）。PATH も自動で通る
（Windows は **新しいウィンドウを開いてから** `uv --version` を確認する）。

> ### ★ Windows で `irm ... | iex` を使わない理由（CYCLE17.6.3／F-12・実測）
> uv の公式サイトが案内する `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` は、
> **まっさらな Windows 11 の既定の実行ポリシー（Restricted）で拒否される**——
> `Error: PowerShell requires an execution policy in [Unrestricted, RemoteSigned, Bypass] to run uv.`
>
> 回避には `Set-ExecutionPolicy` で **OS のセキュリティ設定をゆるめる**必要があり、
> **会社支給PC（ドメイン参加）ではグループポリシーで固定されていて、そもそも通らないことがある。**
> `winget` なら初回に `Y` を1回押すだけで、**設定は何も変わらない。**
>
> ★ 副次的な利点: `winget` は `Microsoft.VCRedist.2015+.x64` を**依存として解決する**。
> `irm | iex` 経路にはこの解決が無く、VCRedist 不在の環境では uv の実行時に落ちうる。**winget のほうが頑健。**

> なぜ uv が前提なのか: `.mcp.json` は起動コマンドを **1つしか書けず、OS 分岐の機構が無い**
> （公式スキーマに OS 変種が存在しない）。したがって「Windows でも動く1つのコマンド」を
> 選ぶしかなく、`uvx` を直接指す形にした（CYCLE15.12.2 判断D-1）。
> uv が無い場合は規律層の hook が毎ターン導入手順を案内する。

## 導入手順（Claude Code）

### 1. インストール — ★**デスクトップアプリは画面操作で入れる**

> ★ **Claude Code デスクトップアプリの Code タブでは `/plugin` が使えない**（CYCLE17.6.3／F-14・実測）。
> 「/plugin はここでは認識されないコマンドです」と表示される。**画面から入れる:**

```
設定 → ディレクトリ → プラグイン → 右上の「+」
 → 「マーケットプレイスを追加」
 → 「リポジトリから追加」に  cyclegen/core  を入力
 → 一覧から cyclegen-core をインストール
```

★ **インストール後、アプリを再起動する**——**成功メッセージは出るが、再起動するまで何も現れない**（F-15）。

**ターミナルの `claude` を使っている場合**は、次のコマンドでも入る:

```
/plugin marketplace add cyclegen/core
/plugin install cyclegen-core@cyclegen
```

### 2. プロジェクト初期化（標準ディレクトリ＋薄い層2 CLAUDE.md を生成）
```
/cyclegen-core:init
```

### 3. 初CYCLE開始
```
/cyclegen-core:cyclegen start      # 省略形 /cyclegen start が効けばそれでも可
```

（開発反復時は `claude --plugin-dir ./plugins/cyclegen-core` ＋ `/reload-plugins`）

## 導入手順（Codex）
```
# 配線コマンドが Codex 側の config.toml / hooks.json / skills をまとめて設定する
uvx --from "cyclegen[semantic,docx]" cyclegen setup codex --dry-run   # 何を書くか確認
uvx --from "cyclegen[semantic,docx]" cyclegen setup codex             # 実行
uvx --from "cyclegen[semantic,docx]" cyclegen setup codex --remove    # 撤去（記憶データは残る）
```
実行後は Codex を再起動する。既存の `config.toml` / `hooks.json` は全面書き換えせず、
バックアップ（`*.cyclegen-bak`）を取り、2回実行しても壊れない。
配線の中身と設計の要点は `manifests/codex/README.md`。
- 思考モード明示指定: `/cyclegen-core:cyclegen mode review`（review/analyze/decide/create… 12種）
- 名前空間はプラグイン名 `cyclegen-core`。曖昧性が無ければプレフィックス省略可（`/cyclegen ...`）。

## 更新のしかた（★**新しい版が出たとき**）

### Claude Code

**デスクトップアプリ**は導入と同じく画面から:

```
設定 → ディレクトリ → プラグイン → cyclegen-core → 更新
```

**ターミナルの `claude`** なら `/plugin update cyclegen-core`。

どちらの経路でも `.mcp.json` ごと新しくなるので、**MCPサーバの版も一緒に上がる**。更新後はアプリを再起動する。

### Codex — ★**`--force` を必ず付ける**

```bash
uvx --from "cyclegen[semantic,docx]" cyclegen setup codex --force
```

> ### ★★ `--force` を付けないと、半分だけ新しくなる
> `setup codex` は**配布物（スキル・hook）は版を見て更新するが、`~/.codex/config.toml` は版を見ない**——
> `[mcp_servers.cyclegen]` が既にあれば「変更しません」と言って飛ばす。
>
> その結果:
>
> | | |
> |---|---|
> | ✔ スキル・hook | **新しい版になる** |
> | ・ `config.toml` の版ピン | ★**古いまま残る** |
>
> ★ **表示は成功に見え（`✔` が並ぶ）、警告も出ない。** けれど動いている MCP サーバは古い版のままなので、
> **新しいスキルが、新しいツールを、持っていないサーバに呼びにいく**ことになる。
>
> ★ `--force` を付ければ `config.toml` も置き換わる（**既存の設定はバックアップ `*.cyclegen-bak` が残る**）。

★ 更新できたかの確かめかた: `~/.codex/config.toml` の `args` に入っている版が、入れたばかりの版と一致していること。

```bash
grep 'cyclegen\[semantic' ~/.codex/config.toml
```

## 各ツール対応状況
| 層 | 機構 | Claude Code | Codex CLI | 他3ツール |
|----|------|------------|-----------|----------|
| MCP（濠の本体） | スキル記憶ストア19ツール | `.mcp.json`（stdio直結） | `~/.codex/config.toml`（TOML読み替え） | MCP対応なら可 |
| Skill本文 | 5自動起動スキル（agentskills.io標準） | `skills/<name>/` | `.agents/skills/<name>/`（同一標準・コピー） | 標準採用ツールは流用可 |
| hook | 常時規律の毎ターン強化 | `hooks/hooks.json` | `~/.codex/hooks.json`（同名イベント） | 劣化許容 |
| 明示コマンド | フロントドア/init | `/cyclegen-core:*`（skills＋disable-model-invocation） | `/prompts:cyclegen(-init)`（明示・deprecated機構） | 自然言語劣化 |
| 指示ファイル（層2） | 常時規律フォールバック | `CLAUDE.md` | `AGENTS.md` | `AGENTS.md`/相当 |

Codex への配置は `manifests/codex/README.md` 参照（共通ペイロードは流用・配線のみ読み替え）。
劣化の優先順位は「**MCP ＞ 指示ファイル本文 ＞ hook**」の3層耐久モデル（hook無効ツールでもグレースフルに劣化）。

## 既知の保留（解決済みは消し込み）
**解決済み**: description自動起動調整（F3-a/CYCLE14.5）／本文移植（F3-b/CYCLE14.6）／
UserPromptSubmit薄いプライマー `remind-primer.sh`（CYCLE14.3）／フロントドア・init（F4実装①/CYCLE14.8）／
Codex manifest・`.agents/skills/`標準パス確定（F4-3/CYCLE14.9）／3層フォールバック配線（Codex manifest＋AGENTS.md自然言語/CYCLE14.9）。

**解決済み（配布導線）**: `.mcp.json` の command（リポ相対 → bootstrapラッパー → **`uvx` 直指定**／
CYCLE15.3・15.12.2 判断D-1）／marketplace.json 新設と公開チャネル経由の導入実証（CYCLE15.12.1）／
規律層 payload の wheel 同梱（`cyclegen/_payload/`・force-include／CYCLE15.12.3 判断E1=A-1）／
**Codex の入手導線 `cyclegen setup codex`（M-0b・CYCLE15.12.4 実装・隔離環境で通し受入32項目PASS）**。

**残**:
- ~~**Windows での規律層 hook**（`#!/bin/bash` 6本・`jq` 依存）は未検証~~ → ★**解決済み（CYCLE20.4＋17.6.3 実機）**: **`jq` 依存を除去**（`hooks/_json.sh` を新設し、bash組み込みと `sed`/`grep` だけで JSON を扱う）。**Windows 実機（WIN-01）で hook 全数を実行し、`command not found` ゼロ**。Git Bash 経由で `.sh` が発火することも確認ずみ。**MCP 起動側は D-1 で解決済み**（この2つは別の問題）。
- ★**規律層 hook の到達には、いま1つ例外がある（CYCLE17.6.6／F-33）**: **Codex Desktop の code mode**（`exec` の中から `ALL_TOOLS` 経由でMCPツールを呼ぶ経路）では、**PreToolUse hook が掛からない**。**hook は載っているが迂回される。** → **`manifests/codex/README.md` の「ブロック機構」の行を参照。根治はサーバ側検証（MS2）。**
- **本体リファインとの同期方式**（軸A）= (c)ハイブリッド確定（MCPは直結で自動最新／Skill・hook本文はスナップショット＋re-sync）。**本体が別リポ化した時点で再評価**（現在は同一リポで乖離リスク小）。
- **`permissions.allow`**: 利用者固有のため配布除外（F1 H7）。
- **実発火検証**（スラッシュ起動・`disable-model-invocation`・init生成・Codex hooks.jsonネスト）はJAY環境の対話起動（`claude --plugin-dir` / Codex `/hooks`）に委譲。
