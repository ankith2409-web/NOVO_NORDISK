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

import { useId, useState } from "react";
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
}

/**
 * A ring, for a split into a handful of parts.
 *
 * The hole is not decoration: it is where the total goes, so the part and the
 * whole are readable without moving the eye off the chart.
 */
export function Donut({ slices, by, measure }: ChartProps) {
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
export function Bars({ slices, by, measure, additive = true }: ChartProps) {
  const [active, setActive] = useState<number | null>(null);
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
              )}
              onMouseEnter={() => setActive(at)}
              onMouseLeave={() => setActive(null)}
            >
              <span className="w-[7.5rem] shrink-0 truncate" title={slice.label}>
                {slice.label}
              </span>
              <span className="relative h-3.5 min-w-0 flex-1 rounded-[2px] bg-hairline">
                <span
                  className="absolute inset-y-0 rounded-[2px] bg-accent transition-[width] duration-200"
                  style={{
                    width: `${extent}%`,
                    left: signed ? (slice.value < 0 ? `${50 - extent}%` : "50%") : 0,
                    opacity: shade(at, slices.length),
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
export function Columns({ slices, by, measure, additive = true }: ChartProps) {
  const [active, setActive] = useState<number | null>(null);
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
              className="w-full rounded-t-[2px] bg-accent transition-[height] duration-200"
              style={{
                height: `${(Math.abs(slice.value) / tallest) * 100}%`,
                opacity: active === at ? 1 : shade(at, slices.length),
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
