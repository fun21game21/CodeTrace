import { defineConfig } from "vite";
export default defineConfig({
  root: new URL(".", import.meta.url).pathname,
  build: {
    outDir: new URL("./dist", import.meta.url).pathname,
    emptyOutDir: true,
    rollupOptions: {
      input: {
        benchmark: new URL("./index.html", import.meta.url).pathname,
        stress: new URL("./stress.html", import.meta.url).pathname,
      },
    },
  },
});
