/**
 * Splitting a string into plain runs and marked runs.
 *
 * Two inline constructs, both meaning "this is the name of a thing in the
 * model, or an expression -- not prose": `**bold**`, which the requirement
 * deriver emits, and `` `code` ``, which a language model writes when it
 * quotes DAX in a copilot answer. They render identically because they mean
 * the same thing to a reader.
 *
 * Deliberately not a Markdown parser. Two constructs, no nesting, no blocks.
 * A real parser would be a dependency, a bundle cost on a page that ships as
 * one inlined file, and an HTML-injection surface, in exchange for syntax that
 * never appears.
 *
 * Split out from the component so the rule can be tested directly: what has to
 * be right is where the boundaries fall, and asserting that through rendered
 * DOM would test React rather than this.
 */

export interface Run {
  text: string;
  marked: boolean;
}

//: `**…**` or `` `…` ``. One alternation rather than two passes, so neither
//: form can match inside the other's output. Both require a non-empty body,
//: which is what keeps a lone backtick or a `2 ** 3` from swallowing the rest
//: of the string.
const MARKED = /\*\*(.+?)\*\*|`([^`]+?)`/g;

export function splitMarked(text: string): Run[] {
  const runs: Run[] = [];
  let cursor = 0;

  for (const match of text.matchAll(MARKED)) {
    const start = match.index ?? 0;
    if (start > cursor) runs.push({ text: text.slice(cursor, start), marked: false });
    runs.push({ text: match[1] ?? match[2], marked: true });
    cursor = start + match[0].length;
  }
  if (cursor < text.length) runs.push({ text: text.slice(cursor), marked: false });

  return runs;
}
