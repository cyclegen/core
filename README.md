# CycleGen Core

**A human–AI collaboration framework with a 3-dimensional memory store (Skill & Memory Store), built on the "Deep Out" design philosophy.**

CycleGen turns AI collaboration into a repeatable one-hour cycle: the AI works autonomously, you review and decide, and the context you accumulate is structured and re-injected — so the same model gets qualitatively better at *your* work over time. CycleGen Core is the open-source (Apache-2.0) personal edition.

> "Rent the model. Own the context."

日本語版は [README.ja.md](./README.ja.md) を参照してください。

---

## What's inside

CycleGen Core ships an **MCP server** exposing **19 tools** for a semantic, self-ranking memory store:

- **Semantic memory search** — recall the right memories by meaning, ranked across 3 axes (Layer / Priority / Context), following Miller's 7±2 to avoid overload.
- **Store / update / pin / archive** — capture knowledge as you work; priority rises with use and decays without it.
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

### Prerequisite: install uv (one line, every OS)

The MCP server is launched through `uvx`, so **install uv first**:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

You do not need to install Python yourself — uv provides the right version and puts it on your PATH.

### Claude Code — use as a plugin (recommended)

CycleGen ships a plugin that wires the MCP server **and** the cycle discipline (hooks, skills, approval gate) automatically — no manual MCP configuration, no cloning this repository.

In Claude Code, run:

```
/plugin marketplace add cyclegen/core
/plugin install cyclegen-core@cyclegen
```

Then restart the session so the MCP server is picked up.

### Codex — wire it with the bundled command

Codex has no plugin mechanism, so the package ships a setup command instead. No cloning here either.

```bash
# See what would be written first
uvx --from "cyclegen[semantic,docx]" cyclegen setup codex --dry-run

# Wire it
uvx --from "cyclegen[semantic,docx]" cyclegen setup codex
```

This writes `~/.codex/config.toml` (MCP), `~/.codex/hooks.json` (the discipline hooks) and `~/.agents/skills/` (six skills). Restart Codex afterwards.

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
- Website & guides: **https://cyclegen.io** *(coming soon)*

## License

Apache License 2.0 — see [LICENSE](./LICENSE). Copyright 2026 rashiku Corp.
