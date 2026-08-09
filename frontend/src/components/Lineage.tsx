/**
 * Where one number comes from, and what depends on it, drawn as a chain.
 *
 * The Model view argues -- correctly -- that a semantic model is better browsed
 * as a tree than as a canvas, and that "what breaks if I change this" is better
 * answered by naming the affected objects than by asking someone to trace edges.
 * Both still hold. This is the case neither covers.
 *
 * A list of names loses two things a chain keeps: the order of the hops, and the
 * fact that there were hops at all. `OOS Rate` reads `OOS Results`, which reads
 * `TestResult[ResultStatus]`; flattened into "reads: ResultStatus, OOS Results,
 * Tests Performed, TestResult" that reads as four peers, and the column that is
 * two steps away is indistinguishable from the measure that is one. When a
 * reviewer asks where a number comes from, the shape of the answer is the answer.
 *
 * So this is deliberately not a model map. It is one object's ancestry and
 * descent, to a bounded depth, laid out left to right: what feeds it on the
 * left, itself in the middle, what it feeds on the right.
 *
 * Drawn as plain SVG. A layout library would be a large dependency for a graph
 * this size, and the layering here is exact rather than approximate: every node
 * sits at its true distance in hops from the subject, which is the one property
 * the picture has to get right.
 */
import { useMemo } from "react";
import type { GraphPayload } from "@/lib/api";
import { cx } from "@/lib/cx";

type GraphNode = GraphPayload["nodes"][number] & { name?: string; table?: string };

/** The edge kinds that mean "reads". `contains` and `defines` are ownership and
 *  `joins` is structure; drawing any of them here would put two different
 *  meanings on one arrow.
 *
 *  `loads` is a table pointing at the file or warehouse it comes from. It is
 *  the same relation one step further out -- without it the chain ends at the
 *  Power BI table, which is exactly where the interesting part begins for
 *  anyone asking where a number really originates. */
const READS = new Set(["references", "loads"]);

const NODE_WIDTH = 150;
const NODE_HEIGHT = 34;
const GAP_X = 58;
const GAP_Y = 12;
const PADDING = 10;
/** Above this, one column stops being a picture and becomes a very tall list.
 *  Nothing is lost by capping it: the panels beside this one name every
 *  dependency in full, and the count of what is not drawn is stated. */
const MAX_PER_COLUMN = 12;

interface Placed {
  id: string;
  label: string;
  sub: string;
  x: number;
  y: number;
}

