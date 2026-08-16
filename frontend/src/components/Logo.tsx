/**
 * The Concordance mark.
 *
 * Two record-lines held in alignment across a single binding. That is the
 * product stated in four strokes: a requirement on one side, the model object
 * on the other, and the fingerprint that binds them -- the same shape as Power
 * BI against a warehouse, and as one model version against the next.
 *
 * It also lands, deliberately, near the double dagger of scholarly apparatus.
 * A concordance is the reference that binds every word to the place it occurs,
 * which is what this tool does for every sentence in a requirements document.
 *
 * Drawn rather than imported. The page is inlined into one file served by a
 * Python `http.server` that routes `/` and `/api` and nothing else, and a test
 * asserts it requests no external asset -- so an icon font or a hosted SVG
 * would both break the build and leave an air-gapped machine with a blank box.
 *
 * `currentColor` throughout, so the mark themes with its surroundings and needs
 * no light/dark variant. Alternatives were tested against the icons they could
 * be mistaken for: a ring-and-chord reads as "no entry", a cross as "cancel",
 * and a spine-with-rules as text-align -- all fatal for a mark that has to mean
 * something else at 16px.
 */
export function Mark({
  size = 20,
  className,
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      // Thickened below ~20px: strokes that read as hairlines at header size
      // disappear entirely in a favicon.
      strokeWidth={size < 20 ? 2.4 : 1.9}
      strokeLinecap="round"
      className={className}
      // Decorative here: every place it appears is beside the word
      // "Concordance", so announcing it again would just repeat the name.
      aria-hidden="true"
      focusable="false"
    >
      <path d="M3.5 8.5h7" />
      <path d="M13.5 8.5h7" />
      <path d="M3.5 15.5h7" />
      <path d="M13.5 15.5h7" />
      <path d="M12 4.5v15" />
    </svg>
  );
}

/**
 * Mark plus name, the way the product signs itself.
 *
 * The serif is doing real work: this is a tool whose output is a signed
 * document, and a geometric sans wordmark would place it beside developer
 * tooling rather than beside the requirements specification it produces.
 */
export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={`flex items-center gap-2 ${className ?? ""}`}>
      <Mark size={18} className="flex-none text-accent" />
      <span className="font-serif text-sm font-semibold tracking-[-0.01em]">
        Concordance
      </span>
    </span>
  );
}

/**
 * The same mark as a data URI, for the browser tab.
 *
 * Inline rather than a served file for the reason above -- one self-contained
 * page, no second request. `%23` is a literal `#`: unescaped, it truncates the
 * URI at the fragment and the tab silently falls back to a blank icon.
 */
export const FAVICON_SVG =
  "data:image/svg+xml," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" ' +
      'stroke="#0f6e72" stroke-width="2.4" stroke-linecap="round">' +
      '<path d="M3.5 8.5h7"/><path d="M13.5 8.5h7"/>' +
      '<path d="M3.5 15.5h7"/><path d="M13.5 15.5h7"/>' +
      '<path d="M12 4.5v15"/></svg>',
  );
