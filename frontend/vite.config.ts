import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built viewer ships inside the Python package; `codeatlas build` copies it next to data/.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "../src/code_atlas/web", emptyOutDir: true, chunkSizeWarningLimit: 2000 },
  worker: { format: "es" }, // keep language grammar imports separate and load them on demand
  // `npm run dev` against a site built with `codeatlas build -o ../.site`
  server: { proxy: { "/data": { target: "http://127.0.0.1:8765", changeOrigin: true } } },
});
