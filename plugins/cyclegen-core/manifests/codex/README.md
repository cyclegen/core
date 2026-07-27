# CycleGen — Codex CLI manifest（配置テンプレート）

Codex CLI は Claude Code のプラグインバンドル（`--plugin-dir`）を読み込まない。
そのため Codex では、**共通ペイロード（SKILL.md / MCP / hook 本文）はそのまま流用**しつつ、
**ツール固有の配線（パス・設定ファイル形式）だけを Codex 流に読み替えて配置**する。

設計の確定版: `docs/design/CYCLE14.9_FR034-F4-3_Codex調査とmanifest.md`（※14.16実発火で prompts 方式は撤回・skills化へ。差分は本README末尾＋`docs/cycles/CYCLE14.15_Codex実発火検証手順書.md`）
一次ソース: developers.openai.com/codex（skills / hooks / mcp）

## 配置早見表

| 共通ペイロード（CC版から流用） | Codex での配置先 | 形式 |
|------------------------------|-----------------|------|
| 自動起動4スキル `skills/cyclegen-{cycle,memory,glossary,ops}/SKILL.md` | `.agents/skills/<name>/SKILL.md`（REPO）または `~/.agents/skills/`（USER） | **そのままコピー**（同一 agentskills.io 標準） |
| MCP 接続（`.mcp.json`） | `~/.codex/config.toml` の `[mcp_servers.cyclegen]` | `config.toml.example` を読み替え |
| hook 配線（`hooks/hooks.json` + `*.sh`） | `~/.codex/hooks.json` ＋ スクリプトを絶対パス参照 | `hooks.json.example` を読み替え |
| フロントドア `skills/cyclegen/`（明示起動） | `~/.agents/skills/cyclegen/SKILL.md`（本文コピー）＋ `agents/openai.yaml`（明示専用サイドカー） → `$cyclegen` / `/skills` | `skills-explicit/cyclegen/agents/openai.yaml` |
| init `skills/init/`（明示起動） | `~/.agents/skills/cyclegen-init/SKILL.md`（本文コピー）＋ `agents/openai.yaml` → **`$init`**（frontmatter `name: init`）/ `/skills` | `skills-explicit/cyclegen-init/agents/openai.yaml` |
| 指示ファイル本文（層2 `CLAUDE.md`） | プロジェクト直下 `AGENTS.md` | init が生成 |

> **⚠ frontdoor/init は skills 化（CYCLE14.17・旧prompts方式は0.142.5で機能せず＝finding#2）**。
> 自動起動4スキルと同じ `~/.agents/skills/` にSKILL.md本文を**無改変コピー**し、Codex固有の「明示専用」制御だけを **サイドカー `agents/openai.yaml`（`policy.allow_implicit_invocation: false`）** で付与する。
> ＝共通ペイロード（SKILL.md本文＝CC正本）＋ツール固有配線（サイドカー）の分離。CCの `disable-model-invocation:true` に相当。

## 既定の導線: `cyclegen setup codex`（CYCLE15.12.4）

**このREADMEの手順を手で追う必要は無い。** 配線は PyPI パッケージに同梱したコマンドで行う
（＝既に生きている配布チャネルの再利用・15.12.2 論点B）。clone も絶対パスの書き換えも要らない。

```bash
# 何が書き換わるかを先に見る
uvx --from "cyclegen[semantic,docx]" cyclegen setup codex --dry-run

# 配線する
uvx --from "cyclegen[semantic,docx]" cyclegen setup codex

# 撤去する（記憶ストアのデータは消さない）
uvx --from "cyclegen[semantic,docx]" cyclegen setup codex --remove
```

配線される先は下の「配置早見表」のとおり。設計上の要点:

| 事項 | 挙動 | 理由 |
|------|------|------|
| ペイロードの配置 | `~/.cyclegen/plugin/` へ**コピー**する | インストール済みパッケージ内の payload は **uvx キャッシュ配下**に解決される（15.12.3 F15 実測）。設定から直接指すと `uv cache clean` で配線が壊れる |
| 配置パス | 版数を**含めない**（フラット） | `config.toml` / `hooks.json` は絶対パスを保持するため、版数を含めると更新で配線が無効化される。Claude Code 側は逆に版数を含むのが正しい |
| MCP の `command` | `uvx` 直指定 | `uvx cyclegen setup codex` で配線した場合、`cyclegen-mcp` は setup 完了後に消える。PATH 前提を書くと**壊れた設定を書き込む**（§4-2）。恒久導入済みなら `--use-path` |
| 既存設定 | 全面書き換えしない・バックアップ（`*.cyclegen-bak`）・2回実行しても壊れない | `tomllib` は読み取り専用で、書き戻すと利用者のコメントが壊れるため、テキストブロックの追記方式 |
| 他者の hook | 触らない | 自分のエントリだけをスクリプト名で識別して入れ替える（旧来の手動配線も掃除するので**二重発火しない**） |

