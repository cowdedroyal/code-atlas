# Workflows

Budgets keep each step reviewable: a changeset should be something a teammate can read in a few minutes.

## regionize

For a repository with no regions (`agenda` shows `regionize`).

1. `codeatlas scan -r REPO` and `codeatlas status` — check language coverage; note big `vendored`/`binary`
   counts (they are off the map and need no region).
2. `codeatlas outline` — read every directory line, the entry points, the important files and externals.
3. Read the README / docs index and the 3–6 most important files (`show <path> --code`). Write down the main
   flow(s) in one line each: input → stages → output.
4. Draft 5–12 top-level regions with role and responsibility ([regions.md](regions.md)). For each directory
   you cannot place, run `context <dir>`.
5. Language: if `.codemap/config.yaml` does not exist yet, write in the user's language and put
   `"config": {"language": "<code>"}` in the first changeset; otherwise follow the configured language.
6. Changeset 1: all regions + assignment rules + one note per top-level region. `validate` → fix → `apply`.
7. `agenda --type assign` — place the remaining groups (changeset 2). Leave a remainder only if it is truly
   miscellaneous, and say so to the user.
8. If you have budget left: `agenda --type link` — add relations for the 3–5 heaviest region pairs and any
   invisible link you noticed while reading.

## assign

`agenda --type assign` lists unassigned files grouped by directory. For each group: `context <dir>`, decide
the region (existing, or a new one if it is a real capability), add a rule. One changeset for all groups.

## annotate

Take `annotate-region` tasks first, then `annotate-file`, then `annotate-symbol`, up to ~12 notes per
changeset. Read each target's code before writing ([writing.md](writing.md)).

## link

`agenda --type link` gives region pairs with heavy structural traffic and example edges. Open the examples,
find what flows, and write one relation per pair (or none if the traffic is incidental, e.g. everyone uses
the logger). Then look for invisible links: grep for HTTP clients and route definitions, subprocess calls,
queue names, file paths written in one place and read in another.

## tour

`agenda --type tour` suggests starting symbols. For each flow: read the entry point (`show --code`), follow
it with `show` and `codeatlas path <a> <b>`, pick the 4–10 stops where something meaningful happens, and write
one tour per changeset ([tours.md](tours.md)). If two stops are linked only over HTTP/queues/files, add the
relation first (same or earlier changeset) and reference it with `via_relation`.

## refresh

Run `scan` first, then `agenda --type refresh`. For each stale note or relation, read its target and
evidence, update the content if behavior changed, and validate/apply. For orphans, find where the code
went (`show <name>` searches) and move the note/relation, or delete it. If the content remains correct, use `op: confirm` to refresh its anchors; confirm rechecks evidence
and tour connectivity. Ordinary identical upserts retain old anchors. Check `agenda` afterward.

## auto

`agenda --limit 30`, then work down the list in order for N tasks (default 8), batching tasks of the same
type into one changeset. Report what you did and what the agenda shows next.

## Review the rendered result

If the user asks to see the map, build current data with `codeatlas build -r REPO` after applying semantic
changes. Start `serve` when local browsing is requested; it runs until stopped. Open the printed URL and
refresh after subsequent builds. Read [viewer.md](viewer.md) for level-specific line filtering, module
scope and Markdown behavior. Use `show`/`context` to verify data before treating a hidden line as a defect.
