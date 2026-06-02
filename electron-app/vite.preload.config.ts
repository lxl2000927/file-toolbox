import { defineConfig } from "vite";
import { builtinModules } from "module";
import { resolve } from "path";

export default defineConfig({
  build: {
    outDir: "dist/preload",
    lib: {
      entry: resolve(__dirname, "preload/index.ts"),
      formats: ["cjs"],
      fileName: () => "index.js",
    },
    rollupOptions: {
      external: [
        "electron",
        ...builtinModules,
        ...builtinModules.map((m) => `node:${m}`),
      ],
    },
    minify: false,
    sourcemap: false,
  },
});
