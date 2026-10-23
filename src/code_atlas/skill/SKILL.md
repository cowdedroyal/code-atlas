---
name: codeatlas
description: Write the semantic layer of a Code Atlas map — divide a repository into logical regions by first principles, assign files to them, write short notes on regions, files and key symbols, and record the logical links (including ones static analysis cannot see). Use when the user says "map this repo", "regionize", "codeatlas", "explain this project as a map", "annotate the atlas", "refresh the code map", or asks to keep a `.codemap/` up to date.
---

# codeatlas: the semantic layer of a code map

A Code Atlas map has two layers:

- **Structure** — files, classes, functions, imports, calls. The `codeatlas` program scans these
  deterministically. The viewer also derives module-scope cards for file-owned execution. These are
  generated data, not agent-authored symbols.
- **Meaning** — which *region* each file belongs to, what each region is responsible for, short notes,
  and *relations* between regions. You write this layer, through changesets the program validates.

The meaning layer is stored as text in the repository's `.codemap/` and reviewed like code, so write
for a teammate who is new to the project.

## How to call it

```bash
codeatlas <command> -r <repo>        # or: uvx repo-code-atlas <command> …; query/mutation commands print JSON
```

| Command | Use |
|---|---|
| `scan` | refresh the structure index; run first, and again after code changes |
| `status` | sizes, language coverage, semantic coverage (regions, unassigned files, stale notes) |
| `agenda [--type T] [--limit N]` | what to do next, most important first |
| `outline` | the whole repo at a glance — start every regionize here |
| `context <region:id \| dir \| file>` | members with docstrings and main symbols, dependencies in and out |
| `show <id> [--code]` | one file or symbol with neighbours; `--code` prints the source |
| `path <a> <b>` | the call chain from one file/symbol to another — use it while drafting tours |
| `validate -f cs.json` / `apply -f cs.json` | dry-run / write a changeset (format: [references/changeset.md](references/changeset.md)) |
| `log` / `revert <run>` | applied runs / undo the latest run |
| `build [-o SITE] [--no-source]` | refresh the scan and write a static viewer with current data |
| `serve [--port N] [--no-source]` | scan, build and serve locally; open the printed URL yourself |

Keep changesets in the task's scratch directory, preferably outside the mapped repository so drafts
do not become map nodes.

## Modes

| The user says | Mode | Steps |
|---|---|---|
| "map / regionize this repo" | **regionize** | [workflows.md § regionize](references/workflows.md#regionize) |
| "assign the rest" | **assign** | `agenda --type assign` → place each group |
| "annotate / explain" | **annotate** | notes for regions, then important files and symbols |
| "how do the parts connect" | **link** | relations between regions, especially invisible ones |
| "walk me through X / write a tour" | **tour** | 4–10 connected steps through one flow ([references/tours.md](references/tours.md)) |
| "refresh / it's out of date" | **refresh** | rewrite stale notes, fix orphaned relations, repair stale or broken tours |
| "run a round" | **auto** | follow `agenda` for N tasks (default 8) |

How to divide a system into regions — the core of this skill — is in [references/regions.md](references/regions.md).
How to choose and phrase relations: [references/relations.md](references/relations.md). How to write notes:
[references/writing.md](references/writing.md). How to write tours: [references/tours.md](references/tours.md).
When checking or handing off the rendered map, read [references/viewer.md](references/viewer.md) for
level locks, line meanings, generated module-scope cards and rebuilding.

## Hard rules

1. **Look before you write.** Base every region, note and relation on what `outline`, `context`, `show --code`
   or the files themselves say — never on names alone. Read the code of anything you describe.
2. **Regions are capabilities, not folders.** Name each by what the system would lose without it
   ("Payment provider client", not "stripe_utils"). Directory structure is evidence, not the answer.
3. **Every file belongs somewhere.** After regionizing, `state.unassigned` should be 0 or a small, explained
   remainder. Prefer directory globs; put exceptions after the broad rule (later rules win).
4. **Every relation has evidence** — symbol ids or `path:start-end` ranges that show the link — and a one-sentence
   rationale. Don't restate import edges the map already draws; add what explains *why* or what the scanner
   cannot see (HTTP calls, queues, subprocesses, files handed over on disk, config that wires parts together).
5. **Short and true beats complete.** Notes follow the limits in writing.md. If you are unsure, leave it out
   rather than guessing.
6. **Write in the configured language** (`.codemap/config.yaml` → `language`; on the first run set it with
   `"config": {"language": …}` in the changeset, matching the user's language). Keep identifiers and paths as they are.
7. **Validate, read the errors, fix the content** — never weaken a note or drop evidence just to pass.
   Apply only after `"ok": true`.
8. **Use changesets for semantic files.** In a mapping task, do not edit source code, the structure
   index or built JSON to make the map agree with a description. Explicit source/viewer repair requests
   are separate work; after those edits, rescan before inspecting or anchoring semantic content.
9. **Use indexed targets.** Viewer-only `path#@module` cards are absent from `show`, `path` and changeset
   validation. For top-level code, annotate the file and use existing symbols or `path:start-end` evidence.

## Session skeleton

```text
codeatlas scan -r REPO                     → index is current
codeatlas status -r REPO / agenda          → what is missing
codeatlas outline -r REPO                  → the shape of the repo (regionize)
codeatlas context <dir or region:id>       → zoom in; read key files with show --code
write cs-01.json  → codeatlas validate -f cs-01.json → fix → codeatlas apply -f cs-01.json
codeatlas agenda                            → confirm it moved; continue with the next task
codeatlas build -r REPO                     → refresh and rebuild when a rendered result is requested
```

Finish by telling the user in two or three sentences what regions you drew, what was hard to place, and any
relation you found that the code structure alone would not show. If a rendered result was requested,
build it and report its path or the running service URL. `serve` runs until stopped and watches semantic files by default; source changes require rebuilding.
Refresh the page after updates.
