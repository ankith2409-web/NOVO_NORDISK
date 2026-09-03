/**
 * The one piece of arithmetic in the charts, and the one way it could lie.
 *
 * A bar chart of the top eight tables that quietly dropped the other twelve
 * would show a picture whose total does not match the count printed beside it,
 * and the reader has no way to notice: the rows that would have said so are
 * exactly the ones missing. So the tail is summed into one row rather than cut.
 */
import { describe, expect, it } from "vitest";
import { foldRows, type Row } from "./Chart";

const total = (rows: Row[]) => rows.reduce((sum, row) => sum + row.value, 0);

describe("foldRows", () => {
  it("orders biggest first", () => {
    const rows = foldRows(
      [
        { label: "Store", value: 6 },
        { label: "Sales", value: 26 },
        { label: "Item", value: 1 },
      ],
      8,
    );
    expect(rows.map((r) => r.label)).toEqual(["Sales", "Store", "Item"]);
  });

  it("leaves a short list exactly as long as it was", () => {
    const rows = [
      { label: "A", value: 3 },
      { label: "B", value: 1 },
    ];
    expect(foldRows(rows, 8)).toHaveLength(2);
    // And no phantom "0 more" row on a list that fits exactly.
    expect(foldRows(rows, 2)).toHaveLength(2);
  });

  it("keeps the total when it folds a tail", () => {
    const rows: Row[] = Array.from({ length: 20 }, (_, i) => ({
      label: `T${i}`,
      value: i + 1,
    }));
    const folded = foldRows(rows, 8);
    expect(folded).toHaveLength(9);
    expect(total(folded)).toBe(total(rows));
  });

  it("names what it folded, so nothing is lost without saying so", () => {
    const rows: Row[] = [
      { label: "Big", value: 10 },
      { label: "Small", value: 2 },
      { label: "Tiny", value: 1 },
    ];
    const folded = foldRows(rows, 1);
    expect(folded[1].label).toBe("2 more");
    expect(folded[1].value).toBe(3);
    expect(folded[1].title).toBe("Small: 2, Tiny: 1");
  });

  it("draws the same data the same way twice", () => {
    // Ties break on the label, so a chart does not reshuffle between loads.
    const rows: Row[] = [
      { label: "Zeta", value: 4 },
      { label: "Alpha", value: 4 },
      { label: "Mid", value: 4 },
    ];
    expect(foldRows(rows, 8).map((r) => r.label)).toEqual(["Alpha", "Mid", "Zeta"]);
    expect(foldRows([...rows].reverse(), 8).map((r) => r.label)).toEqual([
      "Alpha",
      "Mid",
      "Zeta",
    ]);
  });

  it("does not mutate what it was given", () => {
    const rows: Row[] = [
      { label: "A", value: 1 },
      { label: "B", value: 9 },
    ];
    foldRows(rows, 8);
    expect(rows.map((r) => r.label)).toEqual(["A", "B"]);
  });

  it("handles a model with nothing to chart", () => {
    expect(foldRows([], 8)).toEqual([]);
  });
});
