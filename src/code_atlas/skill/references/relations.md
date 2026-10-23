# Relations: the links worth writing down

The scanner records imports, calls, inheritance and instantiation it can resolve; the viewer filters
them by level, focus and visibility. A semantic
relation is worth adding when it tells the reader something those lines cannot:

1. **Invisible links** — the scanner sees no edge, but the parts depend on each other: an HTTP request to
   another service in the same repo, a subprocess, a job queue, a file one part writes and another reads,
   environment variables or config files that wire parts together, a frontend calling a backend route.
2. **The reason behind a heavy link** — two regions have many edges between them (`agenda` → `link` tasks);
   say what flows and why, in one phrase.

Don't write relations that just restate one import.

## Families

Each relation has one closed **family** and an open **label**:

| Family | The link is about | Label examples |
|---|---|---|
| `control` | one part triggers, calls or schedules another | "runs each case through", "dispatches jobs to", "serves requests for" |
| `data` | data is produced, consumed or transformed | "writes plan JSON read by", "streams progress events to", "feeds masks into" |
| `contract` | one part implements, extends or constrains another | "implements the tool API of", "validates plans against the schema in" |
| `config` | configuration, registration or wiring | "selects the model and keys for", "registers routes of" |
| `observe` | monitoring, logging, testing, evaluation | "evaluates the output of", "tests", "logs every step of" |

Write the label as a verb phrase read from `src` to `dst`: *src* — label → *dst*. Keep it under ~6 words.

## Ends and evidence

- Ends can be regions (`region:<id>`), files (`path`) or symbols (`path#Qual.name`). Prefer regions for the
  L0 picture; use files or symbols when the link is specific (an endpoint and its client).
- The map draws a relation only when both ends are at the active level. Mixed-level relations still
  appear in details and work for tours. A missing canvas line is not a reason to invent another relation.
- Generated module-scope cards are viewer-only; use their file, real indexed symbols or source ranges
  as semantic targets/evidence. See [viewer.md](viewer.md#module-scope-is-generated).
- `evidence` is required: the symbols or `path:start-end` ranges where the link is visible — the code that
  sends the request and the handler that receives it, the writer and the reader of a file. The program
  records a hash of each piece of evidence; when that code changes, the relation is flagged for refresh.
- `rationale` is one sentence a reviewer can check against the evidence.

```json
{"src": "region:web-app", "dst": "region:orders-api", "family": "control",
 "label": "places orders through",
 "rationale": "The checkout page POSTs the cart to /api/orders, handled by create_order.",
 "evidence": ["web/src/pages/Checkout.tsx#submitOrder", "api/routes/orders.py#create_order"]}
```

If you cannot point at evidence, the relation is a guess — leave it out.
