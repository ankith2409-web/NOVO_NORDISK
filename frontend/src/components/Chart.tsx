/**
 * Two charts, both deliberately plain.
 *
 * A reviewer twice asked for this to be simpler, and then asked for charts.
 * Those are the same request: a bar whose length is the number needs no
 * explaining, and a bespoke diagram does. So there are exactly two forms here
 * and no third.
 *
 * **Bars** compare magnitudes -- how many measures each table holds, how many
 * requirements sit at each confidence. **Meter** shows one ratio against its
 * whole: 29 measures of 32 translate to SQL.
 *
 * Three decisions worth stating, because each rules out something that looks
 * more impressive:
 *
 * *One hue, not a palette.* Length already carries the magnitude, so colouring
 * each bar differently would encode nothing and cost something: this project's
 * three status colours -- the obvious candidates for a confidence chart --
 * measure ΔE 3.6 apart under deuteranopia and 11.5 under normal vision. A
 * reader with common colour blindness cannot tell "needs review" from
 * "divergent". The row label carries identity instead, which every reader can
 * see.
 *
 * *A meter, not a pie.* Two slices is a ratio, and a ratio reads better as one
 * bar with both numbers written next to it than as a circle the eye has to
 * estimate angles from.
 *
 * *No axis furniture.* Every value is written at the end of its own bar, so a
 * gridline would be a second, worse way of reading the same number.
 */
import type { ReactNode } from "react";
import { cx } from "@/lib/cx";

export interface Row {
  label: string;
  value: number;
  /** Shown after the value, e.g. "· 4 measures". Never the value itself. */
  note?: string;
  /** Sentence for the hover, when the label alone does not explain the row. */
  title?: string;
}

/**
 * The rows to draw: biggest first, with a long tail summed into one.
 *
 * Folded rather than truncated. A chart of the top eight tables that quietly
 * dropped the other twelve would show a total that does not match the count
 * beside it -- and the reader has no way to tell, because the rows that would
 * have said so are the ones missing. The folded row carries their sum and
 * names them in its hover.
 *
 * Ties break on the label so the same data always draws the same chart.
 */
export function foldRows(rows: Row[], max: number): Row[] {
  const ordered = [...rows].sort(
    (a, b) => b.value - a.value || a.label.localeCompare(b.label),
  );
  if (ordered.length <= max) return ordered;

  const shown = ordered.slice(0, max);
  const rest = ordered.slice(max);
  return [
    ...shown,
    {
      label: `${rest.length} more`,
      value: rest.reduce((sum, row) => sum + row.value, 0),
      title: rest.map((row) => `${row.label}: ${row.value}`).join(", "),
    },
  ];
}

/**
 * Horizontal bars, longest first.
 *
 * Horizontal rather than vertical because the labels are table names and
 * confidence levels -- words, not dates. Vertical columns would turn every one
 * of them on its side.
 */
export function Bars({
  rows,
  caption,
  unit,
  max = 8,
}: {
  rows: Row[];
  caption?: ReactNode;
  /** What one unit is, for the reader: "measures", "requirements". */
  unit: string;
  /** Beyond this many rows the rest are summed into one, rather than scrolled. */
  max?: number;
}) {
  const shown = foldRows(rows, max);
  const top = Math.max(1, ...shown.map((row) => row.value));

  if (rows.length === 0) return null;

  return (
    <figure className="flex flex-col gap-1.5">
      <ul className="flex flex-col gap-1">
        {shown.map((row) => (
          <li
            key={row.label}
            className="grid grid-cols-[minmax(5rem,9rem)_1fr] items-center gap-2"
            title={row.title}
          >
            <span className="truncate text-right text-[12.5px] text-muted">{row.label}</span>
            <span className="flex min-w-0 items-center gap-2">
              {/* The bar. Thin, one hue, and a minimum width so a row with a
                  small count still reads as a row rather than as nothing. */}
              <span
                className="h-3.5 flex-none rounded-[3px] bg-accent"
                style={{ width: `max(3px, ${(row.value / top) * 100}%)` }}
                aria-hidden="true"
              />
              {/* Written at the end of its own bar rather than read off an
                  axis. With this few rows an axis would be a second and worse
                  way of getting the same number. */}
              <span className="flex-none font-mono text-[11.5px] text-ink tabular">
                {row.value}
              </span>
              {row.note && (
                <span className="truncate font-mono text-[11px] text-faint">{row.note}</span>
              )}
            </span>
          </li>
        ))}
      </ul>
      <figcaption className="text-[11.5px] text-muted">
        {caption ?? <>Bars are {unit}; the number is written at the end of each.</>}
      </figcaption>
    </figure>
  );
}

/**
 * One ratio against its whole.
 *
 * Both numbers are written out. A reader should never have to estimate a
 * proportion from a shape when the two figures that make it are known.
 */
export function Meter({
  value,
  of,
  label,
  rest,
  tone = "accent",
}: {
  value: number;
  of: number;
  /** What the filled part is. */
  label: string;
  /** What the unfilled part is, said rather than left as an absence. */
  rest?: string;
  tone?: "accent" | "ok";
}) {
  const share = of > 0 ? value / of : 0;
  const missing = of - value;

  return (
    <figure className="flex flex-col gap-1.5">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-lg font-semibold tabular">{value}</span>
        <span className="text-[12.5px] text-muted">
          of {of} {label}
        </span>
        <span className="ml-auto font-mono text-[11.5px] text-faint tabular">
          {Math.round(share * 100)}%
        </span>
      </div>
      {/* A hairline round the track, because the track's own fill is barely a
          step off the surface it sits on -- 1.07:1 -- and without the border
          the unfilled part has no visible extent. */}
      <div
        className="h-2.5 w-full overflow-hidden rounded-full border border-hairline bg-raised"
        role="img"
        aria-label={`${value} of ${of} ${label}`}
      >
        <div
          className={cx("h-full rounded-full", tone === "ok" ? "bg-ok" : "bg-accent")}
          style={{ width: `${share * 100}%` }}
        />
      </div>
      {rest && missing > 0 && (
        <figcaption className="text-[11.5px] text-muted">
          The other {missing} {rest}
        </figcaption>
      )}
    </figure>
  );
}
