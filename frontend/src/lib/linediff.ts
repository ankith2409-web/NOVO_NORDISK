/**
 * Which lines differ between two versions of a definition.
 *
 * Drift already proves *that* something changed -- two fingerprints over
 * canonicalised text, which is exact. What it could not do is say *where*, and
 * for a seven-line SWITCH whose only edit is `0.8` becoming `0.9` on line five,
 * "these two blocks differ" leaves the reader diffing by eye. That is the one
 * job this file has.
 *
 * It is presentation and nothing more. The fingerprint remains the sole
 * authority on whether a definition changed; these marks only direct attention
 * within a change already established. Getting the alignment wrong would
 * mis-highlight lines, never mis-report a change -- which is why an ordinary
 * longest-common-subsequence is enough here and no canonicalisation is done: it
 * is the text as written that a reader is looking at.
 */

export type LineTag = "same" | "added" | "removed";

export interface DiffLine {
  text: string;
  tag: LineTag;
}

/** Guard against the quadratic table on pathological input. */
const MAX_LINES = 400;

/**
 * Align two texts by line and tag each side.
 *
 * Returns both sides separately rather than one merged sequence, because the
 * view shows before and after next to each other and a unified diff would mean
 * rebuilding two columns out of one list.
 */
export function diffLines(
  before: string,
  after: string,
): { before: DiffLine[]; after: DiffLine[] } {
  const a = before.split("\n");
  const b = after.split("\n");

  if (a.length > MAX_LINES || b.length > MAX_LINES) {
    // Too large to align cheaply. Showing both sides unmarked is honest -- the
    // change itself is still reported, only the pointer within it is missing.
    return {
      before: a.map((text) => ({ text, tag: "same" })),
      after: b.map((text) => ({ text, tag: "same" })),
    };
  }

  // lcs[i][j] = length of the longest common subsequence of a[i:] and b[j:].
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0),
  );
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] =
        a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const left: DiffLine[] = [];
  const right: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      left.push({ text: a[i], tag: "same" });
      right.push({ text: b[j], tag: "same" });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      left.push({ text: a[i], tag: "removed" });
      i++;
    } else {
      right.push({ text: b[j], tag: "added" });
      j++;
    }
  }
  while (i < a.length) left.push({ text: a[i++], tag: "removed" });
  while (j < b.length) right.push({ text: b[j++], tag: "added" });

  return { before: left, after: right };
}

/** Whether marking anything would tell the reader more than plain text does. */
export function worthMarking(lines: { before: DiffLine[]; after: DiffLine[] }): boolean {
  const changed =
    lines.before.filter((line) => line.tag !== "same").length +
    lines.after.filter((line) => line.tag !== "same").length;
  const total = lines.before.length + lines.after.length;
  // Every line differing is the common case for a one-line definition and for a
  // rewrite, and marking all of them is noise wearing the costume of a diff.
  return changed > 0 && changed < total;
}