> `cyclegen setup claude` は**無い**。Claude Code はプラグイン機構が正規の導線であり、
> 二重の導線は「どちらが正か」を利用者に伝えられなくするため（15.12.2 判断E4）。

---

## 手で配置する場合（フォールバック）

以下は上記コマンドが行う内容の内訳であり、手作業で追う必要は通常無い。

1. **4スキルをコピー**: CC版 `plugins/cyclegen-core/skills/cyclegen-{cycle,memory,glossary,ops}/` を `~/.agents/skills/`（または リポジトリ直下 `.agents/skills/`）へコピー。本文は無改変。
   - ⚠ ここに**複製を同梱しない**（single source 維持・乖離防止）。正本は常に CC版 skills。
2. **MCP**: `config.toml.example` の `[mcp_servers.cyclegen]` を `~/.codex/config.toml` へ追記。`command` は pip 配布後は `"cyclegen-mcp"`、未整備時は venv バイナリの**絶対パス**。
3. **hook**: hook スクリプト `*.sh` を任意の固定場所（例 `~/.cyclegen/hooks/`）へ置き、`hooks.json.example` の `/ABSOLUTE/PATH/TO/hooks/` をその絶対パスに置換して `~/.codex/hooks.json` へ。
4. **フロントドア/init（skills化・CYCLE14.17）**: CC版 `skills/cyclegen/SKILL.md`・`skills/init/SKILL.md` の**本文を無改変で** `~/.agents/skills/cyclegen/SKILL.md`・`~/.agents/skills/cyclegen-init/SKILL.md` へコピーし、本manifestの `skills-explicit/<name>/agents/openai.yaml`（明示専用サイドカー）を各スキルの `agents/openai.yaml` へコピーする。
   ```bash
   # frontdoor
   mkdir -p ~/.agents/skills/cyclegen/agents
   cp <CC>/skills/cyclegen/SKILL.md              ~/.agents/skills/cyclegen/SKILL.md
   cp skills-explicit/cyclegen/agents/openai.yaml ~/.agents/skills/cyclegen/agents/openai.yaml
   # init（配置dirは cyclegen-init。ただし呼び出しは frontmatter name=init に従い $init＝dir名では呼べない。CYCLE14.21 finding#2 / CYCLE14.24 訂正）
   mkdir -p ~/.agents/skills/cyclegen-init/agents
   cp <CC>/skills/init/SKILL.md                        ~/.agents/skills/cyclegen-init/SKILL.md
   cp skills-explicit/cyclegen-init/agents/openai.yaml ~/.agents/skills/cyclegen-init/agents/openai.yaml
   cp <CC>/agents/cyclegen-persona.md                  ~/.agents/skills/cyclegen-init/agents/cyclegen-persona.md   # ★人格雛形（SKILL.mdが参照・CYCLE14.17 finding#5）
   ```
   - ⚠ **finding#5（14.17実機）**: init SKILL.md は人格雛形 `agents/cyclegen-persona.md` を参照する。CCではプラグインルート `agents/` に在るが、Codex skillは自己完結ディレクトリのため**スキルの `agents/` へ同梱**しないとフォールバック動作になる（機能はするが配布物として不完全）。
   → `$cyclegen mode review` / `$init ja` で明示起動、`/skills` にも出る。**暗黙起動はサイドカーで抑止**。
   - ⚠ SKILL.md本文はCC正本のコピー（single source）。Codex側で改変しない。
   - ⚠ **実発火で要確認**（CYCLE14.17再テスト）: ①CodexがCC固有frontmatter（`disable-model-invocation`/`argument-hint`）を許容しロードするか（finding#1でhooks.jsonは厳格）②サイドカーで暗黙起動が実際に止まるか③`$cyclegen mode review` で引数が router に届くか。
5. **層2**: `$init ja` を実行し `AGENTS.md` を生成（自然言語フォールバックも明記される）。

## ツール別 呼び出し構文 早見表（運用ドキュメント記載時の注意）

明示スキル/コマンドの呼び出し構文は**ツールごとに分岐**する。運用テスト手順書・観測記録などを書くときは、対象ツールの構文で記す（14.9「配線はツール固有・薄い層に押し出す」がドキュメント層でも成立。CYCLE14.21 finding#1/#2）。

