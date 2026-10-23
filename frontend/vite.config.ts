import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: process.env.ACH_API_URL ?? "http://localhost:8600", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
});
