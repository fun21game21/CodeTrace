import { build } from "vite";
await build({
  configFile: false,
  root: new URL("../", import.meta.url).pathname,
  build: {
    outDir: new URL("../tests/history/dist", import.meta.url).pathname,
    emptyOutDir: true,
    rollupOptions: {
      input: {
        app: new URL("../index.html", import.meta.url).pathname,
        native: new URL("../tests/history/native.html", import.meta.url)
          .pathname,
      },
    },
  },
});