| 操作 | Claude Code | Codex |
|------|-------------|-------|
| プロジェクト初期化 | `/cyclegen-core:init ja` | `$init ja`（frontmatter `name: init`。`$cyclegen-init` 不可） |
| フロントドア（mode切替等） | `/cyclegen-core:cyclegen mode review` | `$cyclegen mode review` |
| 自動起動4スキル | description一致で自動（明示は `/cyclegen-core:<name>`） | description一致で自動（明示は `$<name>`） |
| スラッシュ非対応ツール | — | 自然言語で「`/cyclegen` 相当の操作」を依頼（劣化運用） |

> Codexの明示呼び出し名は**配置dir名ではなく frontmatter `name`** に一致する（CYCLE14.21 finding#2）。init は `name: init` ゆえ `$init`。

## Codex 固有の注意
- **`${CLAUDE_PLUGIN_ROOT}` は Codex に無い** → MCP / hook はすべて**絶対パス or PATH バイナリ**で書く（CC のプラグイン相対は使えない）。
- 🔴 **`/prompts:` は 0.142.5 で機能しない**（CYCLE14.16実発火）。公式 deprecated＋回帰[openai/codex#15941]でスラッシュメニュー非表示・`Unrecognized command`。→ **frontdoor/init は Skills（`~/.agents/skills/` ＋ `allow_implicit_invocation:false` で明示専用）へ移行**（CYCLE14.17）。当初の「/prompts:で明示提供」設計（14.9）は不成立。
- **3層耐久フォールバック**: skills も hook も無い最悪時は、AGENTS.md（層2）の自然言語案内で操作する（`$init` が step4 で自動明記）。

## CC↔Codex 配線差分（CYCLE14.15 で公式一次ソース確定）
CC側の 14.11/14.13/14.14 実発火findings が Codex に伝播するかを公式ドキュメント（developers.openai.com/codex/hooks・/mcp）で静的に決着した。

| 論点 | Codex の挙動（公式） | 対応 |
|------|--------------------|------|
| **hooks.json 構造** | トップレベル **`{"hooks":{...}}` ラッパー必須**（CC 14.11バグBと同型） | `hooks.json.example` を修正済（ラッパー追加） |
| **hooks.json は `hooks` キーのみ許可（`_comment`不可）** | 🔴 **CYCLE14.16実発火で判明**: Codex 0.142.5 は厳格パーサで、トップレベルに `_comment` があると `unknown field _comment, expected hooks` で**hooks全体がロード失敗**。CCは `_comment` を無視するため静的には気づけなかった（実発火>静的の再実証） | `hooks.json.example` から `_comment` を削除（JSONにコメント不可・説明は本README） |
| **明示起動の機構（frontdoor/init）** | 🔴 **CYCLE14.16実発火で判明**: `~/.codex/prompts` のカスタムpromptは0.142.5で機能せず（公式deprecated＋回帰[openai/codex#15941]・`Unrecognized command`）。明示専用は **Skills＋サイドカー `agents/openai.yaml`（`policy.allow_implicit_invocation:false`）** が現行機構 | prompts撤去→skills化（CYCLE14.17）。SKILL.md本文はCC正本コピー、サイドカーで暗黙起動抑止 |
| **MCP tool matcher 名** | `mcp__<server>__<tool>`（例 `mcp__filesystem__read_file`・正規表現可）。Codex は直接MCP定義で**名前空間層なし**→`mcp__cyclegen__*` が正しい | 現行 matcher で正しい。パリティで正規表現 `mcp__.*cyclegen__` を採用 |
| **additionalContext 到達** | ~~UserPromptSubmit / PreToolUse / PostToolUse 全てでモデル注入~~ ⚠**14.16で一部覆る**: UserPromptSubmitは**JSON必須**（下行）。PreToolUse/PostToolUseのadditionalContext到達は**未検証**（CV5実行③未実施） | primer JSON化後にPre/Post到達を再検証（CYCLE14.17） |
| **ブロック機構** | `exit 2` + stderr でブロック可（CCと同一） | ✅**CV6実機PASS**: `check-cycle-complete.sh` が Codex でも exit 2 でブロック（stdin JSONは `.tool_input.<arg>` でキー互換）。ただしファイル存在ガードは捏造突破可＝真ゲートはHITL（finding#4） |
| **UserPromptSubmit plain stdout** | ~~「Plain text on stdout is added as extra developer context」＝CCと同一~~ 🔴**14.16実発火で覆る**: Codexは **JSON出力必須**、plain stdoutは `hook returned invalid user prompt submit JSON output` で拒否 | `remind-primer.sh`/`remind-cycle-memory.sh` を **JSON additionalContext化**（CYCLE14.17・CC単一ソース両立は要一次確認） |

→ **静的§1は実機で3系統破綻**（CYCLE14.16）。実機CV1-CV6の結果・4findingは同梱の検証手順書 `docs/cycles/CYCLE14.15_Codex実発火検証手順書.md`（§4総合判定）を参照。
