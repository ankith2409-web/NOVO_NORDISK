/**
 * Object names and expressions, set apart from the prose around them.
 *
 * Requirement statements carry `**object names**` in bold. The emphasis is not
 * decoration -- it marks which words are names of real things in the model,
 * which is exactly what a reader scanning a requirement needs to pick out.
 * Rendering the asterisks literally loses that; stripping them loses it too,
 * silently. So a marked span is rendered as what it is: an object name, in the
 * same monospace face used for object names everywhere else in the interface.
 *
 * Backticks are handled for the same reason, and were added once this was
 * pointed at the copilot's answers. The deriver only ever emits `**…**`, but a
 * language model writes `` `DIVIDE([a], [b])` `` when it quotes an expression,
 * and rendering those backticks literally ran punctuation through the middle
 * of every answer that quoted any DAX -- which is most of them.
 *
 * The splitting rule lives in `lib/marked` so it can be tested without a DOM.
 */
import { splitMarked } from "@/lib/marked";

export function RichText({ children }: { children: string }) {
  const runs = splitMarked(children);

  return (
    <>
      {runs.map((run, index) => {
        if (run.marked) {
          return (
            <span key={index} className="font-mono text-[0.92em] font-medium text-ink">
              {run.text}
            </span>
          );
        }
        // Emphasis stays emphasis rather than becoming another object name:
        // the generated documents label a paragraph `*Rationale:*`, and
        // setting that in the object-name face would claim there is a measure
        // called Rationale.
        if (run.emphasis) return <em key={index}>{run.text}</em>;
        return run.text;
      })}
    </>
  );
}
