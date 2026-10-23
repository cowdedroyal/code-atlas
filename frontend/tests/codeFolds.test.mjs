import assert from "node:assert/strict";
import test from "node:test";
import { foldedCodeRows } from "../src/codeFolds.ts";

test("declaration bodies become expandable folds without renumbering module code", () => {
  const lines = ['app = create()', '@app.command()', 'def run():', '    body()', '    finish()', '', 'app()'];
  const fold = { from: 4, to: 5, name: 'run', id: 'cli.py#run' };
  const rows = foldedCodeRows(lines, 1, [fold]);
  assert.deepEqual(rows.filter(r => r.kind === 'line').map(r => r.n), [1, 2, 3, 6, 7]);
  assert.deepEqual(rows[3], { kind: 'fold', fold });
  assert.equal(foldedCodeRows(lines, 1, []).length, 7);
});
test("long folded bodies do not hide later top-level execution behind a source line limit", () => {
  const lines = ['def run():', ...Array(1000).fill('    body()'), 'app()'];
  const rows = foldedCodeRows(lines, 1, [{ from: 2, to: 1001, name: 'run', id: 'run' }]);
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.at(-1), { kind: 'line', n: 1002, text: 'app()' });
});
