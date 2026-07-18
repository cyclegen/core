---
name: cyclegen-ops
description: "CycleGenのツール固有操作。コンテキストリセットの実行と復元手順、プロジェクト状態セクションの更新、CycleGen Finish（md→docx変換）の呼び出しを扱う。起動: 「リセットしたい」「コンテキストをクリア」「状態を更新」「docx化」「Finishして」。※いつリセットすべきかの判断・PDCA運営・完了処理はcyclegen-cycle。"
---

# CycleGen Operations（ツール固有操作）

> ツール依存のコマンド名・パスはここに隔離する（ツールごとに差し替え）。
> プロトコル本体は Skill `cyclegen-cycle`、記憶運用は `cyclegen-memory` を参照。

## コンテキストリセットの実行
リセットのコマンドはツール依存: Claude Code は **`/clear`**、他ツールは各相当（例: Codex は新規セッション開始）。

### リセット後の復元手順
1. リセットを実行 → 会話履歴がゼロに
2. 常時規律は hook（UserPromptSubmit毎ターン）＋薄い指示ファイル本文で再注入される
3. 利用者が新しいCYCLEの意図を伝える
4. `memory_search` で関連記憶を再注入（MCP接続時）

### リセットしても消えないもの
- スキル・記憶ストア（MCPサーバーに保存済み）
- プラグインの Skill / hooks（再ロードされる）

### リセットで消えるもの
- 会話履歴（これが目的）

## プロジェクト状態の更新
CYCLE完了時に利用者プロジェクトの状態セクション（薄いCLAUDE.md/AGENTS.md末尾）を更新:
- 現在のCYCLE / 最終更新日 / 今何が揃っているか / 次のアクション / 直近の活動（最新3-5件）

※ PostToolUse hook（`remind-profile-update.sh`）でリマインドされるが、hook無効ツールでも必ず更新する。

## CycleGen Finish（md→docx変換・条件付き有効化）
CycleGen Core同梱の変換スクリプトがある場合のみ:
```
python3 <CYCLEGEN_CORE>/scripts/finish/cyclegen_finish.py <input.md> --template <template>
```
テンプレート: executive, minimal, creative, modern, wa-modern, gold, paperback, green

> **思考モードのプロンプトサジェスト（旧O2）はここに置かない。** prompts skillは廃止（CYCLE14.5 F3-a）。サジェスト機能は明示コマンド `/cyclegen mode <x>`（F4で実装）へ移管する。正本のサジェスト表は `~/.claude/CLAUDE.md` プロンプトサジェスト節＋`docs/design/CYCLE12.9.2_cyclegen-ops改訂案.md` に残る。

---
*（F3-b 完全移植済: ops改訂案のO1（/clearリセット実行・復元手順）/O3（プロジェクト状態更新）/O4（Finish）を吸い上げ。O2サジェスト表はprompts廃止により除外（→F4 /cyclegen mode）。description自動起動はF3-aでチューニング済。出典: docs/design/CYCLE12.9.2_cyclegen-ops改訂案.md ＋ FR034-F1 §4 O1/O3/O4。Core依存パスは /cyclegen-core:init が解決予定）*
