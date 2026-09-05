/**
 * The three charts a dashboard is made of, drawn from the model's own numbers.
 *
 * Hand-drawn SVG rather than a charting library, for the same reason the rest
 * of this project has no runtime dependencies it does not need: a chart here
 * has exactly three jobs -- a ring, a row of bars, a row of columns -- and a
 * library would bring a theme system that fights this one and a tooltip layer
 * that fights the keyboard.
 *
 * One rule runs through all three, and it is the reason they look the way they
 * do. **Colour never carries identity.** This project measured its own status
 * colours under deuteranopia and found them 3.6 ΔE apart, and a categorical
 * palette of eight hues would be far worse: a reader who cannot separate two
 * of them cannot read the chart at all. So every slice is labelled in text,
 * and the fill is a single-hue ramp that encodes *rank* -- darkest is largest
 * -- which is information the labels already carry and the colour merely
 * repeats. Nothing is lost by not seeing it.
 */

import { useId, useRef, useState } from "react";
import { cx } from "@/lib/cx";
import type { Slice } from "@/lib/api";

/** Bars this many or fewer read as a ring; more, and the thin slices vanish. */
export const RING_MAX = 6;

/** Above this, a label will not sit under a column and the split wants bars. */
export const LABEL_MAX = 11;

/**
 * Which of the three a split should be drawn as.
 *
 * A ring is parts of a whole, so a measure that goes both ways -- a variance,
 * a delta -- is never one: a negative arc is not a smaller share of anything,
 * and drawing its magnitude would state the opposite of what the number says.
 */
/** How the reader has asked for the groups to be arranged. */
export type Order = "largest" | "smallest" | "label" | "time";

/**
 * The groups, arranged.
 *
 * `time` is the one that needs the data's help: the labels alone cannot be put
 * in date order, because sorting `Jan, Feb, Mar` as text puts April first. Each
 * slice carries the earliest date its group actually occupies, and that is what
 * this sorts on. A slice with no such date -- the folded "N more", which is
 * several groups at several times -- goes last, which is the only honest place
 * for it.
 */
export function arrange(slices: Slice[], order: Order): Slice[] {
  const out = [...slices];
  if (order === "largest") return out.sort((a, b) => b.value - a.value);
  if (order === "smallest") return out.sort((a, b) => a.value - b.value);
  if (order === "label") {
    return out.sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }));
  }
  return out.sort((a, b) => {
    if (!a.order) return 1;
    if (!b.order) return -1;
    return a.order.localeCompare(b.order);
  });
}

/** True when every group says where it sits in time, so `time` is offerable. */
export function canOrderByTime(slices: Slice[]): boolean {
  const dated = slices.filter((s) => s.order);
  // The folded slice never has one, so "all but at most the fold" is the test.
  return dated.length >= slices.length - 1 && dated.length >= 2;
}

/** Words that mean the split is a sequence rather than a set of parts. */
const TEMPORAL = /\b(year|quarter|month|week|day|date|period|fiscal)\b/i;

/**
 * True when a column name names a period.
 *
 * The camelCase split is not a nicety: Store Sales' column is `FiscalYear`,
 * and a word-boundary rule reads that as one word and misses it. Splitting
 * before an interior capital is also what keeps the rule from matching
 * `Yearbook`, which a plain substring test would.
 */
export function namesAPeriod(column: string): boolean {
  return TEMPORAL.test(column.replace(/([a-z0-9])([A-Z])/g, "$1 $2"));
}

export function chartFor(
  slices: Slice[],
  additive = true,
  column = "",
): "donut" | "columns" | "bars" {
  const signed = slices.some((s) => s.value < 0);
  // A period is a sequence, and a ring has no beginning: the reader's question
  // about months is "which way is it going", which a ring cannot answer at any
  // size. Unlike everything else here this reads a name, which is safe because
  // only the chart's shape rides on it -- no claim about the numbers does.
  //
  // What this does *not* do is put the periods in order. Power BI stores that
  // in a sort-by column, and the reader this project uses does not expose one,
  // so "Jan, Feb, Mar" is a set of strings here and sorting them would give
  // April first. The columns stay ranked by size like every other chart, and
  // the panel says so rather than letting the shape imply a chronology.
  const ordered = namesAPeriod(column);
  if (ordered) {
    // Never a ring once it is a sequence -- bars keep the order too, and are
    // where a period with long labels has to land.
    return slices.every((s) => s.label.length <= LABEL_MAX) ? "columns" : "bars";
  }
  // A ring says "these are the parts of that whole". For an average or a ratio
  // that sentence is false however good the chart looks: the two chains'
  // average store sizes are a fair comparison and their sum is not a size.
  if (additive && !signed && slices.length <= RING_MAX) return "donut";
  if (!signed && slices.every((s) => s.label.length <= LABEL_MAX)) return "columns";
  return "bars";
}

/**
 * Rank, as opacity on the accent. Six steps, floored well above invisible so
 * the smallest slice is still a shape on both grounds.
 */
function shade(rank: number, total: number): number {
  if (total <= 1) return 1;
  const step = (1 - 0.34) / Math.max(total - 1, 1);
  return 1 - step * rank;
}

/** How many groups are picked out in the accent colour; the rest go quiet. */
export const LEADERS = 3;

/**
 * Each group's rank by value, regardless of the order it is drawn in.
 *
 * Rank and position are the same thing only while the chart is sorted by size.
 * Put the months in date order and they part company, and picking out "the
 * first three drawn" would then highlight January, February and March for no
 * reason. The three *largest* stay the three largest however the chart is
 * arranged, which is the thing worth pointing at.
 */
export function ranks(slices: Slice[]): number[] {
  const order = slices
    .map((slice, at) => ({ at, value: Math.abs(slice.value) }))
    .sort((a, b) => b.value - a.value);
  const out = new Array<number>(slices.length);
  order.forEach((entry, rank) => {
    out[entry.at] = rank;
  });
  return out;
}

