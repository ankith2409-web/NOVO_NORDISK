/**
 * Splitting a generated document into blocks.
 *
 * The companion to `marked.ts`, one level up: that splits a line into plain and
 * marked runs, this splits a document into headings, paragraphs, lists, tables
 * and code fences. Same reasoning for not reaching for a Markdown library --
 * this page ships as one inlined file, and a parser would be a dependency and
 * an HTML-injection surface bought for syntax that never appears.
 *
 * And it never appears because the input is not arbitrary Markdown: it is what
 * `generate/document.to_markdown` emits, which is a closed set. Counting the
 * lines of both documents for the Store Sales model gives headings at three
 * levels, paragraphs, one blockquote, unordered lists, pipe tables, fenced code
 * with a language, and hard line breaks written as two trailing spaces. That is
 * the whole grammar, so that is what this reads.
 *
 * The one thing it must never do is drop something it does not recognise. An
 * unknown line becomes a paragraph, which renders it as prose -- readable, if
 * plain. Silently swallowing a line would make this a document that differs
 * from the file, which is the one failure that would matter.
 */

export type Block =
  | { kind: "heading"; level: 1 | 2 | 3 | 4 | 5 | 6; text: string }
  | { kind: "paragraph"; lines: string[] }
  | { kind: "quote"; lines: string[] }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "code"; language: string; text: string }
  | { kind: "table"; head: string[]; rows: string[][] };

const HEADING = /^(#{1,6})\s+(.*)$/;
const FENCE = /^```(\w*)\s*$/;
const BULLET = /^[-*]\s+(.*)$/;
const NUMBER = /^\d+\.\s+(.*)$/;

/** A pipe row into cells, without the empty ones the outer pipes create. */
function cells(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((cell) => cell.trim());
}

/** `|---|:--:|` -- the separator that makes the row above it a header. */
function isRule(line: string): boolean {
  const parts = cells(line);
  return parts.length > 0 && parts.every((cell) => /^:?-{1,}:?$/.test(cell));
}

export function splitBlocks(source: string): Block[] {
  // `\r\n` is what a document downloaded and pasted back through Windows
  // arrives as, and a stray `\r` at the end of every line would defeat every
  // pattern here at once.
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let at = 0;

  while (at < lines.length) {
    const line = lines[at];

    if (line.trim() === "") {
      at += 1;
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      // Everything to the closing fence, verbatim -- blank lines included,
      // because a DAX expression's blank lines are its formatting. An unclosed
      // fence runs to the end of the document rather than being abandoned,
      // which is what keeps a truncated file readable instead of empty.
      const body: string[] = [];
      at += 1;
      while (at < lines.length && !FENCE.test(lines[at])) {
        body.push(lines[at]);
        at += 1;
      }
      at += 1;
      blocks.push({ kind: "code", language: fence[1], text: body.join("\n") });
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({
        kind: "heading",
        level: heading[1].length as 1 | 2 | 3 | 4 | 5 | 6,
        text: heading[2].trim(),
      });
      at += 1;
      continue;
    }

    if (line.startsWith("|") && at + 1 < lines.length && isRule(lines[at + 1])) {
      const head = cells(line);
      const rows: string[][] = [];
      at += 2;
      while (at < lines.length && lines[at].startsWith("|")) {
        rows.push(cells(lines[at]));
        at += 1;
      }
      blocks.push({ kind: "table", head, rows });
      continue;
    }

    if (line.startsWith(">")) {
      const body: string[] = [];
      while (at < lines.length && lines[at].startsWith(">")) {
        body.push(lines[at].replace(/^>\s?/, ""));
        at += 1;
      }
      blocks.push({ kind: "quote", lines: body });
      continue;
    }

    const bullet = BULLET.exec(line);
    const number = NUMBER.exec(line);
    if (bullet || number) {
      const ordered = Boolean(number);
      const items: string[] = [];
      while (at < lines.length) {
        const match = ordered ? NUMBER.exec(lines[at]) : BULLET.exec(lines[at]);
        if (!match) break;
        items.push(match[1]);
        at += 1;
      }
      blocks.push({ kind: "list", ordered, items });
      continue;
    }

    // A paragraph runs to the next blank line or to the start of any other
    // block. Its lines are kept separate rather than joined, because the
    // document uses two trailing spaces as a hard break -- the header block of
    // every document is five such lines, and joining them would run the source
    // path, the date and the counts into one sentence.
    // The first line is always taken. Every other block has already declined
    // it, so refusing it here would advance nothing and loop forever -- which
    // is what a lone `|` row did, since the table branch needs a rule line
    // under it and the paragraph guard below rejects anything starting `|`.
    const body: string[] = [line.trimEnd()];
    at += 1;
    while (at < lines.length) {
      const next = lines[at];
      if (
        next.trim() === "" ||
        HEADING.test(next) ||
        FENCE.test(next) ||
        next.startsWith(">") ||
        next.startsWith("|") ||
        BULLET.test(next) ||
        NUMBER.test(next)
      ) {
        break;
      }
      body.push(next.trimEnd());
      at += 1;
    }
    blocks.push({ kind: "paragraph", lines: body });
  }

  return blocks;
}
