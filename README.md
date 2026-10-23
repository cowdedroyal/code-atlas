# Code Atlas

A zoomable map of a code repository. Open a large project and see, in this order:

| Zoom | You see | Lines mean |
|---|---|---|
| **L0 Regions** | the logical areas of the system, coloured by role | how strongly regions depend on each other |
| **L1 Files** | every file as a dot inside its region | imports and aggregated calls between files |
| **L2 Symbols** | classes (with their methods), functions, constants, and module scope for top-level execution | calls, inheritance, instantiation, references |
| **L3 Detail** | click anything: signature, doc, callers and callees, the code itself | |

The map has two layers with different owners:

- **Structure** — files, symbols, imports, calls — is scanned deterministically with tree-sitter. One `codeatlas scan`
  makes it complete and current; no LLM involved.
- **Meaning** — which region a file belongs to, what a region is responsible for, the logical links between
  regions, guided tours — is written by a coding agent (Claude Code, Codex, …) following the bundled skill, and stored
  as reviewable text in your repository's `.codemap/`. Until an agent has run, regions are drafted from the
  directory tree, so the map is never empty.

## Quick start

```bash
uvx repo-code-atlas serve -r path/to/your-repo     # scan + build + serve http://127.0.0.1:8765
```

(Until the first PyPI release, run it from a clone: `uv sync && (cd frontend && npm install && npm run build)`,
then `uv run codeatlas serve -r …`.)

The PyPI distribution is **repo-code-atlas**; the command remains **codeatlas** and the Python module
remains **code_atlas**. The shorter distribution name was rejected by PyPI's similar-name protection.

The demo map in this repository's Pages site is Code Atlas mapped by itself: its `.codemap/` holds the regions,
notes, relations and two tours an agent wrote with the bundled skill.

Other commands (all print JSON):

```bash
codeatlas scan            # incremental: only changed files are re-parsed
codeatlas scan --scip index.scip   # use a SCIP index (scip-python, scip-typescript, scip-java, …) for precise references
codeatlas status          # size, language coverage, semantic coverage
codeatlas show <id|name>  # a file or symbol with its neighbours (add --code for source)
codeatlas build -o site   # static site for GitHub Pages or any static host (--no-source to omit code)
```

`build` refreshes the scan and writes a consistent snapshot of symbol ranges and source. `serve` scans
and builds once, then serves the printed local URL until stopped; it does not open a browser. It watches
semantic files by default and rebuilds after apply; refresh the page to see the result. Use `--no-watch`
to disable watching. Source edits still require a scan/build. `serve --no-scan` uses the existing index deliberately;
keep it current first. Source changes during a build are detected by hash checks.

Grammars are downloaded on first use by `tree-sitter-language-pack` and cached.

## Exploring the map

**Lock level** keeps the current L0, L1 or L2 content while zooming and panning; labels grow with zoom.
The theme button switches light/dark mode and remembers the choice. L1 overview edges share region
hubs to reduce clutter; selected dependency lines remain direct.
Click L0/L1/L2 to choose and lock a level directly, or unlock to resume automatic level changes.
At locked L0, click a region to keep its incoming and outgoing relationships visible; empty map space
clears the focus. At unlocked L0, clicking a region zooms into it.

Each level shows its own lines. **Imports** connect files at L1; **calls** describe invocations found in
source, shown between files at L1 and between symbols at L2. Incoming and outgoing lists are the two
directions of the same edge. They are static code relationships, not runtime execution traces.
At L0, gray structural lines aggregate dependency weights; they currently have no text label.
Darker semantic lines carry agent-written labels, which may be hidden to avoid overlapping text.
Semantic lines appear on the map only when both endpoints match the current level; mixed-level
relations remain available in the detail panel and tours. Symbol lines also depend on endpoint visibility.

**Module scope** at L2 represents execution outside named declarations, including anonymous test
callbacks. File-owned calls and references connect through this derived card. A file can have both a
module-scope card and named functions/classes without duplicating their execution. Its code view folds
declaration bodies and keeps signatures, decorators and original line numbers; expand a fold or choose
**View complete file** for context. Pure imports and document links remain at L1. These generated cards
are absent from the scanner's symbol index, so agent changesets and CLI queries should target their file
or a real indexed symbol instead.

Drag the detail panel's left edge to resize it. Ctrl + wheel and the − / + buttons change text size.
Symbol source preserves exact inclusive ranges and line numbers; **Show more code** expands excerpts
beyond the initial 400 lines. Syntax highlighting supports all 193 bundled highlight.js grammars,
scanner language aliases and Vue/Svelte scripts. Other scanner languages use tree-sitter highlights
queries or AST token categories. Browser parsing runs in cached, on-demand Web Workers; fallback token
data is fetched only with details. Very large prefixes (over 100,000 characters), missing grammars or
unavailable workers may leave code as plain text.

Descriptions, notes, relation rationales and tour text render Markdown. Files and Markdown sections
offer **Preview / Source** modes: headings, lists, tables, quotes, links and highlighted code blocks in
preview; exact ranges and line numbers in source. Repository links in file/symbol text open mapped files.
The renderer and parser load on demand, with cached background parsing and batched long-document
display. Raw HTML is shown literally and images as links; math and Mermaid are not rendered.

## Letting an agent write the meaning layer

```bash
codeatlas skill install                     # → ~/.claude/skills/codeatlas
codeatlas skill install --target agents     # → <repo>/.agents/skills/codeatlas
codeatlas skill install --target <directory> # → <directory>/codeatlas
```

