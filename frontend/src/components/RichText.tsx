/**
 * Requirement statements carry `**object names**` in bold.
 *
 * The emphasis is not decoration -- it marks which words are names of real
 * things in the model, which is exactly what a reader scanning a requirement
 * needs to pick out. Rendering the asterisks literally loses that; stripping
 * them loses it too, silently. So the marked spans are rendered as what they
 * are: object names, in the same monospace face used for object names
 * everywhere else in the interface.
 *
 * Deliberately not a Markdown library. The only construct the generator emits
 * is `**…**`, and a full parser would be a dependency, a bundle cost, and an
 * HTML-injection surface in exchange for handling syntax that never appears.
 */
import type { ReactNode } from "react";

const BOLD = /\*\*(.+?)\*\*/g;

export function RichText({ children }: { children: string }) {
  const parts: ReactNode[] = [];
  let cursor = 0;

  for (const match of children.matchAll(BOLD)) {
    const start = match.index ?? 0;
    if (start > cursor) parts.push(children.slice(cursor, start));
    parts.push(
      <span key={start} className="font-mono text-[0.92em] font-medium text-ink">
        {match[1]}
      </span>,
    );
    cursor = start + match[0].length;
  }
  if (cursor < children.length) parts.push(children.slice(cursor));

  return <>{parts.length > 0 ? parts : children}</>;
}
