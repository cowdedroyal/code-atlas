# Validation record (2026-10-23)

This records completed checks and their limits; it is not a claim that every design goal is finished.

## Published tour review

The stale badge on the Pages map was the existing anchor check. The published main revision still used
the initial annotations; a separate refresh branch had not been deployed. That branch's symbol anchors
matched local code, but some descriptions omitted later scan/layout/zoom changes and file evidence used
a CRLF hash that differs in Linux. Desktop review corrected the descriptions and confirmed current
anchors. File annotation hashes now normalize line endings separately from raw source consistency hashes.
The warning feature remains enabled; changed behavior must still trigger review.

## Local checks

- Windows Python 3.12 and an isolated Python 3.13.16 environment: 49 backend tests pass.
- Frontend: 25 tests pass; TypeScript and Vite production build pass. Syntax grammar tests cover
  193 lazy grammars plus Vue/Svelte, source preservation and multiline scopes.
- A real `@sourcegraph/scip-typescript` 0.5.2 index was generated from a TypeScript project, imported
  with 2 covered files and 1 cross-file call. This exposed absolute Windows paths; normalization now
  handles them and has a regression test. Other real SCIP producers have not been tested.
- `uv sync --locked` completed. An incomplete legacy numpy installation was moved out of the virtual
  environment; importing numpy is no longer possible. No previous serve process was running at inspection.
- Semantic confirmation, watcher debounce, Git rename with a changed body, stable positions on file
  addition, non-overlapping boxes and Leiden communities have regression tests.

## Real public repositories

Pinned shallow clones were scanned and built locally without executing their application code.
All six had zero parser failures. These are representative projects, not one large multilingual repository.

| Language | Repository | Commit | Parsed files | Symbols |
|---|---|---|---:|---:|
| Go | hashicorp/raft | a5b469295ddc9924541477eafe34bf59a8571063 | 84 | 1,513 |
| Java | google/gson | 881d2b55de74dd6c2fded7b2cb307b048117af97 | 297 | 5,147 |
| Rust | serde-rs/json | 8c535a260ce561eaab4111aafc29038eb9f77ff3 | 78 | 1,735 |
| C | redis/hiredis | 5bb7f030f6631b243438cf0fdd3be84352b905bb | 67 | 1,146 |
| C++ | fmtlib/fmt | 0fc31633e7d53ac895ae0a688b83b7d44f0b0c1f | 111 | 5,229 |
| C# | commandlineparser/commandline | 1e3607b97af6141743edb3c434c06d5b492f6fb3 | 204 | 1,987 |

C# namespace cycles exposed repeated export traversal. Visited lookup pairs and a per-scan cache now
bound this search. A deterministic sample of 20 cross-file call edges found three incorrect unknown-receiver
matches (Go `t.Logf`, Rust `scratch.clear`, C++ `std::move`); the mocked close call was ambiguous without
expanding its macro, rather than a proven wrong target. Global-unique lookup now
accepts only bare names; unknown dotted receivers are omitted instead of mapped to unrelated methods.
The remaining sampled edges have source invocation and target-definition evidence, but this small review
does not establish a precision percentage for all edges or overload resolution.

## Scale

A generated Python repository with **50,000 files and 500,000 functions** scanned in **56.36 s** and built
static data without source in **21.20 s**, producing 200 symbol chunks and no parse failures. Generation
took 52 s. The fixture has no call edges, so this measures node volume rather than a dense dependency graph;
large-scale browser frame times and dense-edge performance remain unmeasured.
Browser smoke testing of this generated map loaded the overview in about 2.94 s with zero symbol-chunk
requests; navigating to a file requested four chunks and produced no runtime errors. This is not a frame-time benchmark.

## Remaining boundaries

- Stable layout preserves sibling coordinates when they still fit. Growing/relocated parents may move
  descendants. `build --repack` compacts and uses translation-only Procrustes alignment; arbitrary rotation
  and scaling would break the axis-aligned nested rectangles and are not implemented.
- L1 overview links share region hubs. This is simple hierarchical bundling, not iterative force-directed
  edge bundling. Selected edges remain direct.
- `serve` watches semantic files, not arbitrary source edits. Rebuild/scan after source changes; refresh
  the browser after any data rebuild. Automatic browser reload is not implemented.
- [CI run 39949977702](https://github.com/cowdedroyal/code-atlas/actions/runs/39949977702) succeeded:
  Python 3.12/3.13 across Windows/Linux/macOS, plus the frontend tests/build.
- No release tag or PyPI upload has been made. A pending publisher for **repo-code-atlas** is now
  registered for owner `cowdedroyal`, repository `code-atlas`, workflow `release.yml`, environment `pypi`.
  PyPI rejected `code-atlas` as too similar to an existing project. Pending registration does not reserve
  the name or create the project until the first publication.
  [Pages run 39949977648](https://github.com/cowdedroyal/code-atlas/actions/runs/39949977648) built the
  site but initially failed deployment with 404. Pages was subsequently enabled with GitHub Actions:
  [run 39951023277](https://github.com/cowdedroyal/code-atlas/actions/runs/39951023277) succeeded and
  the [live map](https://cowdedroyal.github.io/code-atlas/) was verified in the browser.
- The private integration fixture received a first batch of 6 important-file and 5 symbol notes. Its remaining annotation
  agenda is not exhausted and its `.codemap` remains uncommitted, as requested.