export function Lineage({
  graph,
  focus,
  depth = 2,
  onSelect,
}: {
  graph: GraphPayload;
  /** Node id at the centre. */
  focus: string;
  /** How many hops out, each way. */
  depth?: number;
  onSelect?: (id: string) => void;
}) {
  const layout = useMemo(() => build(graph, focus, depth), [graph, focus, depth]);

  if (layout.nodes.length <= 1) {
    return (
      <p className="p-3 text-xs text-muted">
        Nothing reads this and it reads nothing — there is no chain to draw.
      </p>
    );
  }

  const { nodes, edges, notes, width, height } = layout;
  const byId = new Map(nodes.map((node) => [node.id, node]));

  return (
    <div className="overflow-x-auto p-3">
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Lineage for ${byId.get(focus)?.label ?? focus}`}
        className="max-w-none"
      >
        {edges.map((edge) => {
          const from = byId.get(edge.from);
          const to = byId.get(edge.to);
          if (!from || !to) return null;
          // Right edge of the upstream box to the left edge of the downstream
          // one, bowed so parallel edges between the same two columns stay
          // distinguishable.
          const x1 = from.x + NODE_WIDTH;
          const y1 = from.y + NODE_HEIGHT / 2;
          const x2 = to.x;
          const y2 = to.y + NODE_HEIGHT / 2;
          const bend = (x2 - x1) / 2;
          return (
            <path
              key={`${edge.from}->${edge.to}`}
              d={`M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`}
              className="fill-none stroke-hairline"
              strokeWidth={1.25}
            />
          );
        })}

        {nodes.map((node) => {
          const isFocus = node.id === focus;
          return (
            <g
              key={node.id}
              transform={`translate(${node.x} ${node.y})`}
              onClick={() => onSelect?.(node.id)}
              className={cx(onSelect && !isFocus && "cursor-pointer")}
            >
              <rect
                width={NODE_WIDTH}
                height={NODE_HEIGHT}
                rx={3}
                className={cx(
                  "stroke-hairline",
                  isFocus ? "fill-accent-soft stroke-accent" : "fill-ground",
                )}
              />
              <text
                x={7}
                y={14}
                className={cx(
                  "font-mono text-[10.5px]",
                  isFocus ? "fill-accent" : "fill-ink",
                )}
              >
                {clip(node.label, 21)}
              </text>
              <text x={7} y={26} className="fill-faint font-mono text-[9px]">
                {clip(node.sub, 25)}
              </text>
            </g>
          );
        })}
        {notes.map((note) => (
          <text
            key={`${note.x}:${note.y}`}
            x={note.x + 7}
            y={note.y + 10}
            className="fill-faint font-mono text-[9px]"
          >
            {note.text}
          </text>
        ))}
      </svg>
    </div>
  );
}

function clip(text: string, at: number): string {
  return text.length > at ? `${text.slice(0, at - 1)}…` : text;
}

/**
 * Walk out from the focus in both directions and place what is found.
 *
 * Depth is signed: negative upstream (what this reads), positive downstream
 * (what reads this). A node reachable both ways -- possible in a model where two
 * measures read each other's inputs -- keeps whichever distance is shorter, so
 * the column is drawn once at its nearest true position rather than twice.
 */
function build(graph: GraphPayload, focus: string, maxDepth: number) {
  const reads = new Map<string, string[]>();
  const readBy = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (!READS.has(edge.kind)) continue;
    push(reads, edge.source, edge.target);
    push(readBy, edge.target, edge.source);
  }

  const depths = new Map<string, number>([[focus, 0]]);
  walk(reads, focus, -1, maxDepth, depths);
  walk(readBy, focus, 1, maxDepth, depths);

  // A source is pulled in whatever the hop budget says. It is a terminal node
  // -- nothing is upstream of a CSV -- so it can only ever add one box, and it
  // is the specific thing a lineage is usually being read to find. Cutting it
  // off at the depth limit would answer "where does this number come from"
  // with the name of a Power BI table, which is where the question starts.
  for (const [id, depth] of [...depths]) {
    for (const upstream of reads.get(id) ?? []) {
      if (!upstream.startsWith("source:") || depths.has(upstream)) continue;
      depths.set(upstream, depth - 1);
    }
  }

  const info = new Map(graph.nodes.map((node) => [node.id, node as GraphNode]));
  const columns = new Map<number, Placed[]>();
  for (const [id, depth] of depths) {
    const node = info.get(id);
    if (!node) continue;
    const column = columns.get(depth) ?? [];
    column.push({
      id,
      label: node.name ?? id,
      sub: node.table ? `${node.kind} · ${node.table}` : node.kind,
      x: 0,
      y: 0,
    });
    columns.set(depth, column);
  }

  const order = [...columns.keys()].sort((a, b) => a - b);
  const overflow = new Map<number, number>();
  for (const depth of order) {
    const items = columns.get(depth)!;
    items.sort((a, b) => a.label.localeCompare(b.label));
    if (items.length > MAX_PER_COLUMN) {
      overflow.set(depth, items.length - MAX_PER_COLUMN);
      columns.set(depth, items.slice(0, MAX_PER_COLUMN));
    }
  }
  const tallest = Math.max(...order.map((depth) => columns.get(depth)!.length));
  const noteRoom = overflow.size > 0 ? 18 : 0;
  const height = tallest * (NODE_HEIGHT + GAP_Y) - GAP_Y + PADDING * 2 + noteRoom;

  const placed: Placed[] = [];
  const notes: { x: number; y: number; text: string }[] = [];
  order.forEach((depth, column) => {
    const items = columns.get(depth)!;
    // Centred against the tallest column, so the focus sits on the eye line
    // instead of at the top of a short column beside a long one.
    const span = items.length * (NODE_HEIGHT + GAP_Y) - GAP_Y;
    // Centred against the node area only. Folding the overflow note's room into
    // the centring would push the tallest column down onto its own note.
    const top = PADDING + (height - noteRoom - PADDING * 2 - span) / 2;
    items.forEach((item, row) => {
      placed.push({
        ...item,
        x: PADDING + column * (NODE_WIDTH + GAP_X),
        y: top + row * (NODE_HEIGHT + GAP_Y),
      });
    });
    const hidden = overflow.get(depth);
    if (hidden) {
      notes.push({
        x: PADDING + column * (NODE_WIDTH + GAP_X),
        y: top + items.length * (NODE_HEIGHT + GAP_Y) + 2,
        text: `+${hidden} more, listed in full below`,
      });
    }
  });

  const present = new Set(placed.map((node) => node.id));
  const edges: { from: string; to: string }[] = [];
  for (const [target, sources] of readBy) {
    if (!present.has(target)) continue;
    for (const source of sources) {
      if (!present.has(source)) continue;
      const from = depths.get(target)!;
      const to = depths.get(source)!;
      // Only edges that actually step one column to the right are drawn. An
      // edge between two nodes in the same column would be a horizontal line
      // through its neighbours, and one spanning three columns would cross
      // boxes it has nothing to do with.
      if (to - from !== 1) continue;
      edges.push({ from: target, to: source });
    }
  }

  const width = PADDING * 2 + order.length * (NODE_WIDTH + GAP_X) - GAP_X;
  return { nodes: placed, edges, notes, width, height };
}

function push(map: Map<string, string[]>, key: string, value: string): void {
  const existing = map.get(key);
  if (existing) existing.push(value);
  else map.set(key, [value]);
}

function walk(
  adjacency: Map<string, string[]>,
  from: string,
  step: -1 | 1,
  maxDepth: number,
  depths: Map<string, number>,
): void {
  let frontier = [from];
  for (let hop = 1; hop <= maxDepth; hop++) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const neighbour of adjacency.get(id) ?? []) {
        const known = depths.get(neighbour);
        // Nearest wins, so a node reachable by both a short and a long path is
        // drawn where it actually sits relative to the subject.
        if (known !== undefined && Math.abs(known) <= hop) continue;
        depths.set(neighbour, hop * step);
        next.push(neighbour);
      }
    }
    if (next.length === 0) return;
    frontier = next;
  }
}
