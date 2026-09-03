/**
 * Reading a generated document back into blocks.
 *
 * The cases here are taken from what `generate/document.to_markdown` actually
 * emits, not from the Markdown spec: the parser only has to be right about the
 * grammar those documents use, and being right about that is checkable against
 * the real files.
 *
 * The rule that matters most is the last one. Whatever this fails to
 * recognise must still appear, because a preview that silently drops a line is
 * a preview that disagrees with the file.
 */

import { describe, expect, it } from "vitest";
import { splitBlocks, type Block } from "./blocks";

const kinds = (blocks: Block[]) => blocks.map((b) => b.kind);

describe("headings", () => {
  it("reads the three levels the documents use", () => {
    const blocks = splitBlocks("# One\n\n## Two\n\n### Three");
    expect(blocks).toEqual([
      { kind: "heading", level: 1, text: "One" },
      { kind: "heading", level: 2, text: "Two" },
      { kind: "heading", level: 3, text: "Three" },
    ]);
  });

  it("does not mistake a hash inside a sentence for a heading", () => {
    expect(kinds(splitBlocks("Batch #4 failed."))).toEqual(["paragraph"]);
  });
});

describe("paragraphs", () => {
  it("keeps hard-broken lines apart", () => {
    // Every document opens with five of these. Joining them would run the
    // source path, the date and the counts into one sentence.
    const head = "**Source model:** `x.pbix`  \n**Generated:** 2026-09-03  \n**Requirements:** 69";
    const [block] = splitBlocks(head);
    expect(block).toEqual({
      kind: "paragraph",
      lines: ["**Source model:** `x.pbix`", "**Generated:** 2026-09-03", "**Requirements:** 69"],
    });
  });

  it("ends a paragraph at the next block, not only at a blank line", () => {
    expect(kinds(splitBlocks("Some prose.\n## Next"))).toEqual(["paragraph", "heading"]);
  });
});

describe("code", () => {
  it("keeps a fenced expression verbatim, blank lines and all", () => {
    const source = "```dax\nVAR a = 1\n\nRETURN a\n```";
    expect(splitBlocks(source)).toEqual([
      { kind: "code", language: "dax", text: "VAR a = 1\n\nRETURN a" },
    ]);
  });

  it("does not read the markup inside a fence as markup", () => {
    // An M query holds `#(lf)` and pipes, and a SQL query holds hashes.
    const source = "```m\n# not a heading\n| not | a table |\n```";
    const [block] = splitBlocks(source);
    expect(block).toEqual({
      kind: "code",
      language: "m",
      text: "# not a heading\n| not | a table |",
    });
  });

  it("runs an unclosed fence to the end rather than abandoning it", () => {
    // A truncated file should still be readable, not blank.
    const [block] = splitBlocks("```sql\nSELECT 1");
    expect(block).toEqual({ kind: "code", language: "sql", text: "SELECT 1" });
  });
});

describe("tables", () => {
  it("reads a pipe table into a head and rows", () => {
    const source = "| Table | Rows |\n| --- | ---: |\n| Sales | 923,371 |\n| Store | 104 |";
    expect(splitBlocks(source)).toEqual([
      {
        kind: "table",
        head: ["Table", "Rows"],
        rows: [
          ["Sales", "923,371"],
          ["Store", "104"],
        ],
      },
    ]);
  });

  it("needs the rule line, so a lone pipe row stays prose", () => {
    expect(kinds(splitBlocks("| this is not a table"))).toEqual(["paragraph"]);
  });
});

describe("lists and quotes", () => {
  it("reads a bulleted list", () => {
    expect(splitBlocks("- one\n- two")).toEqual([
      { kind: "list", ordered: false, items: ["one", "two"] },
    ]);
  });

  it("reads a numbered list as ordered", () => {
    expect(splitBlocks("1. first\n2. second")).toEqual([
      { kind: "list", ordered: true, items: ["first", "second"] },
    ]);
  });

  it("reads the standing note at the top of every document", () => {
    expect(splitBlocks("> Every requirement below was derived.")).toEqual([
      { kind: "quote", lines: ["Every requirement below was derived."] },
    ]);
  });
});

describe("nothing is dropped", () => {
  it("renders an unrecognised line as prose rather than swallowing it", () => {
    const odd = "~~~ not a fence we know ~~~";
    expect(splitBlocks(odd)).toEqual([{ kind: "paragraph", lines: [odd] }]);
  });

  it("survives Windows line endings", () => {
    expect(kinds(splitBlocks("# One\r\n\r\nprose\r\n"))).toEqual(["heading", "paragraph"]);
  });

  it("returns nothing for nothing, rather than an empty paragraph", () => {
    expect(splitBlocks("")).toEqual([]);
    expect(splitBlocks("\n\n  \n")).toEqual([]);
  });
});

describe("termination", () => {
  it("consumes every line, so no input can loop forever", () => {
    // A lone `|` row is the case that did: the table branch declines it for
    // want of a rule line, and the paragraph guard rejects anything starting
    // with a pipe, so nothing consumed it.
    for (const odd of ["| lone", "> ", "|", "#", "```"]) {
      expect(() => splitBlocks(odd)).not.toThrow();
      expect(splitBlocks(odd).length).toBeGreaterThanOrEqual(0);
    }
  });
});
