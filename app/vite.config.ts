import { defineConfig } from "vite";

// 构建产物输出到 Python 包里：`jiyao ui`（浏览器 / pywebview）和 Tauri 用同一份界面
export default defineConfig({
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    // 在浏览器里开发：接口转给本地跑着的 `jiyao serve --port 8765`
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  build: {
    outDir: "../src/jiyao/web",
    emptyOutDir: true,
    target: "safari16", // Tauri 在 macOS 上用系统的 WKWebView
  },
});
