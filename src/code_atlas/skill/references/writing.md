# Writing notes

A note answers, in this order and only as far as it is useful:

1. **What problem it solves** — the constraint or need that makes it exist.
2. **How** — the key idea or mechanism, not a walkthrough.
3. **Boundary or cost** — what it deliberately does not do, or the trade-off it makes.

Limits (characters): region 500, file 360, symbol 280. Most good notes are one to three sentences.

The detail panel renders Markdown in notes, responsibility text, relation rationales and tour steps.
Use inline code for identifiers, emphasis sparingly, and a short list only when it improves reading.
The limits count Markdown syntax and URLs too. Keep region names, relation labels and tour titles plain:
they appear on the canvas, where Markdown is not rendered. Semantic notes and relation fields reject
fenced code blocks; use inline code and links instead. Markdown source files can contain fenced blocks
with language tags for highlighting. Raw HTML, math and Mermaid do not render as rich content.
Relative repository links in file/symbol notes resolve from that file's directory; region notes have
no file base, so do not rely on relative links there.

## Do

- Use the project's own vocabulary (its domain terms, its names for stages).
- Mention the one or two things a newcomer would otherwise get wrong ("runs synchronously inside the
  request", "the sandbox copies, not imports, the main pipeline").
- For a region, say what goes in and what comes out.

## Don't

- Restate the name or the signature ("`load_profile` loads a profile").
- Narrate the code line by line, list every function, or paste code.
- Speculate ("probably used for…"). If you didn't read it, don't describe it.
- Write marketing ("powerful", "robust", "seamless").

## Examples

| Target | Bad | Good |
|---|---|---|
| region `payments` | "Contains the stripe folder and helpers." | "Takes a priced order and makes the money move: creates Stripe payment intents, verifies webhooks and marks orders paid or failed. It never computes prices — that is `pricing`." |
| file `config/profiles.py` | "Loads config." | "Merges per-environment YAML over defaults so limits and feature flags change per deployment without code changes." |
| symbol `ProgressBus` | "Class that emits progress." | "Single place every stage reports progress through; the HTTP server relays these events to the browser over WebSocket." |

## What to annotate first

`agenda` lists `annotate-region`, then `annotate-file` by importance (PageRank over the dependency
graph), then `annotate-symbol` by how many places use it. Regions first: they are what the reader sees
first. Skip files whose docstring already says what the note would say.

## Stale notes

When the code under a note changes, the note is flagged `stale` (`agenda --type refresh`). Re-read the
code and update the note to describe the current behavior. For a still-correct note, use `{"op":"confirm","target":"<id>"}` after reading current code.
This updates its anchor without cosmetic text edits; an identical upsert keeps the old anchor. Check
`agenda` after applying. `orphan` means the code is gone: delete or move it to a verified target.
