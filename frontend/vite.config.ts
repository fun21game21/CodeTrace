import { defineConfig } from "vite";
export default defineConfig({
  root: new URL(".", import.meta.url).pathname,
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true, ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
