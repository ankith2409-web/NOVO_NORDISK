/**
 * The geometry of the schema diagram.
 *
 * Both defects this layout has had were invisible from the component's output
 * and obvious the moment the numbers were measured:
 *
 * Boxes abutting. Laid on a circle of radius 132, a table due left of the hub
 * had its right edge exactly on the hub's left edge. The line between them was
 * zero pixels long and its cardinality label printed over the hub's name, so
 * `Sales` rendered as `SM:1s`. The rings are elliptical now, because the boxes
 * are: 132 across and 38 tall want different room on each axis.
 *
 * Labels on top of tables. An edge between two tables on opposite sides of the
 * hub runs straight through it -- Microsoft's Sales & Returns joins
 * `Association` at the bottom to `Product` at the top -- and its midpoint,
 * where the label went, is the hub's centre.
 *
 * Both are pinned here as properties over the whole drawing rather than as one
 * example each, because the next layout change will break them somewhere else.
 */
import { describe, expect, it } from "vitest";
import type { DatasetJoin, DatasetTable } from "@/lib/api";
import { BOX_H, BOX_W, MIN_GAP, layout } from "./SchemaMap";

function table(name: string, columns = 5): DatasetTable {
  return { name, columns, measures: 0, measures_only: false, dax: null };
}

function join(from: string, to: string, cardinality = "M:1", active = true): DatasetJoin {
  return {
    from_table: from,
    from_column: "id",
    to_table: to,
    to_column: "id",
    cardinality,
    cross_filter: "Single",
    active,
    sql: "",
  } as DatasetJoin;
}

/** A star: one fact table, four dimensions, one of them snowflaked. */
const STAR = {
  tables: ["Sales", "Store", "Item", "Calendar", "Customer", "District", "Orphan"].map((n) =>
    table(n),
  ),
  joins: [
    join("Sales", "Store"),
    join("Sales", "Item"),
    join("Sales", "Calendar"),
    join("Sales", "Customer"),
    join("Store", "District"),
  ],
};

/**
 * Too close, not merely overlapping.
 *
 * A gap of zero is the defect this started as: on a circle of radius 132 a
 * table due left of the hub had its right edge exactly on the hub's left edge.
 * Nothing overlapped, and it read as one box with two names in it. So the test
 * demands clear air, the same `MIN_GAP` the layout enforces.
 */
function tooClose(a: { x: number; y: number }, b: { x: number; y: number }): boolean {
  return (
    Math.abs(a.x - b.x) < BOX_W + MIN_GAP && Math.abs(a.y - b.y) < BOX_H + MIN_GAP
  );
}

