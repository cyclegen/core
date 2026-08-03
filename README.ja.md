# CycleGen Core

**3次元記憶システム（スキル・記憶ストア）を備えた、1時間1サイクルの人間-AI協働フレームワーク。**

CycleGenは、AIとの協働を「1時間1サイクル」の反復に変えます。前半はAIが自律的に作業し、後半で人間が判断・承認する。そこで蓄積された文脈を構造化して再注入することで、**同じモデルが、あなたの仕事に対して質的に強くなっていきます**。CycleGen Core はその個人版OSS（Apache-2.0）です。

> 「モデルは借りる。文脈は自分で持つ。」

English: [README.md](./README.md)

---

## 何が入っているか

CycleGen Core は、意味検索・自己ランキングするスキル・記憶ストアを **19個のMCPツール**として提供します。

- **意味検索での記憶想起** — Layer（抽象度）／Priority（重要性）／Context（作業の種類）の3軸で順位づけし、Miller's 7±2 に従って渡しすぎを防ぎます。
- **保存・更新・ピン留め・アーカイブ** — 使われるほど重要度が上がり、参照されなければ下がります。
- **CYCLEライフサイクル** — `cycle_complete` がサイクルを記録し、昇格候補を提示します。
- **CycleGen Finish**（任意の `docx` extra）— Markdown を装飾済み `.docx` に変換します。

## 導入

### 前提: uv の導入（1行・全OS共通）

CycleGen の MCP サーバーは `uvx` で起動します。**先に uv を入れてください。**

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Python の事前導入は不要です（uv が必要な版を自動で用意し、PATH も通します）。

### Claude Code — プラグインとして使う（推奨）

CycleGen は、MCPサーバーと**CYCLEの規律（hook・スキル・承認ゲート）**をまとめて配線するプラグインを提供しています。MCPの手動設定も、このリポジトリのcloneも不要です。

Claude Code で以下を実行します:

```
/plugin marketplace add cyclegen/core
/plugin install cyclegen-core@cyclegen
```

そのあとセッションを再起動してください（MCPサーバーの読み込みに必要）。

### Codex — 同梱のコマンドで配線する

Codex にはプラグイン機構が無いため、パッケージに同梱した配線コマンドを使います。こちらも clone は不要です。

```bash
# 何が書き換わるかを先に確認する
uvx --from "cyclegen[semantic,docx]" cyclegen setup codex --dry-run

# 配線する
uvx --from "cyclegen[semantic,docx]" cyclegen setup codex
```

`~/.codex/config.toml`（MCP）・`~/.codex/hooks.json`（規律層のhook）・`~/.agents/skills/`（スキル7本）に配線します。実行後は Codex を再起動してください。

- 既存の設定は書き換えず、バックアップ（`*.cyclegen-bak`）を取ります。2回実行しても壊れません。
- 撤去は `cyclegen setup codex --remove`。**蓄積した記憶のデータは削除しません。**

- プラグインの詳細と配線される中身: [plugins/cyclegen-core/README.md](./plugins/cyclegen-core/README.md)
- Codex の配線を手で行う場合: [plugins/cyclegen-core/manifests/codex/README.md](./plugins/cyclegen-core/manifests/codex/README.md)

### MCPサーバー単体で使う

```bash
# MCPサーバーを直接起動（初回に自動でインストールされる）
uvx --from "cyclegen[semantic,docx]" cyclegen-mcp
```

pip の場合:

```bash
pip install "cyclegen[semantic,docx]"
cyclegen-mcp
```

- `semantic` — 記憶の意味検索に使う埋め込みバックエンド（**推奨**。初回起動時に小さなモデルをダウンロードします）。
- `docx` — `document_finish` / `list_finish_templates` ツールを有効化します。

> ⚠ `semantic` を付けずに導入すると `memory_search` が縮退し、ツール数も19に満たなくなります。特に理由がなければ `cyclegen[semantic,docx]` を指定してください。

## MCPクライアントの手動設定

```json
{
  "mcpServers": {
    "cyclegen": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "cyclegen[semantic,docx]", "cyclegen-mcp"]
    }
  }
}
```

## CycleGen Finish のテンプレート

`docx` extra を入れると以下のテンプレートが使えます:

executive / minimal / creative / startup / wa-modern / deep-out / deep-out-paperback / concept-book

## ドキュメント

- プラグイン（Claude Code / Codex）: [plugins/cyclegen-core/README.md](./plugins/cyclegen-core/README.md)
- 変更履歴: [CHANGELOG.md](./CHANGELOG.md)
- Webサイト・ガイド: **https://cyclegen.ai** *(公開準備中)*

## ライセンス

Apache License 2.0 — [LICENSE](./LICENSE) を参照。Copyright 2026 rashiku Corp.
