/**
 * What someone sees the first time they open this, and whenever they ask again.
 *
 * The interface is six views over one model, and nothing on screen says which
 * of them answers the question the person actually arrived with. Worse, the
 * single claim the whole thing rests on -- that every statement here was
 * derived from an object in the model and can be traced back to it -- is
 * invisible until someone happens to expand a requirement and find the
 * evidence. Stating it once, up front, is the difference between a document a
 * reviewer trusts and one they have to audit before they can use it.
 *
 * Shown once, remembered as dismissed, and reachable again from the header. A
 * tour that cannot be reopened is a tour nobody can check something against,
 * and the first run is exactly when someone is least able to absorb it.
 *
 * The figures in it are read from the model that is loaded, not written into
 * the copy. An onboarding panel quoting invented numbers would undercut the one
 * claim it exists to make.
 */
import { useEffect, useRef } from "react";
import type { Overview } from "@/lib/api";
import { Button } from "@/components/primitives";

export function Intro({
  overview,
  onClose,
}: {
  overview: Overview | null;
  onClose: () => void;
}) {
  const dismiss = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    dismiss.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const rows: [string, string][] = [
    [
      "Overview",
      overview
        ? `What was read: ${overview.user_tables} tables, ${overview.measures} measures, ` +
          `${overview.relationships} joins — and, just as deliberately, what was not.`
        : "What was read out of the model, and what was not.",
    ],
    [
      "Model",
      "Every table, measure and join, with the DAX behind each one and what breaks if you change it.",
    ],
    [
      "Requirements",
      "The BRD and FRD, derived from the model rather than typed. Each statement carries the objects it came from.",
    ],
    [
      "Drift",
      "What moved between two versions of a model, and which requirements it puts back in question.",
    ],
    [
      "Review",
      "Everything the tool was not confident enough to assert on its own. It is meant to be short, and it is meant to be read.",
    ],
    [
      "Copilot",
      "Ask about this model in plain English. Answers come from reading the graph, not from memory.",
    ],
  ];

  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-ink/30 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="intro-title"
        onClick={(event) => event.stopPropagation()}
        className="max-h-full w-full max-w-xl overflow-auto rounded border border-hairline bg-ground shadow-xl"
      >
        <header className="border-b border-hairline px-4 py-3">
          <h2 id="intro-title" className="font-serif text-lg font-semibold">
            {overview ? `${overview.model} — read, not summarised` : "Read, not summarised"}
          </h2>
          <p className="mt-1 text-sm text-muted">
            Every statement in here was derived from an object in this model and shows
            the object it came from. Where the model does not say enough to be sure,
            that is marked rather than smoothed over.
          </p>
        </header>

        <dl className="flex flex-col">
          {rows.map(([name, what]) => (
            <div
              key={name}
              className="grid grid-cols-[7.5rem_1fr] gap-3 border-b border-hairline px-4 py-2.5 last:border-b-0"
            >
              <dt className="font-mono text-xs text-accent">{name}</dt>
              <dd className="text-sm text-muted">{what}</dd>
            </div>
          ))}
        </dl>

        <footer className="flex items-center justify-between gap-3 border-t border-hairline px-4 py-3">
          <p className="font-mono text-[11px] text-faint">
            Reopen any time from “guide” in the header
          </p>
          <Button ref={dismiss} onClick={onClose} tone="primary">
            start
          </Button>
        </footer>
      </div>
    </div>
  );
}
