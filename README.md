# CycleGen Core

**A human–AI collaboration framework with a 3-dimensional memory store (Skill & Memory Store), built on the "Deep Out" design philosophy.**

CycleGen turns AI collaboration into a repeatable one-hour cycle: the AI works autonomously, you review and decide, and the context you accumulate is structured and re-injected — so the same model gets qualitatively better at *your* work over time. CycleGen Core is the open-source (Apache-2.0) personal edition.

> "Rent the model. Own the context."

日本語版は [README.ja.md](./README.ja.md) を参照してください。

---

## What's inside

CycleGen Core ships an **MCP server** exposing **19 tools** for a semantic, self-ranking memory store:

- **Semantic memory search** — recall the right memories by meaning, following Miller's 7±2 so a search returns a set you can actually hold in your head.
- **Store / update / pin / archive** — capture knowledge as you work. Every memory is filed on 3 axes (Layer / Priority / Context), and **priority rises with use** — memories you rely on surface more readily, and ones that miss can be pushed down explicitly.
- **Cycle lifecycle** — `cycle_complete` records a work cycle and surfaces promotion candidates.
- **CycleGen Finish** (optional `docx` extra) — convert Markdown to styled `.docx`.

## Install

CycleGen Core runs as an MCP server. The recommended way is via [`uv`](https://docs.astral.sh/uv/):

```bash
# Run the MCP server directly (installs on first use)
uvx --from "cyclegen[semantic,docx]" cyclegen-mcp
```

Or with pip:

```bash
pip install "cyclegen[semantic,docx]"
cyclegen-mcp
```

- `semantic` — embeddings backend for memory search (recommended; first run downloads a small model).
- `docx` — enables the `document_finish` / `list_finish_templates` tools.

### Prerequisite: install uv (one line per OS)

The MCP server is launched through `uvx`, so **install uv first**:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell)
winget install --id=astral-sh.uv -e
```

You do not need to install Python yourself — uv provides the right version and puts it on your PATH.
On Windows, **open a new terminal window** before checking `uv --version`.

> **Why not `irm ... | iex` on Windows?**
> The one-liner published on uv's own site is rejected by the default execution policy
> (`Restricted`) on a clean Windows 11 install:
> `Error: PowerShell requires an execution policy in [Unrestricted, RemoteSigned, Bypass] to run uv.`
> Working around it means loosening an OS security setting with `Set-ExecutionPolicy` — and on a
> company-managed machine that is often locked by group policy, so it may not be possible at all.
> `winget` asks you to press `Y` once and changes no settings. It also resolves
> `Microsoft.VCRedist.2015+.x64` as a dependency, which the `irm | iex` path does not.

### Claude Code — use as a plugin (recommended)

CycleGen ships a plugin that wires the MCP server **and** the cycle discipline (hooks, skills, the approval-gate protocol) automatically — no manual MCP configuration, no cloning this repository.

**In the desktop app, install it from the screen** — the Code tab does not accept `/plugin`:

```
Settings → Directory → Plugins → "+" (top right)
  → Add marketplace
  → Add from repository:  cyclegen/core
  → install cyclegen-core from the list
```

**If you use the `claude` terminal**, these commands work instead:

```
/plugin marketplace add cyclegen/core
/plugin install cyclegen-core@cyclegen
```

Then restart the app so the MCP server is picked up — **nothing appears until you do**, even though
the install reports success.

### Codex — wire it with the bundled command

Codex has no plugin mechanism, so the package ships a setup command instead. No cloning here either.

```bash
# See what would be written first
uvx --from "cyclegen[semantic,docx]" cyclegen setup codex --dry-run

# Wire it
uvx --from "cyclegen[semantic,docx]" cyclegen setup codex
```

This writes `~/.codex/config.toml` (MCP), `~/.codex/hooks.json` (the discipline hooks) and `~/.agents/skills/` (seven skills). Restart Codex afterwards.

- Existing settings are never rewritten in place; backups (`*.cyclegen-bak`) are kept, and running it twice is safe.
- To undo: `cyclegen setup codex --remove`. **Your stored memories are left untouched.**

- Plugin details and what gets wired: [plugins/cyclegen-core/README.md](./plugins/cyclegen-core/README.md)
- Wiring Codex by hand: [plugins/cyclegen-core/manifests/codex/README.md](./plugins/cyclegen-core/manifests/codex/README.md)

## MCP client configuration (manual)

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

## Documentation

- Plugin (Claude Code / Codex): [plugins/cyclegen-core/README.md](./plugins/cyclegen-core/README.md)
- Changelog: [CHANGELOG.md](./CHANGELOG.md)
- Website & guides: **https://cyclegen.ai** *(coming soon)*

## License

Apache License 2.0 — see [LICENSE](./LICENSE). Copyright 2026 rashiku Corp.
