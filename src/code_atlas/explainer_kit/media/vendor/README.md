# Vendored chart runtime

`<ex-chart interactive>` needs a Vega runtime inside the card, because a card has no network and cannot load one.
These two files are that runtime, copied here once so that every build is offline and reproducible:

| File | Package | Version | License |
|---|---|---|---|
| `vega.min.js` | [vega](https://github.com/vega/vega) | 6.4.0 | BSD-3-Clause (`LICENSE-vega.txt`) |
| `vega-interpreter.min.js` | [vega-interpreter](https://github.com/vega/vega/tree/main/packages/vega-interpreter) | 2.3.2 | BSD-3-Clause (`LICENSE-vega-interpreter.txt`) |

The interpreter is what makes Vega work under the card's Content-Security-Policy: Vega normally compiles its
expressions with `new Function`, which `script-src 'unsafe-inline'` (deliberately) does not allow. With
`vega.parse(spec, {ast: true})` plus `new vega.View(runtime, {expr: vegaInterpreter})`, the expressions are
interpreted instead of compiled, so no `'unsafe-eval'` is needed.

`vega-interpreter` ships as an ES module, which cannot be inlined as a plain `<script>`; the vendored file is its
bundle with a `window.vegaInterpreter` global:

```bash
npm i vega@6.4.0 vega-interpreter@2.3.2
printf "import { expressionInterpreter } from 'vega-interpreter';\nwindow.vegaInterpreter = expressionInterpreter;\n" > interp-entry.js
npx esbuild interp-entry.js --bundle --format=iife --minify --outfile=vega-interpreter.min.js
```

Update these files (and this table) together, and check `tests/test_media.py::test_interactive_chart_is_csp_safe`
still passes: it packs a chart card and asserts the page has no `new Function` call in the card's own markup.
