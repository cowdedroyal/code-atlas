# Changeset format

One JSON object; every section is optional. `op` defaults to `upsert` (regions, notes, relations) or `add`
(assign).

```json
{
  "intent": "regionize",
  "config": {"language": "zh"},
  "regions": [
    {"op": "upsert", "id": "payments", "name": "Payments",
     "role": "integration", "responsibility": "Moves money for priced orders through Stripe and records the outcome.",
     "parent": null},
    {"op": "delete", "id": "old-region"}
  ],
  "assign": [
    {"op": "add", "match": "services/payments/", "region": "payments", "also": []},
    {"op": "remove", "match": "some/old/glob/"}
  ],
  "notes": [
    {"op": "upsert", "target": "region:payments", "text": "…"},
    {"op": "upsert", "target": "services/payments/webhooks.py", "text": "…"},
    {"op": "upsert", "target": "services/payments/webhooks.py#verify_signature", "text": "…"},
    {"op": "delete", "target": "…"}
  ],
  "relations": [
    {"op": "upsert", "src": "region:a", "dst": "region:b", "family": "data", "label": "…", "rationale": "…",
     "evidence": ["path/to/file.py#func", "other/file.ts:120-140"]},
    {"op": "delete", "id": "rel-0123456789"}
  ],
  "tours": [
    {"op": "upsert", "id": "place-an-order", "title": "…", "summary": "…",
     "steps": [{"at": "web/src/pages/Checkout.tsx#submitOrder", "text": "…"},
               {"at": "api/routes/orders.py#create_order", "text": "…", "via_relation": "rel-0123456789"}]},
    {"op": "delete", "id": "old-tour"}
  ]
}
```

| Field | Rule |
|---|---|
| `config.language` | language of all written text (`en`, `zh`, `ja`…); set once on the first run |
| region `id` | lowercase slug `[a-z0-9-]`, stable — other entries refer to it; rename by changing `name`, not `id` |
| `name` | ≤ 40 chars, a capability in a few words |
| `role` | one of `interface orchestration core state integration presentation foundation tooling` |
| `responsibility` | ≤ 140 chars, one sentence: what the system would lose without it |
| `parent` | another region's id, or null for top level |
| `match` | gitignore-style glob; must match ≥ 1 mapped file; re-adding an existing `match` moves it last |
| `also` | secondary regions (highlighting only; the primary region decides placement) |
| note `target` | `region:<id>`, a file path, or an indexed symbol id `path#Qual.name` (from `show` / `context`); generated `path#@module` cards are not valid targets |
| tour `id` / `title` ≤ 60 / `summary` ≤ 300 | slug; a tour is replaced as a whole on upsert |
| tour `steps` | 2–20; `at` is a symbol id or file, `text` ≤ 220; consecutive steps must be connected: each step reachable by a call path (≤ 6 hops) from some earlier step, or by the relation named in `via_relation` — see tours.md |
| relation `family` | `control data contract config observe` |
| `label` ≤ 48, `rationale` ≤ 200 | see relations.md |
| `evidence` | ≥ 1 symbol id, file path, or `path:start-end` that exists in the current index |

Deleting a region also removes its note and relations (reported as a warning); rules or child regions
still pointing at it are an error. `validate` reports all problems at once; `apply` either writes the whole
changeset or nothing. Re-applying the same changeset changes nothing.

After reviewing still-correct content against a current scan, confirm it without changing text:

```json
{"notes":[{"op":"confirm","target":"app/service.py#run"}],
 "relations":[{"op":"confirm","id":"rel-0123456789"}],
 "tours":[{"op":"confirm","id":"checkout"}]}
```

Confirm accepts only the identifier and `op`. It refreshes note/evidence/step hashes, rejects missing
targets or evidence, and revalidates a tour's connectivity. It is revertible and repeating it against
the same code leaves semantic content unchanged.

Text fields may contain Markdown where the detail panel supports it; character limits apply to the
stored source, including formatting. Names, relation labels and tour titles should be plain text.
Ordinary identical upserts preserve prior anchors. Use explicit `confirm` after reviewing current
code to refresh a still-correct note, relation or tour without changing its text.
