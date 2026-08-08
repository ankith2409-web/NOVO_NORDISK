import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";


// The API runs as a separate Python process. Proxying it through the dev server
// keeps the browser on one origin, so the chat's session cookie behaves exactly
// as it will in production and CORS never enters the picture during development.
// The server's cross-origin support still matters -- it is what allows the two
// to be served from different ports -- but relying on it here would mean
// developing against a code path the shipped app does not use.
const API = process.env.CONCORDANCE_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": new URL("./src", import.meta.url).pathname },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API, changeOrigin: false },
    },
  },
});
