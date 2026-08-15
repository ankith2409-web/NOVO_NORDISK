/**
 * The inline-markup rule `RichText` implements.
 *
 * Extracted as a pure function so it can be tested without rendering: what
 * matters is the split, not the span it ends up in. The rule was `**bold**`
 * only until the copilot started writing into the same component -- a language
 * model quotes DAX in backticks, and every answer that quoted an expression
 * had punctuation running through the middle of it.
 */
import { describe, expect, it } from "vitest";
import { splitMarked } from "./marked";

describe("splitMarked", () => {
  it("marks bold spans and drops the asterisks", () => {
    expect(splitMarked("The **OOS Rate** measure")).toEqual([
      { text: "The ", marked: false },
      { text: "OOS Rate", marked: true },
      { text: " measure", marked: false },
    ]);
  });

  it("marks backtick spans, which is what a model actually writes", () => {
    expect(splitMarked("computed as `DIVIDE([a], [b])` exactly")).toEqual([
      { text: "computed as ", marked: false },
      { text: "DIVIDE([a], [b])", marked: true },
      { text: " exactly", marked: false },
    ]);
  });

  it("handles both in one string without one eating the other", () => {
    expect(splitMarked("**Rate** is `DIVIDE(x, y)`")).toEqual([
      { text: "Rate", marked: true },
      { text: " is ", marked: false },
      { text: "DIVIDE(x, y)", marked: true },
    ]);
  });

  it("leaves unmatched punctuation alone rather than swallowing the rest", () => {
    // A single stray backtick or asterisk must not turn the remainder of an
    // answer into one giant marked span.
    expect(splitMarked("a ` b")).toEqual([{ text: "a ` b", marked: false }]);
    expect(splitMarked("2 ** 3 is 8")).toEqual([{ text: "2 ** 3 is 8", marked: false }]);
  });

  it("keeps an empty marker as literal text", () => {
    expect(splitMarked("``")).toEqual([{ text: "``", marked: false }]);
  });

  it("preserves newlines, which the answer relies on for its layout", () => {
    expect(splitMarked("one\n**two**\nthree")).toEqual([
      { text: "one\n", marked: false },
      { text: "two", marked: true },
      { text: "\nthree", marked: false },
    ]);
  });

  it("returns plain text untouched", () => {
    expect(splitMarked("nothing marked here")).toEqual([
      { text: "nothing marked here", marked: false },
    ]);
  });
});
