import { defineConfig } from "vitest/config";
// Port 1421 so it can run next to idle-games (1420).
export default defineConfig({
  clearScreen: false,
  server: { port: 1421, strictPort: true },
  build: { outDir: "dist", target: "es2022" },
  test: { environment: "jsdom" },
});