describe("layout", () => {
  it("puts the busiest table at the centre of its cluster", () => {
    const { placed } = layout(STAR.tables, STAR.joins);
    const hub = placed.find((n) => n.depth === 0);
    expect(hub?.name).toBe("Sales");
    // And a table two joins away sits further out than one join away.
    expect(placed.find((n) => n.name === "District")?.depth).toBe(2);
    expect(placed.find((n) => n.name === "Store")?.depth).toBe(1);
  });

  it("leaves clear air between every pair of boxes", () => {
    const { placed } = layout(STAR.tables, STAR.joins);
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        expect(
          tooClose(placed[i], placed[j]),
          `${placed[i].name} is touching ${placed[j].name}`,
        ).toBe(false);
      }
    }
  });

  it.each([4, 8, 14])("leaves clear air with %i dimensions on one table", (count) => {
    // The ring has to grow to hold them. Eight is where an approximation of
    // how far apart points on an ellipse are left two tables five pixels
    // apart -- close enough to look deliberate and still wrong.
    const names = Array.from({ length: count }, (_, i) => `Dim${i}`);
    const { placed } = layout(
      [table("Fact"), ...names.map((n) => table(n))],
      names.map((n) => join("Fact", n)),
    );
    expect(placed).toHaveLength(count + 1);
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        expect(
          tooClose(placed[i], placed[j]),
          `${placed[i].name} is touching ${placed[j].name}`,
        ).toBe(false);
      }
    }
  });

  it("never writes a cardinality label inside a table", () => {
    // The join that broke this: two dimensions on opposite sides of the hub,
    // whose line runs straight through it. With four of them laid at the
    // quarter points, `Store` at twelve o'clock and `Calendar` at six are
    // exactly that pair, and the midpoint of the line between them is the
    // centre of `Sales`.
    const { placed, drawn } = layout(STAR.tables, [
      ...STAR.joins,
      join("Store", "Calendar"),
    ]);
    for (const edge of drawn) {
      for (const node of placed) {
        const inside =
          Math.abs(node.x - edge.lx) < BOX_W / 2 &&
          Math.abs(node.y - edge.ly) < BOX_H / 2;
        expect(inside, `${edge.join.cardinality} sits on ${node.name}`).toBe(false);
      }
    }
  });

  it("never writes two labels on top of each other", () => {
    // Two relationships between the same pair share a midpoint.
    const { drawn } = layout(
      [table("Sales"), table("Store")],
      [join("Sales", "Store"), join("Sales", "Store", "1:1")],
    );
    expect(drawn).toHaveLength(2);
    const [a, b] = drawn;
    expect(Math.abs(a.lx - b.lx) >= 26 || Math.abs(a.ly - b.ly) >= 11).toBe(true);
  });

  it("draws a table nothing joins to, apart and marked", () => {
    const { placed, alone } = layout(STAR.tables, STAR.joins);
    expect(alone).toBe(1);
    const orphan = placed.find((n) => n.name === "Orphan");
    // Marked so it can be drawn differently -- "this table filters nothing" is
    // a real finding and easily missed, and dropping it would read as saying
    // the model has no such table.
    expect(orphan?.depth).toBe(-1);
    const joined = placed.filter((n) => n.depth >= 0);
    expect(orphan!.y).toBeGreaterThan(Math.max(...joined.map((n) => n.y)));
  });

  it("keeps two clusters that never meet from being drawn as one", () => {
    const { placed } = layout(
      ["A", "B", "C", "D"].map((n) => table(n)),
      [join("A", "B"), join("C", "D")],
    );
    // Two hubs, because there are two graphs. Drawing them joined would claim
    // a relationship the model does not contain.
    expect(placed.filter((n) => n.depth === 0)).toHaveLength(2);
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        expect(tooClose(placed[i], placed[j])).toBe(false);
      }
    }
  });

  it("draws a model with no relationships at all", () => {
    const { placed, drawn, alone } = layout(
      ["A", "B", "C"].map((n) => table(n)),
      [],
    );
    expect(placed).toHaveLength(3);
    expect(drawn).toHaveLength(0);
    expect(alone).toBe(3);
  });

  it("skips a join naming a table the dataset does not list", () => {
    // System tables are excluded from the dataset payload while a relationship
    // may still name one. Drawing a line to nothing would throw.
    const { drawn } = layout(
      [table("Sales")],
      [join("Sales", "LocalDateTable_abc")],
    );
    expect(drawn).toHaveLength(0);
  });

  it("draws the same model the same way twice", () => {
    // A picture that moved between loads would make a reader doubt what they
    // read last time.
    const a = layout(STAR.tables, STAR.joins);
    const b = layout(STAR.tables, STAR.joins);
    expect(a.placed).toEqual(b.placed);
    expect(a.width).toBe(b.width);
    expect(a.height).toBe(b.height);
  });

  it("reports a canvas big enough to hold every box", () => {
    const { placed, width, height } = layout(STAR.tables, STAR.joins);
    for (const node of placed) {
      expect(node.x - BOX_W / 2).toBeGreaterThanOrEqual(0);
      expect(node.y - BOX_H / 2).toBeGreaterThanOrEqual(0);
      expect(node.x + BOX_W / 2).toBeLessThanOrEqual(width);
      expect(node.y + BOX_H / 2).toBeLessThanOrEqual(height);
    }
  });
});
