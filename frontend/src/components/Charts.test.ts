/**
 * Which chart a split gets drawn as, and how its numbers are abbreviated.
 *
 * These are the two decisions in `Charts.tsx` that can be wrong without
 * looking wrong. A ring drawn for a measure that goes both ways states the
 * opposite of what the number says; a column chart drawn for long labels is
 * unreadable in one model and fine in the next; and an abbreviation that
 * rounds 999,999 to "1.0M" makes two different figures print identically.
 */

import { describe, expect, it } from "vitest";
import { LABEL_MAX, RING_MAX, brief, chartFor, exact } from "./Charts";

const slices = (...pairs: [string, number][]) =>
  pairs.map(([label, value]) => ({ label, value }));

describe("chartFor", () => {
  it("draws a small split as a ring", () => {
    expect(chartFor(slices(["Fashions Direct", 32], ["Lindseys", 13]))).toBe("donut");
  });

  it("never rings a measure that goes both ways", () => {
    // A negative arc is not a smaller share of anything. Store Sales' variance
    // is negative for one chain and positive for the other, and a ring of the
    // two magnitudes would show them as parts of a whole that does not exist.
    expect(chartFor(slices(["Lindseys", -386], ["Fashions Direct", 694]))).toBe("bars");
  });

  it("draws a wider split with short labels as columns", () => {
    const wide = slices(
      ["020-Mens", 8], ["050-Shoes", 7], ["040-Jr", 6], ["090-Home", 5],
      ["010-Wom", 4], ["100-Groc", 3], ["080-Acc", 2],
    );
    expect(wide.length).toBeGreaterThan(RING_MAX);
    expect(chartFor(wide)).toBe("columns");
  });

  it("falls back to bars when a label will not fit under a column", () => {
    const long = slices(
      ["Valery Ushakov", 8], ["Andrew Ma", 7], ["Carlos Grilo", 6],
      ["Tina Lassila", 5], ["Chris McGurk", 4], ["Annelie Zubar", 3], ["Chris Gray", 2],
    );
    expect(long.some((s) => s.label.length > LABEL_MAX)).toBe(true);
    expect(chartFor(long)).toBe("bars");
  });

  it("puts a ring's ceiling exactly at RING_MAX", () => {
    const at = slices(...Array.from({ length: RING_MAX }, (_, i): [string, number] => [`g${i}`, 1]));
    const over = [...at, { label: "g", value: 1 }];
    expect(chartFor(at)).toBe("donut");
    expect(chartFor(over)).not.toBe("donut");
  });
});

describe("brief", () => {
  it("keeps a magnitude readable without inventing precision", () => {
    expect(brief(0)).toBe("0");
    expect(brief(45184553.69)).toBe("45.2M");
    expect(brief(1_500_000_000)).toBe("1.5B");
    expect(brief(13174)).toBe("13.2K");
  });

  it("does not round two different figures into the same string", () => {
    // 999,999 abbreviating to "1.0M" would print identically to 1,000,000.
    expect(brief(999_999)).not.toBe(brief(1_000_000));
  });

  it("keeps the sign, because a variance depends on it", () => {
    expect(brief(-694324)).toMatch(/^-/);
  });

  it("keeps small ratios from collapsing to zero", () => {
    // A margin of 0.0042 shown as "0" is a wrong answer that looks like a
    // rounding choice.
    expect(brief(0.0042)).not.toBe("0");
  });
});

describe("exact", () => {
  it("gives the digits the abbreviation dropped", () => {
    expect(exact(45184553.69)).toContain("45");
    expect(exact(45184553.69).replace(/[^0-9]/g, "").length).toBeGreaterThan(7);
  });
});

describe("chartFor and additivity", () => {
  it("never rings a measure whose parts do not sum to a whole", () => {
    // Store Sales' `Average Selling Area Size` splits into two chains that
    // compare fairly and sum to 59,302 -- a number the model does not contain;
    // its real whole-model average is 24,327. A ring would state those two
    // arcs as parts of a total that does not exist.
    const averages = slices(["Fashions Direct", 48108], ["Lindseys", 11194]);
    expect(chartFor(averages, true)).toBe("donut");
    expect(chartFor(averages, false)).not.toBe("donut");
  });

  it("still draws a non-additive split, because comparing is still valid", () => {
    const averages = slices(["Fashions Direct", 48108], ["Lindseys", 11194]);
    expect(["bars", "columns"]).toContain(chartFor(averages, false));
  });

  it("assumes additive when not told, so an old caller is not silently changed", () => {
    expect(chartFor(slices(["a", 2], ["b", 1]))).toBe("donut");
  });
});

describe("a period is a sequence, not a set of parts", () => {
  const months = slices(
    ["Jan", 6], ["Feb", 5], ["Mar", 4], ["Apr", 3], ["May", 2], ["Jun", 1],
  );

  it("draws six months as columns, not as a ring", () => {
    // Six slices would otherwise be a ring. The reader's question about months
    // is which way it is going, and a ring has no beginning.
    expect(chartFor(months, true, "Month")).toBe("columns");
    expect(chartFor(months, true, "Chain")).toBe("donut");
  });

  it("recognises the period a model actually names it", () => {
    for (const name of ["FiscalYear", "Fiscal Year", "Quarter", "Period", "Order Date"]) {
      expect(chartFor(months, true, name)).toBe("columns");
    }
  });

  it("does not mistake a word that merely contains one", () => {
    // `Yearbook` is not a period, and a substring rule would say it was.
    expect(chartFor(months, true, "Yearbook")).toBe("donut");
  });

  it("still falls back to bars when a period's labels are long", () => {
    const long = slices(["January 2019", 2], ["February 2019", 1]);
    expect(chartFor(long, true, "Month")).toBe("bars");
  });
});
