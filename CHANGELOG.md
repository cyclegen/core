# Changelog

All notable changes to CycleGen Core are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-08-16

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
- A discipline hook that notices when the **memory store is not answering** and says so
  in plain words, instead of letting the session carry on as if memories were being
  written. Silence is the failure mode this is aimed at.

### Changed
- The MCP server is launched via `uvx` on every surface, so **uv is now a prerequisite**
  rather than something the launcher installs on demand. Both READMEs document the
  one-line install for macOS/Linux and Windows.
- The MCP launch configuration now **pins the package version** instead of resolving to
  whatever is latest on PyPI. Without the pin, a plugin built against one release could
  silently start a different one. The pin is applied consistently to the Claude Code
  plugin (`.mcp.json`), the config written by `cyclegen setup codex`, and the manual
  Codex template.

- **Memory store diagnostics** (`memory_diagnostics`) now reports the health of the
  store itself — coverage of returned memories, feedback rates, embedding provenance —
  and stays silent about any figure it does not have enough data to judge.
- **Idle-recall review**: `cycle_complete` lists memories that keep coming back in
  searches but have never been marked as used, and offers three choices (dismiss,
  split, leave). It only presents; nothing is rewritten. Nothing is shown at all when
  the store is too young for the threshold to mean anything.
- Dismissing a memory now warns **before** it disappears ("two more dismisses and this
  memory will stop appearing in searches") instead of only after.
- Every memory now records **which embedding model produced its vector**, so a model or
  library change can be detected instead of having to be guessed.
- `cycle_complete` accepts `used_memory_ids` — the memories actually used during the
  cycle — so "returned by search" is no longer mistaken for "actually used".
- `dismiss` / `boost` / `archive` record **where the operation came from** (a user's
  judgement, a clean-up, or a verification run), so maintenance work no longer inflates
  the quality figures. Set `CYCLEGEN_EVENT_SOURCE=verification` to mark a whole session.

### Changed
- The upper bound of `fastembed` is now pinned (`>=0.4,<0.9`). Pooling behaviour has
  changed between minor releases before, and when it does, stored vectors and query
  vectors end up in different spaces — no error, just quietly worse search results.
- Updating a memory's text now regenerates its embedding **and** its content hash, so a
  memory is never searched for by wording it no longer has.
- The discipline hooks no longer require `jq`. They now run on a plain bash that ships
  with the system, which is what Windows and older macOS actually have.
- `cyclegen --help` now leads with the desktop apps, which is how most people actually
  start, and treats the terminal as the secondary route.
- The Codex configuration written by `cyclegen setup codex` (and the manual template)
  now sets `startup_timeout_sec = 60`. The default is 10 seconds, which is not enough
  room for the first-run import on a cold machine.
- The wait on the very first search (the embedding model is downloaded once, about
  240 MB) is now announced **where the wait happens**, not only during onboarding.
- The cycle skill no longer describes git as optional. Every supported surface expects
  a repository, so the instructions say so without conditions.
- Wording in the Core distribution no longer says "CycleGen Enterprise", and the plugin
  README now states the numbers it actually ships: 19 tools, 5 auto-starting skills.

### Fixed
- The MCP server could **hang on start-up** instead of answering the first request:
  `fastembed` was imported from two threads at once and deadlocked. The import is now
  done once, on a single thread, before the server starts serving. The first-run model
  download is still deferred, so start-up stays short on a warm machine.
- `cycle_complete` no longer names tools that Core does not ship. Descriptions are
  handed to the assistant as-is, so naming an absent tool made it try to call one.
- `memory_pin` no longer claims to stop time-based decay. Priority does not decay with
  time in the first place — it moves with use and judgement.
- Graceful degradation when the Enterprise layer is absent (`cyclegen.org` import no
  longer aborts `memory_search` / `memory_status`).
- Repeated `dismiss` now lands exactly on the lower bound instead of a floating-point
  residue just above it, so threshold-based behaviour fires when it should.
- **The Windows install instructions did not work on a clean machine.** The uv one-liner
  published upstream (`irm ... | iex`) is refused by the default execution policy, and the
  usual workaround loosens an OS security setting that managed machines often lock down.
  Windows now installs uv with `winget`, which changes no settings and additionally
  resolves the VC++ redistributable.
- **The plugin install instructions started with a command the desktop app rejects.**
  `/plugin` is not accepted in the Code tab, which is the surface most people use. The
  screen-based path is now documented first, with the terminal commands kept for `claude`
  users, and the required restart is spelled out — nothing appears until you restart, even
  though the install reports success.
- Documentation no longer describes the discipline hooks as something that *blocks*
  premature completion. On Codex, tool calls made from inside `exec` (code mode) bypass
  `PreToolUse` hooks entirely, so the guard is a speed-bump rather than a mechanism —
  which is what the hook itself has always said. The cycle discipline still holds; it is
  carried by the protocol and the assistant, not by an enforcement layer.
- Search is no longer described as *ranking* across all three axes. Memories are filed on
  three axes, and priority moves with use, but a search without an explicit context does
  not currently weight by Layer or Context.

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
