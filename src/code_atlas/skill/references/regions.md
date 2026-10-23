# Regions: dividing a system by first principles

## The question

For every candidate region ask: **"If this part disappeared, what capability would the system lose?"**
The answer, said in a few words, is the region's name; said in one sentence, its `responsibility`.

- Good: *"Payment provider client"* — "Charges cards through Stripe and turns its webhooks into order
  state changes." Without it, nobody can pay.
- Bad: *"utils"*, *"core"*, *"src"*, *"misc"*, *"v2"* — these name a folder or a feeling, not a capability.

Directories are the author's first guess at what belongs together; use them as evidence and often as
the assignment glob, but a directory can hold two capabilities and one capability can span directories.

## Roles

Every region also gets one of eight **roles**. Roles are fixed across all repositories (they colour the
map and its legend); they come from what every program does with information: take it in, decide and
transform, hold state, cause effects outside — plus the parts that support or build the rest.

| Role | It answers | Typical contents |
|---|---|---|
| `interface` | Where does the outside world come in? | HTTP routes, CLI entry points, MCP/RPC servers, message consumers |
| `orchestration` | Who strings the steps together? | pipelines, job runners, workflow/agent loops, schedulers |
| `core` | What are the rules and algorithms, independent of I/O? | domain logic, geometry, scoring, prompt construction |
| `state` | What does the data look like and where does it live? | schemas, models, storage, caches, datasets the code reads |
| `integration` | Which outside systems does it call? | API/SDK clients, model services, cloud storage, device drivers |
| `presentation` | What do people look at? | UI components, report/HTML generators, viewers |
| `foundation` | What does everyone share? | config loading, logging, credentials, small common helpers |
| `tooling` | What never runs in production? | tests, scripts, experiments, build/deploy, docs |

Pick the role by what the region is *for*, not by its file types. A report generator that writes HTML
for humans is `presentation` even though it is Python. If a region honestly does two of these, it is
probably two regions.

## Granularity

- **Top level: 5–12 regions.** Fewer and the map says nothing; more and nobody remembers them.
- **Nest** (`parent`) when a region has more than ~40 files *or* clearly separate sub-capabilities. Keep
  the tree at most three levels deep.
- A region of one or two files is fine if it is a real, separate capability (e.g. a credentials loader);
  otherwise fold it into the region it serves.
- **Copies and variants** (experiment variants, vendored forks, generated per-case folders): one region for
  the family, with a note explaining what varies — not one region per copy.
- **Tests** go to a `tooling` region; use `also` on the rule to point at the region they verify when that
  is clear (`{"match": "tests/test_api*.py", "region": "verification", "also": ["http-api"]}`).
- **Data and fixtures** that code reads at runtime are `state`; sample outputs and reports are `tooling`.

## Assignment rules

Rules are gitignore-style globs, evaluated in order, **later rules win**:

```json
{"match": "services/orders/", "region": "ordering"},
{"match": "services/orders/pricing/", "region": "pricing"},
{"match": "services/orders/tests/", "region": "verification", "also": ["ordering"]}
```

- A pattern containing `/` is anchored at the repo root; `tests/` without a leading path matches any
  `tests/` directory. `**` crosses directories.
- Every rule must match at least one mapped file (validation enforces it).
- After applying, read the warnings: they list what is still unassigned, grouped by directory.

## Procedure

1. `codeatlas outline` — directories with sizes, entry points, the most important files with their
   docstrings and main symbols, and external packages. Note which packages reveal integrations
   (`fastapi` → HTTP interface, `google.genai` → model service, `PIL`/`numpy` → image processing…).
2. Read the README and the 3–6 most important files (`show --code`, or open them). Find the main flows:
   what comes in, what happens to it, what goes out.
3. Draft the top-level regions on paper: name, role, one-sentence responsibility, which directories.
4. `context <dir>` for any directory you are unsure about; split or merge.
5. Write one changeset with regions + rules (+ a note for each top-level region if you have read enough).
6. `validate`, fix, `apply`; then `agenda --type assign` until nothing important is left unassigned.
