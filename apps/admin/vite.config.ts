import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/admin/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/health": "http://127.0.0.1:8080",
      "/internal": "http://127.0.0.1:8080",
      "/api": "http://127.0.0.1:8080"
    }
  },
  build: {
    outDir: "dist",
    sourcemap: true
  }
});
