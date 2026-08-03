# Changelog

All notable changes to CycleGen Core are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `cyclegen setup codex` — wires Codex (`~/.codex/config.toml`, `~/.codex/hooks.json`,
  `~/.agents/skills/`) straight from the installed package, so Codex users no longer
  need to clone this repository or edit absolute paths. Supports `--dry-run`,
  `--force`, `--remove` and `--use-path`. Existing settings are preserved
  (backups are written, running it twice is a no-op, other tools' hooks are left alone).
- The plugin payload (hooks, skills, manifests) is now shipped inside the wheel at
  `cyclegen/_payload/` and copied to `~/.cyclegen/plugin/` on setup.
- **Guided first cycle** (`onboarding` skill) — a three-step walkthrough that runs on
  first use, so the memory store is exercised (stored, then recalled after a context
  reset) before anything else is asked of you. Available on both Claude Code and Codex.

### Changed
- The MCP server is launched via `uvx` on every surface, so **uv is now a prerequisite**
  rather than something the launcher installs on demand. Both READMEs document the
  one-line install for macOS/Linux and Windows.
- The MCP launch configuration now **pins the package version** instead of resolving to
  whatever is latest on PyPI. Without the pin, a plugin built against one release could
  silently start a different one. The pin is applied consistently to the Claude Code
  plugin (`.mcp.json`), the config written by `cyclegen setup codex`, and the manual
  Codex template.

### Fixed
- Graceful degradation when the Enterprise layer is absent (`cyclegen.org` import no
  longer aborts `memory_search` / `memory_status`).

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

[0.1.0]: https://github.com/cyclegen/core/releases/tag/v0.1.0
