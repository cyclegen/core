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

### Use as a plugin

CycleGen ships a plugin for Claude Code and Codex that wires the MCP server and the cycle discipline (hooks) automatically. See the guide at **https://cyclegen.io**.

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

- Website & guides: **https://cyclegen.io**

## License

Apache License 2.0 — see [LICENSE](./LICENSE). Copyright 2026 rashiku Corp.
