import { build } from "vite";
import { join } from "node:path";
import { tmpdir } from "node:os";
await build({
  configFile: false,
  root: new URL("../", import.meta.url).pathname,
  build: {
    outDir: join(tmpdir(), "codetrace-examples-native-dist"),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        app: new URL("../index.html", import.meta.url).pathname,
        native: new URL("../tests/examples/native.html", import.meta.url)
          .pathname,
      },
    },
  },
});
