# Changelog

All notable changes to CycleGen Core are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-18

Initial public release of **CycleGen Core** (Apache-2.0).

### Added
- MCP server (`cyclegen-mcp`) exposing **19 tools** for a 3-dimensional memory store
  (Skill & Memory Store): semantic search, store/update/pin/archive/boost/dismiss,
  mark-used, diagnostics, reclassify/reembed/recalculate, and the cycle lifecycle
  (`memory_status`, `cycle_complete`).
- Semantic memory search with 3-axis ranking (Layer / Priority / Context) under
  Miller's 7±2, backed by FastEmbed (`semantic` extra).
- **CycleGen Finish** (`docx` extra): `document_finish` / `list_finish_templates`
  for Markdown → styled `.docx` conversion.
- Plugin distribution for Claude Code and Codex, with a bootstrap launcher that
  resolves `uv`/`uvx` automatically.

[0.1.0]: https://github.com/rashiku/cyclegen/releases/tag/v0.1.0
