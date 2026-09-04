/**
 * The Concordance mark.
 *
 * Two records, side by side, tied across the gap between them. That is the
 * product stated in three strokes: a requirement on one side, the model object
 * on the other, and the fingerprint that binds them -- the same shape as Power
 * BI against a warehouse, and as one model version against the next. Read the
 * other way it is a book lying open, which is what a concordance literally is:
 * the reference that binds every word to the place it occurs.
 *
 * It replaced a mark built from five hairline strokes -- four dashes flanking a
 * spine. That version was right about the *idea* and wrong about the drawing:
 * with no enclosed shape it had no silhouette, so at 16px it collapsed into a
 * smudge, and at every size it read as the text-align control it happened to
 * resemble. Rendering the candidates side by side at 16/20/32/64 in both themes
 * is what settled it; enclosed cards survive a favicon, thin strokes do not.
 * Alternatives were checked against what they could be mistaken for: a
 * ring-and-chord reads as "no entry", a cross as "cancel", and a bracketed bar
 * as a "remove" button -- all fatal for a mark that has to mean something else
 * at 16px.
 *
 * Drawn rather than imported. The page is inlined into one file served by a
 * Python `http.server` that routes `/` and `/api` and nothing else, so an icon
 * font or a hosted SVG would leave an air-gapped machine with a blank box.
 *
 * `currentColor` throughout, so the mark themes with its surroundings and needs
 * no light/dark variant.
 */

//: The mark itself, in a 24x24 grid, shared by every rendering of it below.
//: One definition is the whole point: the tab icon and the header mark drifted
//: into two different drawings once already, and the copy nobody looks at --
//: the one in the tab -- is always the one that goes stale.
const CARD_LEFT = "M10 4.4H6.8A2.4 2.4 0 0 0 4.4 6.8v10.4a2.4 2.4 0 0 0 2.4 2.4H10V4.4Z";
const CARD_RIGHT = "M14 4.4h3.2a2.4 2.4 0 0 1 2.4 2.4v10.4a2.4 2.4 0 0 1-2.4 2.4H14V4.4Z";
const TIE = "M10.2 12h3.6";

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
      // Thickened below ~20px: a stroke that reads as a hairline at header size
      // closes up against the tie entirely at favicon size.
      strokeWidth={size < 20 ? 2.3 : 1.9}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      // Decorative here: every place it appears is beside the word
      // "Concordance", so announcing it again would just repeat the name.
      aria-hidden="true"
      focusable="false"
    >
      <path d={CARD_LEFT} />
      <path d={CARD_RIGHT} />
      <path d={TIE} />
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
 * The same mark for the browser tab, knocked out of a filled badge.
 *
 * A tab is the one place the mark cannot control its background: it sits in
 * browser chrome that is light in one theme and near-black in another, at
 * 16px, beside a dozen other tabs. Thin strokes in an accent colour lose that
 * fight in both directions, so the tab gets the solid-badge treatment -- the
 * same geometry, inset in a teal square and drawn in the paper colour. The
 * badge is also why this one carries literal hex rather than `currentColor`:
 * a favicon has no inherited colour to take.
 *
 * `translate(4 4)` insets the 24-grid mark inside a 32-grid badge, which is the
 * padding that keeps it clear of the rounded corners.
 */
export const FAVICON_MARKUP =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">' +
  '<rect width="32" height="32" rx="7" fill="#0f6e72"/>' +
  '<g transform="translate(4 4)" fill="none" stroke="#fcfcfd" stroke-width="2.5" ' +
  'stroke-linecap="round" stroke-linejoin="round">' +
  `<path d="${CARD_LEFT}"/><path d="${CARD_RIGHT}"/><path d="${TIE}"/>` +
  "</g></svg>";

/**
 * That markup as a data URI.
 *
 * Inline rather than a served file, for the same reason the mark is drawn
 * rather than imported -- one self-contained page, no second request. `%23` is
 * a literal `#`: unescaped, it truncates the URI at the fragment and the tab
 * silently falls back to a blank icon.
 */
export const FAVICON_SVG =
  "data:image/svg+xml," + encodeURIComponent(FAVICON_MARKUP);
