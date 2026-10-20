import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run build` emits the client UI into desktop/dist, which the desktop client's backend serves.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../desktop/dist", emptyOutDir: true },
});
