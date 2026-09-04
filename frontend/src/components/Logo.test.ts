/**
 * The mark, and the one thing about it that can silently rot.
 *
 * The tab icon exists twice by necessity: once in `index.html`, so the tab is
 * right on the very first paint, and once in `Logo.tsx`, which is where the
 * running app sets it. Nothing makes those two agree, and they had already
 * stopped agreeing -- the template held a teal square with three lines while
 * the app held a different drawing entirely, so the icon visibly changed the
 * moment React booted. That is the failure this file exists to catch, because
 * it is invisible in review and nobody is watching the tab.
 */

import { describe, expect, it } from "vitest";
import { FAVICON_MARKUP, FAVICON_SVG } from "./Logo";
// The template as text, through Vite's `?raw` rather than through `node:fs`.
// The app's TypeScript config admits only `vite/client` types, deliberately, so
// that browser code cannot reach for Node APIs -- and a test living under
// `src/` is checked by that same config. `?raw` is declared by `vite/client`,
// so this is the one route that both runs and type-checks here.
import indexHtml from "../../index.html?raw";

/** The favicon the browser gets before any JavaScript has run. */
function faviconFromTemplate(): string {
  const match = indexHtml.match(
    /<link rel="icon"[^>]*href="data:image\/svg\+xml;base64,([^"]+)"/,
  );
  if (!match) throw new Error("index.html has no inline base64 favicon");
  // `atob` rather than `Buffer`, for the same config reason as above. The
  // markup is pure ASCII, so the binary string it returns is the text.
  return atob(match[1]);
}

describe("the tab icon", () => {
  it("is the same drawing before and after the app boots", () => {
    expect(faviconFromTemplate()).toBe(FAVICON_MARKUP);
  });

  it("is a real SVG, not a truncated one", () => {
    const svg = faviconFromTemplate();
    expect(svg.startsWith("<svg")).toBe(true);
    expect(svg.endsWith("</svg>")).toBe(true);
    expect(svg).toContain('xmlns="http://www.w3.org/2000/svg"');
  });

  it("carries all three strokes of the mark", () => {
    // Two record cards and the tie across them. A mark missing the tie is two
    // unrelated shapes, which is the opposite of what the product does.
    expect(faviconFromTemplate().match(/<path /g)).toHaveLength(3);
  });

  it("paints its own background, because a tab has none to inherit", () => {
    // `currentColor` is right everywhere else and wrong here: a favicon has no
    // inherited colour, and thin accent strokes lose against browser chrome in
    // both themes.
    const svg = faviconFromTemplate();
    expect(svg).toContain("#0f6e72");
    expect(svg).not.toContain("currentColor");
  });
});

describe("the favicon data URI", () => {
  it("escapes the hash, or the tab falls back to a blank icon", () => {
    // An unescaped `#` truncates the URI at the fragment. The colour is the
    // only hash in the markup, so this is the whole risk.
    expect(FAVICON_SVG).not.toContain("#");
    expect(FAVICON_SVG).toContain("%230f6e72");
  });

  it("declares the right media type", () => {
    expect(FAVICON_SVG.startsWith("data:image/svg+xml,")).toBe(true);
  });

  it("round-trips back to the markup it was built from", () => {
    const decoded = decodeURIComponent(
      FAVICON_SVG.slice("data:image/svg+xml,".length),
    );
    expect(decoded).toBe(FAVICON_MARKUP);
  });
});

describe("the mark requests nothing from the network", () => {
  it("has no external reference in the template", () => {
    // The server routes `/` and `/api` and nothing else, so any external or
    // absolute asset reference is a blank box on an air-gapped machine.
    expect(indexHtml).not.toMatch(/href="https?:/);
    expect(indexHtml).not.toMatch(/src="https?:/);
  });
});
