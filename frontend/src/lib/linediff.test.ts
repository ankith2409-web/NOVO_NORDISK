/**
 * Aligning two versions of a definition, line by line.
 *
 * This is presentation, and the tests are written knowing that: the fingerprint
 * remains the only authority on whether a definition changed, and a wrong
 * alignment here can mis-point attention but can never mis-report a change.
 *
 * What it must get right is the case it exists for. A seven-line SWITCH whose
 * only edit is `0.8` becoming `0.9` on line five has to mark line five and
 * nothing else -- marking all seven is the same as marking none, since a
 * reader is back to diffing by eye either way.
 */
import { describe, expect, it } from "vitest";

import { diffLines, worthMarking } from "./linediff";

const BEFORE = `SWITCH(
TRUE(),
ISBLANK([Enrollment Rate]), "No target set",
[Enrollment Rate] >= 1, "At or above target",
[Enrollment Rate] >= 0.8, "Approaching target",
"Behind target"
)`;

const AFTER = BEFORE.replace("0.8", "0.9");

describe("the case this exists for", () => {
  it("marks only the line that actually moved", () => {
    const { before, after } = diffLines(BEFORE, AFTER);

    const removed = before.filter((line) => line.tag === "removed");
    const added = after.filter((line) => line.tag === "added");

    expect(removed).toHaveLength(1);
    expect(added).toHaveLength(1);
    expect(removed[0].text).toContain("0.8");
    expect(added[0].text).toContain("0.9");
  });

  it("keeps every other line as context rather than dropping it", () => {
    // A diff that showed only the changed line would take the expression away
    // from the reader, and a DAX line means little without the ones around it.
    const { before } = diffLines(BEFORE, AFTER);
    expect(before.filter((line) => line.tag === "same")).toHaveLength(6);
  });
});

describe("what is worth marking at all", () => {
  it("marks nothing when the two are identical", () => {
    const lines = diffLines(BEFORE, BEFORE);
    expect(worthMarking(lines)).toBe(false);
  });

  it("declines to mark a total rewrite", () => {
    // Every line differing is the ordinary case for a one-line measure and for
    // a genuine rewrite. Highlighting all of it is noise wearing the costume
    // of a diff.
    const lines = diffLines("SUM(Sales[Amount])", "COUNTROWS(Batch)");
    expect(worthMarking(lines)).toBe(false);
  });

  it("marks a partial change", () => {
    expect(worthMarking(diffLines(BEFORE, AFTER))).toBe(true);
  });
});

describe("alignment", () => {
  it("reports an inserted line as added on one side only", () => {
    const { before, after } = diffLines("a\nc", "a\nb\nc");
    expect(before.map((l) => l.tag)).toEqual(["same", "same"]);
    expect(after.map((l) => l.tag)).toEqual(["same", "added", "same"]);
  });

  it("reports a deleted line as removed on one side only", () => {
    const { before, after } = diffLines("a\nb\nc", "a\nc");
    expect(before.map((l) => l.tag)).toEqual(["same", "removed", "same"]);
    expect(after.map((l) => l.tag)).toEqual(["same", "same"]);
  });

  it("does not treat a moved line as unchanged", () => {
    const { before, after } = diffLines("a\nb", "b\na");
    // One of the two has to be reported as moved; what must not happen is both
    // sides coming back all-same, which would claim the order did not change.
    const marked =
      before.some((l) => l.tag !== "same") || after.some((l) => l.tag !== "same");
    expect(marked).toBe(true);
  });

  it("keeps every original line on its own side", () => {
    // The alignment may say what it likes about which lines correspond, but it
    // may not invent or lose a line -- the panes show the real expressions.
    const { before, after } = diffLines(BEFORE, AFTER);
    expect(before.map((l) => l.text)).toEqual(BEFORE.split("\n"));
    expect(after.map((l) => l.text)).toEqual(AFTER.split("\n"));
  });
});

describe("input it must not fall over on", () => {
  it("handles an empty side", () => {
    const { before, after } = diffLines("", "SUM(x)");
    expect(before.every((l) => l.text === "")).toBe(true);
    expect(after[0].tag).toBe("added");
  });

  it("gives up on a very large expression rather than hanging", () => {
    // The alignment table is quadratic. Past the cap it returns both sides
    // unmarked, which is honest -- the change is still reported, only the
    // pointer inside it is missing.
    const huge = Array.from({ length: 500 }, (_, i) => `line ${i}`).join("\n");
    const lines = diffLines(huge, huge.replace("line 250", "line 250 changed"));
    expect(lines.before.every((l) => l.tag === "same")).toBe(true);
    expect(worthMarking(lines)).toBe(false);
  });
});
