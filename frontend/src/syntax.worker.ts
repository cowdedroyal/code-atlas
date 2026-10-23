import { tokenize } from "./syntaxEngine";

self.onmessage = async (event: MessageEvent<{ id: number; prefix: string; language: string; lo: number }>) => {
  const { id, prefix, language, lo } = event.data;
  try { self.postMessage({ id, rows: await tokenize(prefix, language, lo) }); }
  catch { self.postMessage({ id, rows: null }); }
};
