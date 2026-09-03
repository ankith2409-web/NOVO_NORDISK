/**
 * The model's tables and the joins between them, drawn.
 *
 * "What are the data sets and how it is joined with each other and what are the
 * SQL" was asked as one question. Two thirds of it were being answered in two
 * lists side by side -- tables on the left, joins on the right -- which is the
 * one shape that cannot show the thing being asked about. A join is a
 * *relationship between two tables*, and a list of relationships makes the
 * reader hold the whole graph in their head to see that `Sales` sits in the
 * middle and everything else hangs off it.
 *
 * So: boxes and lines. A star schema drawn as a star is recognisable in a
 * second by anybody who has built one, and needs no explaining to anybody who
 * has not.
 *
 * Everything here is read from the model. Table names, column counts, the
 * cardinality on each line, and which relationships are inactive are all
 * recorded in the file. The *positions* are the only thing computed here, and
 * they carry one claim: which tables are joined to which. Distance from the
 * centre means hops from the busiest table and nothing else -- it is not a
 * measure of importance, and the caption says so.
 */
import { useMemo, useState } from "react";
import type { DatasetJoin, DatasetTable } from "@/lib/api";
import { cx } from "@/lib/cx";

export const BOX_W = 132;
export const BOX_H = 38;
/**
 * Clear space between two boxes, whichever way round they sit.
 *
 * The rings are elliptical rather than circular because the boxes are: at a
 * circular radius of 132 a table due left of the hub has its right edge exactly
 * on the hub's left edge, the line between them is zero pixels long, and its
 * cardinality label prints on top of the hub's name -- `Sales` rendered as
 * `SM:1s`. Widening the circle to fix that would then push the tables above and
 * below far further apart than they need to be, because a box is 132 across and
 * 38 tall. So each axis gets the room that axis actually needs.
 */
const GAP = 62;
/** How many rings out the layout goes before it stops counting hops. */
const RINGS = 3;
const PAD = 14;
/** A band per connected component, so two clusters cannot overlap. */
const BAND_GAP = 26;

interface Placed {
  name: string;
  x: number;
  y: number;
  /** Hops from its cluster's busiest table. Layout only; not a ranking. */
  depth: number;
}

interface Drawn {
  join: DatasetJoin;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  /** Where its cardinality is written, nudged clear of any other label. */
  lx: number;
  ly: number;
}

/**
 * Clear air between every pair of boxes.
 *
 * Two boxes are far enough apart when they are separated on *either* axis --
 * side by side, or one above the other. `MIN_GAP` is what stops them merely
 * touching: laid on a circle of radius 132 a table due left of the hub had its
 * right edge exactly on the hub's left edge, which is not an overlap and still
 * reads as one box.
 */
export const MIN_GAP = 14;

function separated(nodes: Placed[]): boolean {
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const apartX = Math.abs(nodes[i].x - nodes[j].x) >= BOX_W + MIN_GAP;
      const apartY = Math.abs(nodes[i].y - nodes[j].y) >= BOX_H + MIN_GAP;
      if (!apartX && !apartY) return false;
    }
  }
  return true;
}

/** Where a line from `to` towards `from` meets `from`'s box edge. */
function onEdge(from: Placed, to: Placed): { x: number; y: number } {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (dx === 0 && dy === 0) return { x: from.x, y: from.y };
  const halfW = BOX_W / 2;
  const halfH = BOX_H / 2;
  // The border is hit on whichever axis runs out first.
  const scale = Math.min(
    dx === 0 ? Infinity : halfW / Math.abs(dx),
    dy === 0 ? Infinity : halfH / Math.abs(dy),
  );
  return { x: from.x + dx * scale, y: from.y + dy * scale };
}

/**
 * Exported for its tests. The two defects it has had were both geometric --
 * boxes abutting, and a label printed on top of a table's name -- and neither
 * is visible from the component's output without measuring the numbers.
 */
