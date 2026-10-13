import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Derlenmiş çıktı FastAPI tarafından ada/static/dist altından sunulur.
// Geliştirme modunda (npm run dev) /api istekleri 8000 portundaki ADA sunucusuna yönlenir.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../ada/static/dist", emptyOutDir: true },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
});
