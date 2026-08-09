/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { viteSingleFile } from "vite-plugin-singlefile";


// The API runs as a separate Python process. Proxying it through the dev server
// keeps the browser on one origin, so the chat's session cookie behaves exactly
// as it will in production and CORS never enters the picture during development.
// The server's cross-origin support still matters -- it is what allows the two
// to be served from different ports -- but relying on it here would mean
// developing against a code path the shipped app does not use.
const API = process.env.CONCORDANCE_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  // The snapshot build inlines everything into one HTML file so the interface
  // can be shared as a link. The normal build is untouched.
  plugins: [
    react(),
    tailwindcss(),
    // Two different reasons to inline everything into one HTML file. The
    // snapshot build answers from captured data with no server at all; the
    // embedded build still talks to a live /api, and is inlined only so the
    // Python server can serve the whole interface as a single file without
    // routing asset requests it has no other reason to route.
    ...(process.env.VITE_SNAPSHOT === "1" || process.env.VITE_EMBED === "1"
      ? [viteSingleFile()]
      : []),
  ],
  resolve: {
    alias: { "@": new URL("./src", import.meta.url).pathname },
  },
  // `environment: "jsdom"` rather than node: the persistence module talks to
  // localStorage, and the behaviour worth testing there is what happens when a
  // browser refuses it -- which cannot be exercised without one to refuse.
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API, changeOrigin: false },
    },
  },
});