export function layout(tables: DatasetTable[], joins: DatasetJoin[]) {
  const names = tables.map((t) => t.name);
  const known = new Set(names);

  const neighbours = new Map<string, Set<string>>();
  for (const name of names) neighbours.set(name, new Set());
  for (const join of joins) {
    // A join naming a table the dataset does not list cannot be drawn between
    // two boxes. Skipped here and still listed in full below the drawing.
    if (!known.has(join.from_table) || !known.has(join.to_table)) continue;
    neighbours.get(join.from_table)!.add(join.to_table);
    neighbours.get(join.to_table)!.add(join.from_table);
  }

  const joined = names.filter((n) => neighbours.get(n)!.size > 0);
  const alone = names.filter((n) => neighbours.get(n)!.size === 0);

  // Connected clusters. A model can hold several -- Microsoft's supply-chain
  // sample has two pairs that never meet -- and drawing them as one graph
  // would imply a relationship the file does not contain.
  const seen = new Set<string>();
  const clusters: string[][] = [];
  for (const start of joined) {
    if (seen.has(start)) continue;
    const cluster: string[] = [];
    const queue = [start];
    seen.add(start);
    while (queue.length) {
      const at = queue.shift()!;
      cluster.push(at);
      for (const next of neighbours.get(at)!) {
        if (seen.has(next)) continue;
        seen.add(next);
        queue.push(next);
      }
    }
    clusters.push(cluster);
  }

  const placed: Placed[] = [];
  let top = PAD;
  let widest = 0;

  for (const cluster of clusters) {
    // The busiest table anchors its cluster. In a star schema that is the fact
    // table by construction: every dimension joins to it and to nothing else.
    const hub = cluster.reduce((best, name) =>
      neighbours.get(name)!.size > neighbours.get(best)!.size ? name : best,
    );

    const depth = new Map<string, number>([[hub, 0]]);
    const queue = [hub];
    while (queue.length) {
      const at = queue.shift()!;
      for (const next of neighbours.get(at)!) {
        if (depth.has(next)) continue;
        depth.set(next, Math.min(depth.get(at)! + 1, RINGS - 1));
        queue.push(next);
      }
    }

    const rings: string[][] = Array.from({ length: RINGS }, () => [] as string[]);
    for (const name of cluster) rings[depth.get(name) ?? 1].push(name);

    const here: Placed[] = [];
    for (const [at, ring] of rings.entries()) {
      if (at === 0) {
        for (const name of ring) here.push({ name, x: 0, y: 0, depth: 0 });
        continue;
      }
      // Grown until nothing on the ring is too close to anything already
      // placed. Checked rather than calculated: the closed form for how far
      // apart points on an *ellipse* are is unpleasant, and an approximation
      // that is nearly right still leaves two tables five pixels apart on a
      // model with eight dimensions. Measuring is exact, and a dozen rounds of
      // arithmetic on twenty boxes costs nothing.
      let scale = 1;
      let ringNodes: Placed[] = [];
      for (let attempt = 0; attempt < 14; attempt++) {
        const rx = (BOX_W + GAP) * at * scale;
        const ry = (BOX_H + GAP) * at * scale;
        ringNodes = ring.map((name, index) => {
          // Started at twelve o'clock and stepped clockwise, so a redraw of the
          // same model always produces the same picture. A layout that moved
          // between loads would make a reader doubt what they read last time.
          const angle = (index / ring.length) * Math.PI * 2 - Math.PI / 2;
          return {
            name,
            x: Math.cos(angle) * rx,
            y: Math.sin(angle) * ry,
            depth: at,
          };
        });
        if (separated([...here, ...ringNodes])) break;
        scale *= 1.15;
      }
      here.push(...ringNodes);
    }

    // Shifted to sit against the previous band by the box the nodes actually
    // occupy, not by the radius they were laid out on. A ring holding one node
    // at twelve o'clock is 250px tall in the maths and 38px tall on screen,
    // and reserving the difference left a third of the drawing empty.
    const minX = Math.min(...here.map((n) => n.x)) - BOX_W / 2;
    const minY = Math.min(...here.map((n) => n.y)) - BOX_H / 2;
    const maxX = Math.max(...here.map((n) => n.x)) + BOX_W / 2;
    const maxY = Math.max(...here.map((n) => n.y)) + BOX_H / 2;

    for (const node of here) {
      placed.push({ ...node, x: node.x - minX + PAD, y: node.y - minY + top });
    }

    widest = Math.max(widest, maxX - minX + PAD * 2);
    top += maxY - minY + BAND_GAP;
  }

  // Tables no relationship touches. Drawn, because "this table joins to
  // nothing" is a real and easily-missed finding -- a date table nobody wired
  // up filters nothing -- and a diagram that quietly omitted them would be
  // read as saying the model has no such table.
  if (alone.length) {
    // A clear gap, not a band gap: these are not another cluster, and sitting
    // one row below the last one they read as part of it.
    if (placed.length) top += BAND_GAP;
    const perRow = Math.max(1, Math.floor((widest || 640) / (BOX_W + 16)));
    alone.forEach((name, index) => {
      placed.push({
        name,
        x: PAD + BOX_W / 2 + (index % perRow) * (BOX_W + 16),
        y: top + BOX_H / 2 + Math.floor(index / perRow) * (BOX_H + 12),
        depth: -1,
      });
    });
    top += Math.ceil(alone.length / perRow) * (BOX_H + 12);
  }

  const at = new Map(placed.map((p) => [p.name, p]));
  const drawn: Drawn[] = [];
  // Where a cardinality label has already been written, so the next one can be
  // moved clear of it.
  const taken: { x: number; y: number }[] = [];
  for (const join of joins) {
    const from = at.get(join.from_table);
    const to = at.get(join.to_table);
    if (!from || !to) continue;
    const start = onEdge(from, to);
    const end = onEdge(to, from);

    // Along the line rather than always at its midpoint. An edge between two
    // tables on opposite sides of the hub runs straight through it -- Sales &
    // Returns joins `Association` at the bottom to `Product` at the top -- and
    // its label then prints on the hub's own name, which rendered `Sales` as
    // `SM:1s`. Tried at a few points along the line and settled on the first
    // that is clear of every box.
    const clear = (px: number, py: number) =>
      !placed.some(
        (node) =>
          Math.abs(node.x - px) < BOX_W / 2 + 4 && Math.abs(node.y - py) < BOX_H / 2 + 4,
      );

    let lx = (start.x + end.x) / 2;
    let ly = (start.y + end.y) / 2 - 4;
    for (const along of [0.5, 0.32, 0.68, 0.18, 0.82]) {
      const px = start.x + (end.x - start.x) * along;
      const py = start.y + (end.y - start.y) * along - 4;
      if (clear(px, py)) {
        lx = px;
        ly = py;
        break;
      }
    }

    // And clear of any label already written: two joins between the same pair
    // of tables put their midpoints in the same place.
    while (taken.some((p) => Math.abs(p.x - lx) < 26 && Math.abs(p.y - ly) < 11)) {
      ly += 12;
    }
    taken.push({ x: lx, y: ly });

    drawn.push({ join, x1: start.x, y1: start.y, x2: end.x, y2: end.y, lx, ly });
  }

  return {
    placed,
    drawn,
    alone: alone.length,
    width: Math.max(widest, PAD * 2 + BOX_W),
    height: top + PAD,
  };
}

