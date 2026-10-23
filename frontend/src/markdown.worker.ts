import { parseMarkdown } from "./markdownEngine";

self.onmessage = (event: MessageEvent<{ id: number; text: string }>) => {
  try { self.postMessage({ id: event.data.id, tokens: parseMarkdown(event.data.text) }); }
  catch { self.postMessage({ id: event.data.id, tokens: null }); }
};
