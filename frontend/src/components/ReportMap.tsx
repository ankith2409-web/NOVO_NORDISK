/**
 * The report page redrawn as its own floor plan.
 *
 * A reviewer showed a Superstore dashboard on a call -- five figures across the
 * top, a bar chart, a donut, a map, a table -- and asked how you tell which DAX
 * belongs to which of them. Every answer this tool gave was a list. Lists are
 * the wrong shape for that question: nobody remembers a dashboard as an
 * alphabetical index, they remember it as *the big number top left* and *the
 * donut on the right*.
 *
 * So this draws the page. Every rectangle is where the author actually put it,
 * at the size they made it, on the canvas they chose -- all five numbers are in
 * the .pbix, and none of them are guessed. Click one and the formula behind it
 * is right there.
 *
 * What it deliberately does not do is show the *numbers*. `2.3M` and `13.33%`
 * cannot be had from this file: the value of a measure is DAX evaluated against
 * data under a filter context the report supplies at render time, and this tool
 * does not evaluate DAX. A pretty card reading `2.3M` would be a number this
 * project invented, which is the one thing it exists not to do. So the tiles
 * are drawn as what is actually known about them -- position, size, kind, and
 * the measure behind them -- and the figures are left to Power BI.
 */
import { useMemo } from "react";
import type { Tile } from "@/lib/api";
import { cx } from "@/lib/cx";

/** Power BI's default canvas, used only when a page records no size at all. */
const FALLBACK = { width: 1280, height: 720 };

/**
 * How tall the drawing gets, in pixels.
 *
 * Capped because this is a thumbnail, not a page to inhabit: drawn at the
 * container's full width a 16:9 report ran past 700px tall, pushing the tile it
 * indexes off the bottom of the screen. At this size a whole page is taken in
 * at a glance, which is the only thing the drawing is for.
 */
const MAX_HEIGHT = 400;

/**
 * A tile smaller than this fraction of the canvas gets no text.
 *
 * Measured rather than chosen: at 1000px wide, a tile 4% of the canvas is 40px
 * across, which fits about four characters. A label that renders as "Sto…" is
 * worse than the shape alone, because it looks like a rendering failure.
 */
const TOO_SMALL = 0.045;

export function ReportMap({
  tiles,
  canvas,
  picked,
  onPick,
  nameOf,
}: {
  tiles: Tile[];
  canvas: { width: number; height: number };
  /** Index of the tile currently being read, or null. */
  picked: number | null;
  onPick: (index: number) => void;
  /** What to call a tile whose author set no title. */
  nameOf: (tile: Tile) => string;
}) {
  const width = canvas.width > 0 ? canvas.width : FALLBACK.width;
  const height = canvas.height > 0 ? canvas.height : FALLBACK.height;

  // Sorted by the author's own stacking order, so a tile they put on top of
  // another is drawn on top of it here too. Ties keep file order, which keeps
  // the drawing stable between loads.
  const order = useMemo(
    () =>
      tiles
        .map((tile, index) => ({ tile, index }))
        .filter(({ tile }) => tile.width > 0 && tile.height > 0)
        .sort((a, b) => a.tile.z - b.tile.z || a.index - b.index),
    [tiles],
  );

  const unplaced = tiles.length - order.length;

  if (order.length === 0) return null;

  return (
    <div className="flex flex-col gap-1.5">
      {/* Width is what gets limited, not height: with an aspect ratio set, a
          max-height would be silently ignored while the box kept its full
          width. Capping the width to whatever yields `MAX_HEIGHT` at this
          page's proportions bounds both. */}
      <div
        className="relative w-full overflow-hidden rounded border border-hairline bg-surface"
        // The page's own proportions. A fixed height would squash a portrait
        // report and letterbox a wide one, and the shape of the page is part
        // of what makes it recognisable.
        style={{
          aspectRatio: `${width} / ${height}`,
          maxWidth: `${Math.round(MAX_HEIGHT * (width / height))}px`,
        }}
        role="group"
        aria-label="The report page, drawn to scale"
      >
        {order.map(({ tile, index }) => {
          const w = tile.width / width;
          const h = tile.height / height;
          const tiny = w < TOO_SMALL || h < TOO_SMALL;
          const label = nameOf(tile);
          return (
            <button
              key={index}
              type="button"
              onClick={() => onPick(index)}
              aria-pressed={picked === index}
              title={label}
              style={{
                left: `${(tile.x / width) * 100}%`,
                top: `${(tile.y / height) * 100}%`,
                width: `${w * 100}%`,
                height: `${h * 100}%`,
              }}
              className={cx(
                "absolute flex flex-col justify-start overflow-hidden rounded-[3px] border p-1 text-left",
                "transition-colors duration-(--duration-feedback) ease-(--ease-standard)",
                // A KPI is drawn as the thing it is: filled, so the headline
                // figures read as a group at a glance, which is exactly how
                // they read on the dashboard itself.
                tile.is_kpi
                  ? "border-ok/50 bg-ok-soft hover:border-ok"
                  : "border-edge bg-ground hover:border-accent",
                picked === index && "ring-2 ring-accent ring-offset-1 ring-offset-surface",
              )}
            >
              {!tiny && (
                <>
                  <span className="w-full truncate text-[10.5px] leading-tight font-medium">
                    {label}
                  </span>
                  {tile.is_kpi && (
                    <span className="font-mono text-[8.5px] tracking-[0.08em] text-ok uppercase">
                      kpi
                    </span>
                  )}
                </>
              )}
            </button>
          );
        })}
      </div>

      <p className="font-mono text-[10px] text-faint">
        {Math.round(width)}×{Math.round(height)} · every tile where its author put it
        {unplaced > 0 && ` · ${unplaced} with no recorded position, listed below only`}
      </p>
    </div>
  );
}