export function SchemaMap({
  tables,
  joins,
  selected,
  onSelect,
}: {
  tables: DatasetTable[];
  joins: DatasetJoin[];
  /** The table whose joins are being read, or "" for all of them. */
  selected: string;
  onSelect: (table: string) => void;
}) {
  const [hovered, setHovered] = useState("");
  const { placed, drawn, alone, width, height } = useMemo(
    () => layout(tables, joins),
    [tables, joins],
  );

  if (placed.length === 0) return null;

  // Hover previews what clicking would settle on, so the picture answers
  // "what does this one touch" without committing to anything.
  const lit = hovered || selected;
  const touches = (join: DatasetJoin) =>
    !lit || join.from_table === lit || join.to_table === lit;
  const byName = new Map(tables.map((t) => [t.name, t]));

  return (
    <figure className="flex flex-col gap-1.5">
      <div className="w-full overflow-x-auto">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          style={{ width: Math.min(width, 760), height: "auto", maxWidth: "100%" }}
          role="img"
          aria-label={`${tables.length} tables and ${joins.length} joins between them`}
          className="overflow-visible"
        >
          {drawn.map(({ join, x1, y1, x2, y2 }, index) => {
            const on = touches(join);
            return (
              <g key={`${join.from_table}.${join.from_column}-${join.to_table}.${join.to_column}-${index}`}>
                <line
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke="currentColor"
                  strokeWidth={on ? 1.5 : 1}
                  // An inactive relationship only applies where a calculation
                  // deliberately invokes it. Drawn as a broken line for the
                  // same reason it is labelled everywhere else here: a reader
                  // who takes it for live expects a join the SQL never makes.
                  strokeDasharray={join.active ? undefined : "5 4"}
                  className={cx(
                    "transition-opacity duration-(--duration-feedback)",
                    on ? "text-edge opacity-100" : "text-edge opacity-20",
                  )}
                />
                {/* The "one" end, marked. Power BI's `M:1` reads from the
                    many side to the one side, so the dot sits where a single
                    row is matched. */}
                <circle
                  cx={x2}
                  cy={y2}
                  r={3}
                  className={cx(
                    "fill-edge transition-opacity duration-(--duration-feedback)",
                    on ? "opacity-100" : "opacity-20",
                  )}
                />
              </g>
            );
          })}

          {placed.map((node) => {
            const table = byName.get(node.name);
            const on = !lit || node.name === lit || drawn.some(
              (d) =>
                touches(d.join) &&
                (d.join.from_table === node.name || d.join.to_table === node.name),
            );
            return (
              <g
                key={node.name}
                onMouseEnter={() => setHovered(node.name)}
                onMouseLeave={() => setHovered("")}
                onClick={() => onSelect(selected === node.name ? "" : node.name)}
                className="cursor-pointer"
              >
                <rect
                  x={node.x - BOX_W / 2}
                  y={node.y - BOX_H / 2}
                  width={BOX_W}
                  height={BOX_H}
                  rx={4}
                  className={cx(
                    "transition-all duration-(--duration-feedback)",
                    selected === node.name
                      ? "fill-accent-soft stroke-accent"
                      : node.depth === 0
                        ? "fill-raised stroke-edge"
                        : "fill-ground stroke-hairline",
                    on ? "opacity-100" : "opacity-30",
                  )}
                  // Drawn broken, like the inactive relationships, because it
                  // says the same kind of thing: nothing filters through this
                  // table. Without it an unjoined table is distinguished only
                  // by the absence of a line, which is not something a reader
                  // notices in a diagram they have not seen before.
                  strokeDasharray={node.depth === -1 ? "4 3" : undefined}
                  strokeWidth={selected === node.name ? 2 : 1}
                />
                <text
                  x={node.x}
                  y={node.y - 2}
                  textAnchor="middle"
                  className={cx(
                    "fill-ink text-[11px] font-medium transition-opacity duration-(--duration-feedback)",
                    on ? "opacity-100" : "opacity-30",
                  )}
                >
                  {node.name.length > 17 ? `${node.name.slice(0, 16)}…` : node.name}
                </text>
                <text
                  x={node.x}
                  y={node.y + 11}
                  textAnchor="middle"
                  className={cx(
                    "fill-faint font-mono text-[8.5px] transition-opacity duration-(--duration-feedback)",
                    on ? "opacity-100" : "opacity-30",
                  )}
                >
                  {table?.measures_only
                    ? "measures only"
                    : `${table?.columns ?? 0} cols${table?.measures ? ` · ${table.measures}fx` : ""}`}
                </text>
                <title>
                  {node.depth === -1
                    ? `${node.name} — joined to nothing in this model`
                    : node.name}
                </title>
              </g>
            );
          })}

          {/* Last, so nothing can cover them. Drawn inside the edge pass they
              were painted before the boxes, and a line running behind a table
              took its label with it -- the `1:1` between Associated Product and
              Customer disappeared under `Product`. */}
          {drawn.map(({ join, lx, ly }, index) => (
            <text
              key={`label-${index}`}
              x={lx}
              y={ly}
              textAnchor="middle"
              // A halo in the page's own background colour, so the label stays
              // legible where it crosses a line.
              stroke="var(--color-surface)"
              strokeWidth={3}
              paintOrder="stroke"
              className={cx(
                "fill-faint font-mono text-[9px] transition-opacity duration-(--duration-feedback)",
                touches(join) ? "opacity-100" : "opacity-0",
              )}
            >
              {join.cardinality}
            </text>
          ))}
        </svg>
      </div>

      <figcaption className="max-w-prose text-[11.5px] text-muted">
        Click a table to read only its joins. A broken line is an inactive
        relationship, and the dot marks the end where a single row is matched.
        {alone > 0 && (
          <>
            {" "}
            <span className="text-review">
              {alone} table{alone === 1 ? " joins" : "s join"} to nothing
            </span>{" "}
            and {alone === 1 ? "is" : "are"} drawn apart, below.
          </>
        )}{" "}
        Position is a drawing convenience — distance from the centre is hops from
        the busiest table, not importance.
      </figcaption>
    </figure>
  );
}