/** Magnitude, short enough to sit inside a chart. */
export function brief(value: number): string {
  const size = Math.abs(value);
  if (size === 0) return "0";
  if (size >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`;
  if (size >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (size >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  if (size >= 1) return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
  return Number(value.toPrecision(2)).toString();
}

/** The full figure, for the readout under a chart where there is room for it. */
export function exact(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function share(value: number, total: number): string {
  if (!total) return "";
  return `${((value / total) * 100).toFixed(1)}%`;
}

/**
 * What a hovered or focused slice reads out.
 *
 * A fixed line under every chart rather than a floating tooltip: a tooltip
 * that follows the pointer is unreachable by keyboard and invisible in a
 * screenshot, and this page is one people screenshot into a review pack.
 */
function Readout({
  slice,
  total,
  fallback,
}: {
  slice: Slice | null;
  total: number;
  fallback: string;
}) {
  return (
    <p className="min-h-[1.4em] text-[11.5px] text-muted tabular" aria-live="polite">
      {slice ? (
        <>
          <span className="font-medium text-ink">{slice.label}</span> — {exact(slice.value)}
          {total ? ` · ${share(slice.value, total)} of the total` : ""}
        </>
      ) : (
        fallback
      )}
    </p>
  );
}

interface ChartProps {
  slices: Slice[];
  /** The dimension being split by, for the accessible description. */
  by: string;
  measure: string;
  /** Whether the parts sum to a whole. When they do not, a share of the total
   *  is not a real quantity and the readout does not offer one. */
  additive?: boolean;
  /** Called with a group's label when it is clicked.
   *
   *  This is the cross-filter, and it is a query rather than a highlight: the
   *  caller re-runs every chart on the page with that group held, so the other
   *  panels show the filtered figures. Fading the bars that did not match --
   *  the usual shortcut -- leaves the numbers unfiltered while the page
   *  implies they are not, and across two different dimensions it fades
   *  everything, because a product name is never a store name. */
  onPick?: (label: string) => void;
  /** The group currently held, so it can be marked as the reason. */
  picked?: string | null;
}

/**
 * A ring, for a split into a handful of parts.
 *
 * The hole is not decoration: it is where the total goes, so the part and the
 * whole are readable without moving the eye off the chart.
 */
export function Donut({ slices, by, measure, onPick, picked }: ChartProps) {
  const [active, setActive] = useState<number | null>(null);
  const total = slices.reduce((sum, s) => sum + Math.abs(s.value), 0);
  const titleId = useId();

  const R = 62;
  const STROKE = 26;
  const circumference = 2 * Math.PI * R;
  let travelled = 0;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
        <svg
          viewBox="0 0 160 160"
          className="h-[150px] w-[150px] shrink-0"
          role="img"
          aria-labelledby={titleId}
        >
          <title id={titleId}>
            {measure} by {by}, as a ring
          </title>
          <g transform="translate(80 80) rotate(-90)">
            {slices.map((slice, at) => {
              const length = total ? (Math.abs(slice.value) / total) * circumference : 0;
              const offset = -travelled;
              travelled += length;
              return (
                <circle
                  key={slice.label}
                  r={R}
                  fill="none"
                  stroke="var(--color-accent)"
                  strokeWidth={active === at ? STROKE + 6 : STROKE}
                  strokeOpacity={shade(at, slices.length)}
                  strokeDasharray={`${length} ${circumference - length}`}
                  strokeDashoffset={offset}
                  className="cursor-pointer transition-[stroke-width] duration-150"
                  onMouseEnter={() => setActive(at)}
                  onMouseLeave={() => setActive(null)}
                  onClick={() => onPick?.(slice.label)}
                />
              );
            })}
          </g>
          <text
            x="80"
            y="76"
            textAnchor="middle"
            className="fill-ink font-semibold tabular"
            fontSize="17"
          >
            {brief(active === null ? total : slices[active].value)}
          </text>
          <text
            x="80"
            y="93"
            textAnchor="middle"
            className="fill-faint"
            fontSize="9.5"
          >
            {active === null ? "total" : share(slices[active].value, total)}
          </text>
        </svg>

        {/* The legend is not a key to the colours -- it is the chart's data in
            words, which is what makes the colours optional. */}
        <ul className="flex min-w-[9rem] flex-col gap-1">
          {slices.map((slice, at) => (
            <li key={slice.label}>
              <button
                type="button"
                onMouseEnter={() => setActive(at)}
                onMouseLeave={() => setActive(null)}
                onFocus={() => setActive(at)}
                onBlur={() => setActive(null)}
                onClick={() => onPick?.(slice.label)}
                aria-pressed={picked === slice.label}
                className={cx(
                  "flex w-full items-center gap-2 rounded px-1 py-0.5 text-left text-[11.5px]",
                  active === at ? "bg-raised" : "",
                )}
              >
                <span
                  aria-hidden
                  className="h-2.5 w-2.5 shrink-0 rounded-[2px] bg-accent"
                  style={{ opacity: shade(at, slices.length) }}
                />
                <span className="min-w-0 flex-1 truncate">{slice.label}</span>
                <span className="shrink-0 tabular text-muted">{brief(slice.value)}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <Readout
        slice={active === null ? null : slices[active]}
        total={total}
        fallback="Point at a slice for its exact figure."
      />
    </div>
  );
}

/**
 * Horizontal bars, for a split with more parts than a ring can hold, or with
 * labels longer than a ring can sit next to.
 *
 * Rows rather than an SVG: a category name is text of unpredictable length,
 * and letting the browser lay it out is what keeps a nine-word district name
 * from being clipped or from squeezing every bar to nothing.
 */
export function Bars({
  slices,
  by,
  measure,
  additive = true,
  onPick,
  picked,
}: ChartProps) {
  const [active, setActive] = useState<number | null>(null);
  const rank = ranks(slices);
  const total = slices.reduce((sum, s) => sum + Math.abs(s.value), 0);
  // Scaled against the largest bar, not against the total: a chart where the
  // biggest bar fills a third of the track wastes the two-thirds that would
  // have made the differences visible.
  const widest = Math.max(...slices.map((s) => Math.abs(s.value)), 1);
  // A measure like a variance goes both ways. When it does, zero sits in the
  // middle of the track and the sign is a direction rather than a minus sign
  // the reader has to notice.
  const signed = slices.some((s) => s.value < 0);

  return (
    <div className="flex flex-col gap-2">
      <ul
        className="flex flex-col gap-1"
        aria-label={`${measure} by ${by}, as bars`}
      >
        {slices.map((slice, at) => {
          const extent = (Math.abs(slice.value) / widest) * (signed ? 50 : 100);
          return (
            <li
              key={slice.label}
              className={cx(
                "flex items-center gap-2 rounded px-1 py-[3px] text-[11.5px]",
                active === at ? "bg-raised" : "",
                picked === slice.label ? "ring-1 ring-accent" : "",
                onPick ? "cursor-pointer" : "",
              )}
              onMouseEnter={() => setActive(at)}
              onMouseLeave={() => setActive(null)}
              onClick={() => onPick?.(slice.label)}
            >
              <span className="w-[7.5rem] shrink-0 truncate" title={slice.label}>
                {slice.label}
              </span>
              <span className="relative h-3.5 min-w-0 flex-1 rounded-[2px] bg-hairline">
                <span
                  className={cx(
                    "absolute inset-y-0 rounded-[2px] transition-[width] duration-200",
                    rank[at] < LEADERS ? "bg-accent" : "bg-edge",
                  )}
                  style={{
                    width: `${extent}%`,
                    left: signed ? (slice.value < 0 ? `${50 - extent}%` : "50%") : 0,
                    opacity: rank[at] < LEADERS ? shade(rank[at], LEADERS) : 1,
                  }}
                />
                {signed && (
                  <span
                    aria-hidden
                    className="absolute inset-y-[-2px] left-1/2 w-px bg-edge"
                  />
                )}
              </span>
              <span className="w-[4.5rem] shrink-0 text-right tabular text-muted">
                {brief(slice.value)}
              </span>
            </li>
          );
        })}
      </ul>
      <Readout
        slice={active === null ? null : slices[active]}
        total={signed || !additive ? 0 : total}
        fallback={
          signed
            ? "This measure goes both ways, so bars run from the centre line."
            : "Point at a bar for its exact figure."
        }
      />
    </div>
  );
}

/**
 * Vertical columns, for a split with more parts than a ring can hold whose
 * labels are all short enough to sit under one.
 *
 * The alternative for the same data is `Bars`, and the difference is purely
 * how much room a label needs: `020-Mens` fits under a column and
 * `Fashions Direct` does not. Picking between them by measuring the labels is
 * what stops a chart from being unreadable in one model and fine in the next.
 */
export function Columns({
  slices,
  by,
  measure,
  additive = true,
  onPick,
  picked,
}: ChartProps) {
  const [active, setActive] = useState<number | null>(null);
  const rank = ranks(slices);
  const total = slices.reduce((sum, s) => sum + Math.abs(s.value), 0);
  const tallest = Math.max(...slices.map((s) => Math.abs(s.value)), 1);

  return (
    <div className="flex flex-col gap-2">
      <div
        className="flex h-[150px] items-end gap-1.5"
        aria-label={`${measure} by ${by}, as columns`}
      >
        {slices.map((slice, at) => (
          <button
            key={slice.label}
            type="button"
            onMouseEnter={() => setActive(at)}
            onMouseLeave={() => setActive(null)}
            onFocus={() => setActive(at)}
            onBlur={() => setActive(null)}
            onClick={() => onPick?.(slice.label)}
            aria-pressed={picked === slice.label}
            className="flex h-full min-w-0 flex-1 flex-col justify-end gap-1"
            title={`${slice.label}: ${exact(slice.value)}`}
          >
            <span
              className={cx(
                "text-center text-[9.5px] tabular",
                active === at ? "text-ink" : "text-faint",
              )}
            >
              {brief(slice.value)}
            </span>
            <span
              className={cx(
                "w-full rounded-t-[2px] transition-[height] duration-200",
                rank[at] < LEADERS ? "bg-accent" : "bg-edge",
              )}
              style={{
                height: `${(Math.abs(slice.value) / tallest) * 100}%`,
                opacity:
                  active === at || rank[at] >= LEADERS ? 1 : shade(rank[at], LEADERS),
              }}
            />
            <span className="truncate text-center text-[9.5px] text-muted">
              {slice.label}
            </span>
          </button>
        ))}
      </div>
      <Readout
        slice={active === null ? null : slices[active]}
        total={additive ? total : 0}
        fallback="Point at a column for its exact figure."
      />
    </div>
  );
}


/**
 * The same split as a table: label, figure, share, and a bar in the cell.
 *
 * Every panel offers this beside the chart, because the two answer different
 * questions and a dashboard that only draws forces the second one out to a
 * tooltip. A chart answers "which is bigger"; a table answers "what is it,
 * exactly" -- and this is a documentation tool, where the exact figure is
 * frequently the whole point.
 *
 * The total row appears only where the parts genuinely sum to a whole. Under a
 * measure that does not add up -- an average, a ratio -- a total row would be
 * a number the model does not contain, printed in the most authoritative place
 * on the table.
 */
export function Tabular({
  slices,
  by,
  measure,
  additive = true,
  onPick,
  picked,
}: ChartProps) {
  const rank = ranks(slices);
  const widest = Math.max(...slices.map((s) => Math.abs(s.value)), 1);
  const total = slices.reduce((sum, s) => sum + s.value, 0);

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[11.5px]">
        <caption className="sr-only">
          {measure} by {by}
        </caption>
        <thead>
          <tr className="border-b border-edge text-faint">
            <th scope="col" className="py-1 pr-2 text-left font-medium">
              {by}
            </th>
            <th scope="col" className="py-1 pr-2 text-right font-medium">
              {measure}
            </th>
            <th scope="col" className="w-[38%] py-1 text-left font-medium">
              <span className="sr-only">Relative size</span>
            </th>
            {additive && (
              <th scope="col" className="py-1 pl-2 text-right font-medium">
                Share
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {slices.map((slice, at) => (
            <tr
              key={slice.label}
              className={cx(
                "border-b border-hairline",
                onPick ? "cursor-pointer hover:bg-raised" : "",
                picked === slice.label ? "bg-raised font-medium" : "",
              )}
              onClick={() => onPick?.(slice.label)}
            >
              <td className="max-w-[10rem] truncate py-1 pr-2" title={slice.label}>
                {slice.label}
              </td>
              <td className="py-1 pr-2 text-right tabular">{exact(slice.value)}</td>
              <td className="py-1">
                <span className="block h-2.5 rounded-[2px] bg-hairline">
                  <span
                    className={cx(
                      "block h-full rounded-[2px]",
                      rank[at] < LEADERS ? "bg-accent" : "bg-edge",
                    )}
                    style={{ width: `${(Math.abs(slice.value) / widest) * 100}%` }}
                  />
                </span>
              </td>
              {additive && (
                <td className="py-1 pl-2 text-right tabular text-muted">
                  {share(slice.value, total) || "—"}
                </td>
              )}
            </tr>
          ))}
        </tbody>
        {additive && (
          <tfoot>
            <tr className="font-semibold text-ink">
              <td className="py-1.5 pr-2">Total</td>
              <td className="py-1.5 pr-2 text-right tabular">{exact(total)}</td>
              <td />
              <td className="py-1.5 pl-2 text-right tabular">100%</td>
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

/**
 * Nice round numbers for a coordinate grid.
 *
 * Degrees, coarse to fine. A graticule is only legible when its lines fall on
 * numbers a reader recognises -- 41.8, 41.9 -- so the step is picked from this
 * list rather than computed as span/5, which produces lines at 41.8437.
 */
const GRID_STEPS = [
  30, 10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01, 0.005, 0.002, 0.001,
];

/** The coarsest step that still puts at least `least` lines across `span`. */
export function gridStep(span: number, least = 3): number {
  for (const step of GRID_STEPS) if (span / step >= least) return step;
  return GRID_STEPS[GRID_STEPS.length - 1];
}

/**
 * Degrees written the way a map writes them.
 *
 * The decimal count comes from `step`, the spacing of the grid, and not from
 * the size of the number -- which is the version this replaced, and it was
 * wrong in a way that only shows up on a real map. Rounding by magnitude gave
 * 41.85 and 41.90 one decimal each, so two adjacent grid lines both read
 * "41.9°N" and the grid claimed the same latitude twice.
 */
export function degrees(value: number, axis: "lat" | "lon", step = 0.01): string {
  const hemisphere = axis === "lat" ? (value < 0 ? "S" : "N") : value < 0 ? "W" : "E";
  const places = Math.min(4, Math.max(0, Math.ceil(-Math.log10(step) + 1e-9)));
  const size = Math.abs(value).toFixed(places);
  // No trailing zeros, but never strip the whole fractional part of an integer.
  const trimmed = places > 0 ? size.replace(/\.?0+$/, "") : size;
  return `${trimmed}\u00b0${hemisphere}`;
}

//: The tile size every web basemap uses, in pixels.
export const TILE = 256;

//: The deepest zoom the basemap has tiles for. Past this the server returns
//: nothing and the map goes blank at the moment it is most detailed.
export const MAX_ZOOM = 18;

/**
 * A measure over time, as a line.
 *
 * The shape a time series wants. Columns say "these are separate quantities,
 * compare them"; a line says "this is one quantity, and here is what it did",
 * which is the actual question asked of a trend. The by-month cut was drawn as
 * columns until this existed, which read as twelve unrelated bars.
 *
 * The crosshair reads every series at the hovered point rather than the one
 * nearest the cursor -- with two lines on the chart, "what were both of these
 * in March" is the question, and hunting for the second one is not an answer.
 */
export function Trend({
  slices,
  measure,
  height = 190,
  onPick,
}: {
  slices: Slice[];
  measure: string;
  height?: number;
  /** Called with a point's label when it is clicked, for cross-filtering. */
  onPick?: (label: string) => void;
}) {
  const [at, setAt] = useState<number | null>(null);
  const frame = useRef<SVGSVGElement>(null);
  const titleId = useId();

  // Wide, because this is drawn across a whole page. A narrow viewBox
  // stretched to full width is scaled up in *both* directions, and the chart
  // came out half a screen tall.
  const W = 680;
  const H = height;
  const PAD = { top: 14, right: 14, bottom: 26, left: 52 };
  const plot = { width: W - PAD.left - PAD.right, height: H - PAD.top - PAD.bottom };

  const values = slices.map((s) => s.value);
  // Zero-based unless the data goes below it. A trend line drawn from its own
  // minimum exaggerates every wobble into a cliff, which is the most common
  // way a truthful series tells a lie.
  const low = Math.min(0, ...values);
  const high = Math.max(...values, low + 1);
  const x = (i: number) =>
    PAD.left + (slices.length > 1 ? (plot.width / (slices.length - 1)) * i : plot.width / 2);
  const y = (v: number) => PAD.top + plot.height - ((v - low) / (high - low)) * plot.height;

  const path = slices.map((s, i) => `${i ? "L" : "M"}${x(i)},${y(s.value)}`).join("");
  const under = `${path}L${x(slices.length - 1)},${y(low)}L${x(0)},${y(low)}Z`;

  const move = (event: React.MouseEvent<SVGSVGElement>) => {
    const box = frame.current?.getBoundingClientRect();
    if (!box) return;
    const across = ((event.clientX - box.left) / box.width) * W;
    const step = slices.length > 1 ? plot.width / (slices.length - 1) : plot.width;
    const found = Math.round((across - PAD.left) / step);
    setAt(Math.max(0, Math.min(slices.length - 1, found)));
  };

  // Three gridlines, on the same round-number rule the axes elsewhere use.
  const marks = [low, (low + high) / 2, high];
  // Every label if they fit, else every nth -- a crowded axis is less
  // readable than a sparse one.
  const room = Math.max(1, Math.ceil((slices.length * 44) / plot.width));
  // Which ones actually get drawn. The last is worth having, being the most
  // recent period, but only when there is room for it: taking it
  // unconditionally printed it on top of its neighbour, and thirty-six months
  // came out ending "Apr2020Jul2020".
  const ticks = new Set<number>();
  for (let i = 0; i < slices.length; i += room) ticks.add(i);
  const last = slices.length - 1;
  if (last - Math.max(...ticks) >= room) ticks.add(last);

  return (
    <div className="flex flex-col gap-1.5">
      <svg
        ref={frame}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-labelledby={titleId}
        onMouseMove={move}
        onMouseLeave={() => setAt(null)}
      >
        <title id={titleId}>
          {measure} over {slices.length} periods
        </title>
        {marks.map((mark) => (
          <g key={mark}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={y(mark)}
              y2={y(mark)}
              className="stroke-hairline"
              strokeWidth="0.6"
            />
            <text
              x={PAD.left - 6}
              y={y(mark) + 3}
              textAnchor="end"
              fontSize="9"
              className="fill-faint tabular"
            >
              {brief(mark)}
            </text>
          </g>
        ))}

        <path d={under} className="fill-accent" fillOpacity="0.1" />
        <path
          d={path}
          fill="none"
          className="stroke-accent"
          strokeWidth="1.8"
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {at !== null && (
          <line
            x1={x(at)}
            x2={x(at)}
            y1={PAD.top}
            y2={PAD.top + plot.height}
            className="stroke-edge"
            strokeWidth="0.8"
          />
        )}

        {slices.map((slice, i) => (
          <circle
            key={slice.label}
            cx={x(i)}
            cy={y(slice.value)}
            r={at === i ? 4 : 2.4}
            className={cx("fill-accent", onPick && "cursor-pointer")}
            onClick={() => onPick?.(slice.label)}
          />
        ))}

        {slices.map((slice, i) =>
          ticks.has(i) ? (
            <text
              key={`${slice.label}-tick`}
              x={x(i)}
              y={H - 8}
              // The end labels are anchored inward. Centred, the last one
              // overhangs the viewBox and is clipped -- "Jun 2019" rendered
              // as "Jun 201".
              textAnchor={
                i === 0 ? "start" : i === slices.length - 1 ? "end" : "middle"
              }
              fontSize="9"
              className="fill-faint"
            >
              {slice.label}
            </text>
          ) : null,
        )}
      </svg>
      <Readout
        slice={at === null ? null : slices[at]}
        total={0}
        fallback={`${slices.length} periods, earliest first. Point at one for its figure.`}
      />
    </div>
  );
}

/**
 * The same series again, small enough to sit under a figure.
 *
 * No axes, no labels, no readout -- a card is a number with a shape under it,
 * and anything more competes with the number. It is still the measure's own
 * query, so the shape is load-bearing rather than decorative.
 */
export function Sparkline({
  values,
  height = 26,
}: {
  values: number[];
  height?: number;
}) {
  if (values.length < 2) return null;

  const W = 120;
  const H = height;
  const low = Math.min(0, ...values);
  const high = Math.max(...values, low + 1);
  const x = (i: number) => (W / (values.length - 1)) * i;
  const y = (v: number) => H - 2 - ((v - low) / (high - low)) * (H - 4);
  const path = values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("");

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full"
      preserveAspectRatio="none"
      // Decorative: the figure above it is the content, and a screen reader
      // reading out forty numbers instead is worse than silence.
      aria-hidden="true"
      focusable="false"
    >
      <path d={`${path}L${x(values.length - 1)},${H}L0,${H}Z`} className="fill-accent" fillOpacity="0.12" />
      <path d={path} fill="none" className="stroke-accent" strokeWidth="1.3" strokeLinejoin="round" />
      <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r="1.9" className="fill-accent" />
    </svg>
  );
}

/** Longitude as a fraction across the world, 0 at the antimeridian to 1. */
export function mercatorX(lon: number): number {
  return (lon + 180) / 360;
}

/**
 * Latitude as a fraction down the world, 0 at the north edge to 1.
 *
 * Web Mercator, which is the projection every tile server draws in -- so this
 * is not a stylistic choice. Placed with the flat latitude-as-y projection
 * this component used before, the points would sit beside the basemap rather
 * than on it, and the error grows with distance from the equator.
 */
export function mercatorY(lat: number): number {
  // Clamped to the square the projection covers. Mercator sends the poles to
  // infinity, so an unclamped point near one is drawn outside the world.
  const held = Math.max(-85.05112878, Math.min(85.05112878, lat));
  const sin = Math.sin((held * Math.PI) / 180);
  const fraction = 0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI);
  // And clamped again on the way out. The clamp above is in degrees, and the
  // arithmetic after it lands a few parts in a trillion outside the world at
  // the poles -- which is invisible as a position and fatal as a tile address:
  // `Math.floor(-6e-12)` is -1, and tile row -1 does not exist.
  return Math.min(1, Math.max(0, fraction));
}

/**
 * The zoom at which a bounding box exactly fills the drawing. Fractional.
 *
 * Fractional on purpose. Tiles are only cut at whole zooms, so the obvious
 * thing is to floor this -- and flooring throws away up to a whole doubling.
 * Measured on the sample: Chicago's thirteen stores fit at zoom 9.87, floored
 * to 9, and the map came out 105 kilometres wide to hold 44 kilometres of
 * data, with every store huddled in the middle of a mostly empty frame.
 *
 * So the fraction is kept and paid out as a scale on the tiles, which is what
 * every slippy map does between zoom steps. `tileZoom` below is the whole part
 * that addresses the tiles; the remainder is how much they are enlarged.
 */
export function fitZoom(
  bounds: { minLat: number; maxLat: number; minLon: number; maxLon: number },
  box: { width: number; height: number },
): number {
  const spanX = Math.max(mercatorX(bounds.maxLon) - mercatorX(bounds.minLon), 1e-12);
  const spanY = Math.max(mercatorY(bounds.minLat) - mercatorY(bounds.maxLat), 1e-12);
  const zoom = Math.log2(
    Math.min(box.width / (spanX * TILE), box.height / (spanY * TILE)),
  );
  return Math.max(0, Math.min(MAX_ZOOM, zoom));
}

/**
 * The whole zoom whose tiles are fetched for a fractional one.
 *
 * The level below, never the one above: enlarging a tile blurs it, and
 * shrinking one would drop detail the reader can see is missing.
 */
export function tileZoom(zoom: number): number {
  return Math.max(0, Math.min(MAX_ZOOM, Math.floor(zoom)));
}

/**
 * How much those tiles are enlarged to make up the remainder. Always in
 * `[1, 2)`, so a tile is never shrunk and never doubled twice.
 */
export function tileScale(zoom: number): number {
  return 2 ** (zoom - tileZoom(zoom));
}

/** The tile a point falls in, which is how a basemap URL is addressed. */
export function tileAt(
  lat: number,
  lon: number,
  zoom: number,
): { x: number; y: number } {
  const n = 2 ** zoom;
  // `n - 1` because a point exactly on the world's south or east edge lands on
  // fraction 1, and floor(1 * n) is one past the last tile.
  return {
    x: Math.min(n - 1, Math.max(0, Math.floor(mercatorX(lon) * n))),
    y: Math.min(n - 1, Math.max(0, Math.floor(mercatorY(lat) * n))),
  };
}

//: Where the basemap comes from. CARTO's positron, because a documentation
//: tool wants a basemap that recedes -- a full-colour street map competes with
//: the circles, which are the data. Attribution is required by both
//: OpenStreetMap (the data) and CARTO (the rendering) and is printed under it.
export const BASEMAP = "https://basemaps.cartocdn.com/light_all";

/** One tile's URL. */
export function tileUrl(zoom: number, x: number, y: number): string {
  return BASEMAP + "/" + zoom + "/" + x + "/" + y + ".png";
}

//: Bar lengths worth printing. A scale bar reading "7.4 km" is arithmetic; one
//: reading "5 km" is a measurement a reader can lay against the map.
const BAR_KM = [
  0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000,
];

/** The longest round distance that fits in `available` km. */
export function scaleBar(available: number): number {
  let best = BAR_KM[0];
  for (const km of BAR_KM) if (km <= available) best = km;
  return best;
}

/**
 * Where to put each point's name so the names do not sit on top of each other.
 *
 * Greedy, in the order given -- which is largest-value first, so when two
 * labels collide the bigger figure keeps the good position. Four candidate
 * placements around the point; a name that fits none of them is dropped rather
 * than overlapped, and is still readable by pointing at the point.
 */
export function placeLabels(
  points: { x: number; y: number; r: number; width: number }[],
  box: { width: number; height: number },
): ({ x: number; y: number; anchor: "middle" | "start" | "end" } | null)[] {
  const taken: { left: number; right: number; top: number; bottom: number }[] = [];
  const HEIGHT = 11;

  return points.map((point) => {
    const options: { x: number; y: number; anchor: "middle" | "start" | "end" }[] = [
      { x: point.x, y: point.y - point.r - 4, anchor: "middle" },
      { x: point.x, y: point.y + point.r + 10, anchor: "middle" },
      { x: point.x + point.r + 4, y: point.y + 3.5, anchor: "start" },
      { x: point.x - point.r - 4, y: point.y + 3.5, anchor: "end" },
    ];

    for (const option of options) {
      const left =
        option.anchor === "middle"
          ? option.x - point.width / 2
          : option.anchor === "start"
            ? option.x
            : option.x - point.width;
      const found = {
        left,
        right: left + point.width,
        top: option.y - HEIGHT,
        bottom: option.y + 2,
      };
      if (found.left < 2 || found.right > box.width - 2) continue;
      if (found.top < 2 || found.bottom > box.height - 2) continue;
      const clashes = taken.some(
        (other) =>
          found.left < other.right &&
          found.right > other.left &&
          found.top < other.bottom &&
          found.bottom > other.top,
      );
      if (clashes) continue;
      taken.push(found);
      return option;
    }
    return null;
  });
}

/**
 * One measure, plotted where it happened.
 *
 * Drawn as SVG from the file's own coordinates, with no tiles fetched from
 * anywhere -- this page ships as one inlined file served by a Python
 * `http.server` that routes `/` and `/api` and nothing else, so a basemap
 * request would be a blank square on an air-gapped machine.
 *
 * Which leaves the problem this component actually has to solve. Without a
 * basemap the first version was a scatter plot: circles floating in an empty
 * box, with nothing to say where on the earth they were or how far apart. A
 * coastline was the obvious fix and is the wrong one -- the public vector sets
 * small enough to inline are simplified to kilometres, and at the twenty-five
 * kilometre extent of a store list that error draws shops out in the lake. A
 * basemap that is confidently in the wrong place is worse than none.
 *
 * So the map is built from what *can* be computed exactly: a graticule on
 * round degrees, a scale bar in kilometres, north, and a size legend. Those
 * are arithmetic on the coordinates rather than a picture of the world, they
 * cannot be off by a kilometre, and between them they answer the two questions
 * an empty box could not -- where is this, and how big is it.
 *
 * The projection is the simple one: latitude and longitude scaled linearly,
 * with longitude squeezed by `cos(latitude)` so a degree of longitude is drawn
 * as the shorter distance it actually is at that latitude. Over one city that
 * is indistinguishable from a proper projection and has no constants in it to
 * get wrong.
 */
export function Atlas({
  places,
  measure,
  label,
}: {
  places: { label: string; lat: number; lon: number; value: number }[];
  measure: string;
  /** What one point is, e.g. "Store". */
  label: string;
}) {
  const [active, setActive] = useState<number | null>(null);
  // Optimistic. The basemap is the normal case, and starting without it would
  // flash a bare grid on every load before the first tile arrived.
  const [basemap, setBasemap] = useState(true);
  const titleId = useId();
  const clipId = useId();

  const W = 520;
  const H = 340;
  const PAD = { top: 18, right: 16, bottom: 26, left: 44 };
  const box = { width: W - PAD.left - PAD.right, height: H - PAD.top - PAD.bottom };

  const lats = places.map((p) => p.lat);
  const lons = places.map((p) => p.lon);
  // A margin in data units, so a point never sits on the frame. A single point
  // has no span at all, hence the floor.
  const rawLat = Math.max(...lats) - Math.min(...lats);
  const rawLon = Math.max(...lons) - Math.min(...lons);
  const bounds = {
    minLat: Math.min(...lats) - Math.max(rawLat * 0.14, 0.004),
    maxLat: Math.max(...lats) + Math.max(rawLat * 0.14, 0.004),
    minLon: Math.min(...lons) - Math.max(rawLon * 0.14, 0.004),
    maxLon: Math.max(...lons) + Math.max(rawLon * 0.14, 0.004),
  };

  // -- the projection ---------------------------------------------------------
  //
  // Web Mercator at a whole zoom level, which is what tiles are cut at. Points
  // and tiles are placed by the same two functions, so they cannot drift
  // apart: if the arithmetic is wrong the stores move with the streets.

  const zoom = fitZoom(bounds, box);
  // The whole zoom the tiles come from, and how far they are enlarged to cover
  // the fraction. Both the tiles and the points are placed through `world`, so
  // they scale together and cannot come apart.
  const whole = tileZoom(zoom);
  const stretch = tileScale(zoom);
  const size = TILE * stretch;
  const world = TILE * 2 ** whole * stretch;
  const centreX = ((mercatorX(bounds.minLon) + mercatorX(bounds.maxLon)) / 2) * world;
  const centreY = ((mercatorY(bounds.minLat) + mercatorY(bounds.maxLat)) / 2) * world;
  // The world pixel at the drawing's top-left corner.
  const left = centreX - box.width / 2;
  const top = centreY - box.height / 2;

  const x = (lon: number) => PAD.left + mercatorX(lon) * world - left;
  const y = (lat: number) => PAD.top + mercatorY(lat) * world - top;

  // Every tile the frame touches. At this size that is a handful; the loop is
  // bounded by the frame, not by the zoom.
  const tiles: { x: number; y: number; left: number; top: number }[] = [];
  const span = 2 ** whole;
  for (let tx = Math.floor(left / size); tx <= Math.floor((left + box.width) / size); tx++) {
    for (let ty = Math.floor(top / size); ty <= Math.floor((top + box.height) / size); ty++) {
      // The world wraps east to west, so a frame crossing the antimeridian
      // asks for a tile off the end of the row; it is the one on the other
      // side. Rows do not wrap, so an out-of-range y is simply not a tile.
      if (ty < 0 || ty >= span) continue;
      tiles.push({
        x: ((tx % span) + span) % span,
        y: ty,
        left: PAD.left + tx * size - left,
        top: PAD.top + ty * size - top,
      });
    }
  }

  const biggest = Math.max(...places.map((p) => Math.abs(p.value)), 1);
  // Area, not radius, carries the value: a circle of twice the radius reads as
  // four times the quantity, which would overstate every large point.
  const radius = (v: number) => 4 + Math.sqrt(Math.abs(v) / biggest) * 13;
  const rank = ranks(
    places.map((p) => ({ label: p.label, value: p.value, order: "" })),
  );

  // -- the grid, for when there is no basemap ---------------------------------
  //
  // Kept, and only drawn when the tiles do not arrive. On an air-gapped
  // machine, or behind a proxy that blocks the tile host, the map still says
  // where on the earth it is and how far across it is -- which is the whole
  // reason this was built before the basemap was.

  const midLat = (bounds.minLat + bounds.maxLat) / 2;
  const spanLat = bounds.maxLat - bounds.minLat;
  const latStep = gridStep(spanLat);
  const lonStep = gridStep(bounds.maxLon - bounds.minLon);
  const latLines: number[] = [];
  for (let at = Math.ceil(bounds.minLat / latStep) * latStep; at <= bounds.maxLat; at += latStep) {
    latLines.push(Number(at.toFixed(6)));
  }
  const lonLines: number[] = [];
  for (let at = Math.ceil(bounds.minLon / lonStep) * lonStep; at <= bounds.maxLon; at += lonStep) {
    lonLines.push(Number(at.toFixed(6)));
  }

  // -- the scale bar ----------------------------------------------------------
  //
  // Read off the projection rather than assumed, so it stays right at any
  // zoom. A Mercator pixel is a different distance at every latitude, so it is
  // measured at the middle of this map and nowhere else.
  const kmPerPixel =
    (Math.cos((midLat * Math.PI) / 180) * 40075.016686) / world;
  const km = scaleBar((box.width / 3) * kmPerPixel);
  const barPixels = km / kmPerPixel;
  const barY = H + 4;
  const barX = PAD.left;
  // The stores' own extent, not the frame's. The frame is whatever the fit
  // left over on the slack axis, and reporting it says "70 km" for thirteen
  // shops that span forty -- true of the picture, and not what was asked.
  const across =
    Math.round(
      Math.max(
        (Math.max(...lats) - Math.min(...lats)) * 111.32,
        (Math.max(...lons) - Math.min(...lons)) *
          111.32 *
          Math.cos((midLat * Math.PI) / 180),
      ) * 10,
    ) / 10;

  // -- the labels -------------------------------------------------------------

  // Largest first, so the biggest figure wins any contested position. Roughly
  // 5.6px a character at 10px, which reserves about the right room without
  // measuring text in the DOM.
  const order = places
    .map((_place, at) => at)
    .sort((a, b) => Math.abs(places[b].value) - Math.abs(places[a].value));
  const placed = placeLabels(
    order.map((at) => ({
      x: x(places[at].lon),
      y: y(places[at].lat),
      r: radius(places[at].value),
      width: places[at].label.length * 5.6,
    })),
    { width: W, height: H - PAD.bottom },
  );
  const labelAt = new Map(order.map((at, position) => [at, placed[position]]));

  return (
    <div className="flex flex-col gap-1.5">
      <svg
        viewBox={`0 0 ${W} ${H + 18}`}
        className="w-full rounded border border-hairline bg-surface"
        role="img"
        aria-labelledby={titleId}
      >
        <title id={titleId}>
          {measure} by {label}, plotted at each location&rsquo;s own coordinates
          across about {Math.round(across)} kilometres
        </title>

        <defs>
          {/* Tiles are square and the frame is not a whole number of them, so
              without this the basemap spills over the axis labels. */}
          <clipPath id={clipId}>
            <rect x={PAD.left} y={PAD.top} width={box.width} height={box.height} />
          </clipPath>
        </defs>

        <g clipPath={`url(#${clipId})`}>
          {basemap &&
            tiles.map((tile) => (
              <image
                key={`${tile.x}/${tile.y}`}
                href={tileUrl(whole, tile.x, tile.y)}
                x={tile.left}
                y={tile.top}
                width={size}
                height={size}
                // One failure takes the whole basemap down rather than leaving
                // a half-drawn world: a map with three of its nine tiles
                // missing reads as geography that is not there.
                onError={() => setBasemap(false)}
              />
            ))}

          {/* The graticule, only when the basemap did not arrive. Drawing both
              would put a coordinate grid over street names, which is what a
              map looks like when nobody decided which one it was.

              Lines only. The labels sit in the margin and belong outside this
              clip -- inside it they are cut off by the very rectangle they are
              labelling, which is how the fallback shipped a grid with no
              numbers on it. */}
          {!basemap && (
            <g className="stroke-hairline" strokeWidth="0.5">
              {latLines.map((at) => (
                <line key={`lat${at}`} x1={PAD.left} x2={W - PAD.right} y1={y(at)} y2={y(at)} />
              ))}
              {lonLines.map((at) => (
                <line key={`lon${at}`} y1={PAD.top} y2={H - PAD.bottom} x1={x(at)} x2={x(at)} />
              ))}
            </g>
          )}

          {places.map((place, at) => (
            <circle
              key={place.label}
              cx={x(place.lon)}
              cy={y(place.lat)}
              r={radius(place.value)}
              className={cx(
                "cursor-pointer transition-opacity",
                rank[at] < LEADERS ? "fill-accent" : "fill-edge",
              )}
              fillOpacity={active === null || active === at ? 0.78 : 0.35}
              stroke="var(--color-surface)"
              strokeWidth={active === at ? 2 : 1.2}
              onMouseEnter={() => setActive(at)}
              onMouseLeave={() => setActive(null)}
            />
          ))}

          {/* Names last, so a point never covers one. Every point that can hold
              a name gets one -- a store list is a dozen places, and "which one
              is that" is the question a map is asked. */}
          {places.map((place, at) => {
            const spot = labelAt.get(at);
            if (!spot && active !== at) return null;
            const where =
              spot ?? {
                x: x(place.lon),
                y: y(place.lat) - radius(place.value) - 4,
                anchor: "middle" as const,
              };
            return (
              <text
                key={`${place.label}-name`}
                x={where.x}
                y={where.y}
                textAnchor={where.anchor}
                fontSize="10"
                className={cx(
                  "pointer-events-none",
                  active === at || rank[at] < LEADERS
                    ? "fill-ink font-semibold"
                    : "fill-ink",
                )}
                // A halo, so a name crossing a street or a circle stays
                // readable without a solid box behind it hiding the map.
                stroke="var(--color-surface)"
                strokeWidth="2.6"
                paintOrder="stroke"
              >
                {place.label}
              </text>
            );
          })}
        </g>

        {/* Outside the clip, in the margin, where they can be read. */}
        {!basemap && (
          <g className="fill-faint" fontSize="9">
            {latLines.map((at) => (
              <text key={`latt${at}`} x={PAD.left - 5} y={y(at) + 3} textAnchor="end">
                {degrees(at, "lat", latStep)}
              </text>
            ))}
            {lonLines.map((at) => (
              <text key={`lont${at}`} x={x(at)} y={H - PAD.bottom + 11} textAnchor="middle">
                {degrees(at, "lon", lonStep)}
              </text>
            ))}
          </g>
        )}

        <rect
          x={PAD.left}
          y={PAD.top}
          width={box.width}
          height={box.height}
          fill="none"
          className="stroke-edge"
          strokeWidth="0.8"
        />

        {/* North, so the drawing declares its orientation rather than assuming
            it -- and on a Mercator map north really is straight up. */}
        <g
          transform={`translate(${W - PAD.right - 14} ${PAD.top + 14})`}
          className="fill-ink stroke-ink"
        >
          <line x1="0" y1="8" x2="0" y2="-6" strokeWidth="0.9" />
          <polygon points="0,-9 3,-3 -3,-3" stroke="none" />
          <text x="0" y="18" textAnchor="middle" fontSize="8" stroke="none">
            N
          </text>
        </g>

        {/* Scale, computed from the projection at this map's own latitude, so
            it is exact rather than indicative. */}
        <g className="fill-muted">
          <line x1={barX} x2={barX + barPixels} y1={barY} y2={barY} className="stroke-muted" strokeWidth="1.2" />
          <line x1={barX} x2={barX} y1={barY - 3} y2={barY + 3} className="stroke-muted" strokeWidth="1.2" />
          <line
            x1={barX + barPixels}
            x2={barX + barPixels}
            y1={barY - 3}
            y2={barY + 3}
            className="stroke-muted"
            strokeWidth="1.2"
          />
          <text x={barX + barPixels + 6} y={barY + 3} fontSize="9">
            {km < 1 ? `${km * 1000} m` : `${km} km`}
          </text>
        </g>

        {/* And what the circles mean. Area is proportional to the value, which
            nobody can read off a picture without being told. */}
        <g transform={`translate(${W - PAD.right - 122} ${barY})`} className="fill-muted">
          <circle cx="6" cy="0" r="4" className="fill-edge" fillOpacity="0.78" />
          <circle cx="26" cy="0" r="10" className="fill-accent" fillOpacity="0.78" />
          <text x="42" y="3" fontSize="9">
            area = {measure}
          </text>
        </g>
      </svg>

      <Readout
        slice={
          active === null
            ? null
            : { label: places[active].label, value: places[active].value, order: "" }
        }
        total={0}
        fallback={`${places.length} ${label.toLowerCase()}s across ${across} km, at the coordinates this file records. Point at one for its figure.`}
      />

      {/* Required by both, and it is also the honest note about what part of
          this drawing did not come out of the model. */}
      <p className="text-[10.5px] text-faint">
        {basemap ? (
          <>
            Basemap &copy; OpenStreetMap contributors, &copy; CARTO. The points
            are the model&rsquo;s; the streets are not.
          </>
        ) : (
          <>
            No basemap — the tile service could not be reached, so the map is
            drawn as a coordinate grid. Every point is still at the latitude and
            longitude this file records.
          </>
        )}
      </p>
    </div>
  );
}
