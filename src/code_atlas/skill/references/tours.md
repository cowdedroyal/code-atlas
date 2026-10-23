# Tours: walking a reader through one flow

A tour is the fastest way into an unfamiliar codebase: "here is what happens when a request comes in",
stop by stop, one sentence each, played on the map. Write tours for the two or three flows a newcomer
must understand first — usually the main request/command path and the main data pipeline.

## Choosing a flow

- Start where the outside world comes in: a route handler, CLI command, message consumer or UI event
  (`agenda` → `tour` lists candidates with wide fan-out in `interface` regions).
- End where the flow produces its effect: data written, response returned, job handed off.
- 4–10 steps. Fewer and it says nothing; more and it is a call graph, not a story.

## Each step

```json
{"at": "api/routes/orders.py#create_order", "text": "Validates the cart and reserves stock before any money moves."}
```

- `at` is a symbol id (preferably) or a file.
- Use IDs from `show`/`context`; generated viewer `path#@module` cards are not valid tour stops. A top-level
  flow can start at its file when the file's scanned execution is the actual entry point.
- `text` (≤ 220 chars) says what happens *here* and why it matters for the flow — not what the function is
  called, not its parameters.
- Skip plumbing (wrappers, logging, small helpers): the map draws the intermediate calls between two steps
  for you, so you only stop where something meaningful happens.

## Connected, or it is not accepted

Every step after the first must be reachable from the story so far: `validate` checks that the scanner can
follow calls from some earlier step (anything inside it) to this one within 6 hops, trying the nearest step
first. So an orchestrator can appear once and its successive calls follow as separate steps. Reaching a class
counts as reaching its methods. Use `codeatlas path <a> <b>` while drafting to see the chain.

When the real link is invisible to static analysis — an HTTP call, a WebSocket message, a subprocess, a file
one stage writes and the next reads — first record it as a relation (relations.md), then name it on the step:

```json
{"at": "app/server.py#websocket_endpoint", "text": "The backend receives the chat message.",
 "via_relation": "rel-1a2b3c4d5e"}
```

The relation's `src` must cover an earlier step and its `dst` this step (a region end covers every file in
that region).

If `validate` says there is no path, don't drop the check by switching to a file-level step: either add the
step in between, or find out why the link is invisible and record it.

## Keeping tours true

Each step is anchored to its code. When a step's code changes the tour shows as `stale`; when consecutive
steps stop being connected it is `broken`; both appear under `agenda --type refresh` as `tour:<id>`.
Re-read and repair the steps, then validate/apply and check the refresh agenda. Use `{"op":"confirm","id":"<tour-id>"}` for a still-correct tour; confirmation checks connectivity
and refreshes all step anchors. An unchanged upsert keeps its prior anchors.