Then ask your agent to "map this repo". The skill drives these commands:

```bash
codeatlas outline                  # the repo at a glance: directories, entry points, important files
codeatlas context <dir|region:id>  # members, docstrings, main symbols, dependencies in and out
codeatlas agenda                   # what the semantic layer needs next (regionize, assign, link, refresh, annotate)
codeatlas validate -f cs.json      # dry-run a changeset; apply -f cs.json writes it into .codemap/
codeatlas path <a> <b>             # the call chain between two symbols (used when writing tours)
codeatlas log / revert <run>
```

Regions are named by the capability the system would lose without them and carry one of eight fixed roles
(interface, orchestration, core, state, integration, presentation, foundation, tooling). Relations need
evidence in the code; when that code changes, the relation (or note) is flagged stale and shows up in `agenda`.

Write semantic changes through `validate`/`apply`; generated structure and module-scope cards are not
agent-editable targets. Markdown is supported in descriptive text, with the existing character limits;
keep names and canvas relation labels plain. The bundled [agent skill](src/code_atlas/skill/SKILL.md) includes
[viewer guidance](src/code_atlas/skill/references/viewer.md) for these distinctions. Installed skills are
copies: rerun `skill install` for the same target after upgrading to refresh them.

When content is still correct after code changes, use an explicit `confirm` operation to refresh its
anchors without changing text. Ordinary identical upserts remain idempotent; confirm validates that
evidence and tour paths still exist. See the skill changeset reference for examples.
The “code changed since written” badge is this review feature, not a runtime failure. File anchors
normalize line endings so Windows CRLF and Linux LF checkouts do not cause false review warnings;
the build still checks the original byte hash when matching embedded source to the scanned index.

**Tours** walk a reader through one flow — "what happens when a request comes in" — stop by stop, played on
the map with the real call chain drawn between stops (← / → to step). Every stop must be reachable from an
earlier one through the scanned calls, or through a recorded relation for links static analysis cannot see
(HTTP, WebSocket, subprocesses, files on disk), so a tour can't describe a path that isn't in the code.

## Language support

| Tier | Languages | What you get |
|---|---|---|
| 1 | Python, TypeScript, TSX, JavaScript, Vue/Svelte `<script>` | nested symbols, precise import bindings (relative, packages, `tsconfig` paths, re-exports, `__init__` re-exports, `sys.path` additions), `self`/`this`/`super` calls through base classes |
| 1 | Go, Java, Rust, C, C++, C# | the same, through one spec-driven walker: `go.mod` packages, Java packages and `a.b.*`, C# namespaces, Rust `crate`/`super`/`mod` paths, C/C++ `#include` with calls redirected from header declarations to their definitions; receiver types inferred from declarations, `new`, `T::new()`, `NewT()` and fields, so `svc.place()` reaches `OrderService.place` |
| 2 | every other grammar in tree-sitter-language-pack (Kotlin, Ruby, PHP, Swift, Scala, …) | symbols from the grammar's structure, calls from its `tags.scm`, imports by path matching, same-package resolution |
| outline | Markdown, YAML, TOML, JSON, SQL, Dockerfile, Makefile | headings, top-level keys, tables, stages, targets; Markdown links become edges |

Vendored, generated, binary and data files are classified (linguist-style) and kept off the map.
Every edge records how it was resolved and a confidence; names that match nothing are dropped rather than guessed.
For compiler-grade precision, pass a [SCIP](https://github.com/sourcegraph/scip) index: it replaces the heuristic
references of the files it covers.

## Large repositories

Earlier measurements on a synthetic Python repository of 10,000 files and 170,000 symbols (Windows laptop):

| | |
|---|---|
| full scan | 18 s (parallel parsing) |
| rescan after editing one file | 9 s (only that file is re-parsed; unchanged files are not opened) |
| `codeatlas build` | 13 s → `graph.json` 3.5 MB, search index 15 MB (fetched on first search), 220 chunks of ~330 KB |
| zooming from regions to symbols | no main-thread task over 50 ms; 4 chunks fetched for the screen |

These measurements predate automatic scanning in `build`, module-scope cards and syntax token
fallbacks. They describe the earlier baseline; current build timing and data sizes have not been rebenchmarked.

## Development

Builds cache layout in `.codemap/cache/layout.json`: unchanged siblings retain positions, and growing
boxes move only when needed to avoid overlap. Use `build --repack` to compact and align the map again.
Large draft directory leaves can be subdivided with weighted Leiden communities before agents name
the capabilities. Validation evidence and remaining release requirements are in [docs/validation.md](docs/validation.md).

```bash
uv sync
uv run pytest
cd frontend
npm install
npm test
npm run build   # regenerates the lazy language catalog and writes src/code_atlas/web/
cd ..
uv run codeatlas build -r .   # scans and copies current viewer + data into .codemap/cache/site/
```

Releasing: bump `version` in `pyproject.toml`, push a tag `vX.Y.Z`; `.github/workflows/release.yml` builds the
viewer and the wheel and publishes with PyPI trusted publishing (configure the publisher on PyPI once).
The pending publisher for `repo-code-atlas` is configured for `cowdedroyal/code-atlas`, workflow
`release.yml`, environment `pypi`. It creates the PyPI project on first publication; no package has
been published yet. The live map is at https://cowdedroyal.github.io/code-atlas/.

Historical design notes: [docs/design.md](docs/design.md); current behavior is described above and in the bundled skill.

## License

Apache-2.0 — see [LICENSE](LICENSE).
