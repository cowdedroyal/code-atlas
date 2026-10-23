# Checking and handing off the map

Use this reference when a mapping task includes visual review or a request to view the result.
The semantic changeset schema is unchanged by viewer features.

## Levels and lines

| Level | Nodes and lines |
|---|---|
| L0 | Regions; aggregated structural dependencies plus labeled region-to-region semantic relations |
| L1 | Files; imports and calls/references aggregated between files, plus file-to-file semantic relations |
| L2 | Symbols; calls, references, inheritance and instantiation, plus symbol-to-symbol semantic relations |

`imports` means a file brings another file's definitions into scope; `calls` means source contains a
function invocation. Incoming and outgoing lists are the two directions of the same edge. Calls are
static evidence, not a record that a branch actually ran. Structural L0 edges have a weight but no
natural-language label; semantic labels can also be hidden to avoid overlap. Missing label text alone
is not evidence that a relation is missing.

Relation endpoints may mix regions, files and symbols in the stored schema, but the map draws a semantic
line only when **both endpoints match the current level**. Mixed-level relations remain in the detail
panel and available to tour validation. Choose region-to-region endpoints for an L0 overview; do not
fabricate duplicate semantic relations just to force a line into another level.

**Lock level** keeps L0/L1/L2 content fixed while zooming and panning; labels grow with zoom. Clicking a
level selects and locks it directly. At locked L0, clicking a rectangle selects it and keeps its incoming
and outgoing lines focused; empty map space clears focus. At unlocked L0, clicking still zooms into the
region. At L1/L2, hover/selection controls the visible dependencies. Offscreen or unreadably small symbol
endpoints may be omitted from the map; inspect the detail lists and `show` before diagnosing missing data.
Tours have a separate route overlay.

## Module scope is generated

The builder creates a **Module scope** card for a source/test file with scanned file-owned execution
edges, including anonymous callbacks. It connects these calls/references at L2 without showing L1
import lines. A file can contain both this card and named declarations. They represent different
execution locations, not duplicate executions of the file.

The card is a viewer-only projection with an ID such as `path#@module`. It is not in the scanner's
symbol index: do not use it as a note target, semantic endpoint, evidence symbol or tour stop. Use the
file ID, a real indexed symbol or a source range instead. Module-scope code folds declaration bodies,
keeping signatures, decorators and original line numbers; individual folds and **View complete file**
provide context. Other declaration cards retain their exact ranges.

## Text and source

Descriptions, notes, relation rationales and tour step text render Markdown. Keep region names, relation
labels and tour titles plain because they also appear on the canvas and in compact lists. Writing limits
count the stored Markdown characters; see [writing.md](writing.md).

Markdown files/sections default to **Preview** with a **Source** toggle. Source preserves original line
numbers and inclusive symbol ranges. Repository links in file/symbol text open mapped files; external
links open separately. HTML is literal text and images are shown as links. Math and Mermaid diagrams
are not rendered. Fenced code uses the existing lazy syntax highlighter.

The panel's left edge resizes it; Ctrl + wheel and − / + change text size. Large source excerpts start
at 400 lines and can expand. Markdown parsing and browser syntax highlighting run in cached, on-demand
workers; uncommon scanner languages can use prebuilt tree-sitter tokens fetched with source details.
Very large prefixes or unavailable grammars/workers can fall back to plain text.

## Build current data

- `codeatlas build -r REPO -o SITE` incrementally scans before building. Included source is hash-checked
  against the index; if a file changes during the build, rescan and rebuild.
- `codeatlas serve -r REPO --port 8765` scans and builds, then serves the printed local URL. It does not open a browser. Semantic changes trigger a rebuild by default (`--no-watch` disables
  this); source changes require scan/build. Refresh the browser after rebuilding.
- `serve --no-scan` deliberately uses the existing index; use it only when that index is current.
- After viewer source changes in a clone, run `npm run build` in `frontend/` first, then `codeatlas build`
  to copy viewer assets alongside new data. `--no-source` omits code and prebuilt syntax data.
- Installed skills are copies. After this bundled skill changes, run `codeatlas skill install` again
  for the same target when the user requests updating the installed copy.
