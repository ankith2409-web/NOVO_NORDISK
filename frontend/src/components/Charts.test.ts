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
import {
  LABEL_MAX,
  RING_MAX,
  LEADERS,
  arrange,
  brief,
  canOrderByTime,
  chartFor,
  exact,
  ranks,
} from "./Charts";

//: `order` is what a slice carries to say where its group sits in time. These
//: cases are about shape and magnitude, so they leave it empty -- which is also
//: what a real slice from a non-temporal column carries.
const slices = (...pairs: [string, number][]) =>
  pairs.map(([label, value]) => ({ label, value, order: "" }));

/** The same, with a date anchor on each group, for the ordering tests. */
const dated = (...triples: [string, number, string][]) =>
  triples.map(([label, value, order]) => ({ label, value, order }));

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
    const over = [...at, { label: "g", value: 1, order: "" }];
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


describe("arranging the groups", () => {
  // Deliberately in neither size nor alphabetical order to start with, so a
  // sort that silently did nothing would fail rather than accidentally pass.
  const months = dated(
    ["Mar", 79, "2019-03-03"],
    ["Jun", 387, "2019-06-02"],
    ["Jan", 246, "2019-01-01"],
    ["Apr", 122, "2019-04-07"],
  );
  const labels = (list: { label: string }[]) => list.map((s) => s.label);

  it("ranks largest first", () => {
    expect(labels(arrange(months, "largest"))).toEqual(["Jun", "Jan", "Apr", "Mar"]);
  });

  it("ranks smallest first", () => {
    expect(labels(arrange(months, "smallest"))).toEqual(["Mar", "Apr", "Jan", "Jun"]);
  });

  it("sorts A-Z by label", () => {
    expect(labels(arrange(months, "label"))).toEqual(["Apr", "Jan", "Jun", "Mar"]);
  });

  it("puts months in real date order, which A-Z never would", () => {
    // This is the whole point of carrying an anchor: alphabetically, April
    // comes first, and that is not what a reader means by date order.
    expect(labels(arrange(months, "time"))).toEqual(["Jan", "Mar", "Apr", "Jun"]);
    expect(labels(arrange(months, "label"))[0]).toBe("Apr");
  });

  it("does not mutate what it was given", () => {
    const before = labels(months);
    arrange(months, "time");
    expect(labels(months)).toEqual(before);
  });

  it("sends a group with no place in time to the end", () => {
    // The folded "N more" slice is several groups at several times, so it has
    // no anchor and belongs last rather than first.
    const withFold = [...months, { label: "2 more", value: 10, order: "" }];
    expect(labels(arrange(withFold, "time")).at(-1)).toBe("2 more");
  });
});

describe("when date order is offered at all", () => {
  it("is offered when the groups carry dates", () => {
    expect(canOrderByTime(dated(["Jan", 1, "2019-01-01"], ["Feb", 2, "2019-02-01"]))).toBe(true);
  });

  it("is not offered for groups that are not points in time", () => {
    expect(canOrderByTime(slices(["Fashions Direct", 32], ["Lindseys", 13]))).toBe(false);
  });

  it("is still offered when only the folded slice lacks a date", () => {
    const withFold = [
      ...dated(["Jan", 1, "2019-01-01"], ["Feb", 2, "2019-02-01"]),
      { label: "3 more", value: 1, order: "" },
    ];
    expect(canOrderByTime(withFold)).toBe(true);
  });

  it("is not offered for a single dated group, which is not an order", () => {
    expect(canOrderByTime(dated(["Jan", 1, "2019-01-01"]))).toBe(false);
  });
});

describe("which groups get picked out", () => {
  it("ranks by value, not by the order they are drawn in", () => {
    // Sorted by date, the largest month is not the first drawn. Highlighting
    // "the first three" would then pick out January, February and March for no
    // reason at all.
    const byDate = dated(
      ["Jan", 10, "2019-01-01"],
      ["Feb", 90, "2019-02-01"],
      ["Mar", 20, "2019-03-01"],
      ["Apr", 80, "2019-04-01"],
    );
    // Feb (90) is rank 0, Apr (80) rank 1, Mar (20) rank 2, Jan (10) rank 3.
    expect(ranks(byDate)).toEqual([3, 0, 2, 1]);
  });

  it("ranks a variance by size, ignoring its direction", () => {
    // A large negative is a large number. Ranking by the signed value would
    // put the worst performer last and quietly grey it out.
    expect(ranks(slices(["a", -900], ["b", 100], ["c", -50]))).toEqual([0, 1, 2]);
  });

  it("gives every slice exactly one rank", () => {
    const got = ranks(slices(["a", 3], ["b", 1], ["c", 2], ["d", 4]));
    expect([...got].sort((x, y) => x - y)).toEqual([0, 1, 2, 3]);
  });

  it("picks out three, so a two-group split is not all-accent-and-nothing", () => {
    expect(LEADERS).toBe(3);
  });
});
