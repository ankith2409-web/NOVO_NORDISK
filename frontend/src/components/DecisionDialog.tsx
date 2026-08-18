/**
 * Recording a rejection or a correction, with the statement still on screen.
 *
 * This replaced `window.prompt`, which was wrong here in ways that were not
 * cosmetic. A native prompt is one line, so a corrected requirement -- a full
 * sentence, often longer than the original -- had to be typed into a field that
 * showed a fraction of it. It hid the statement being corrected the moment it
 * opened, so the reviewer was recalling from memory the exact wording they were
 * meant to be fixing. It cannot be cancelled distinguishably from being
 * submitted empty. And several mobile browsers disable `prompt` outright, where
 * the button simply did nothing at all.
 *
 * What replaces it keeps the requirement, its rationale and its id visible
 * while the correction is written, because the whole point of the review queue
 * is that a person reads the statement rather than approving a row in a list.
 *
 * The two verdicts are deliberately not the same screen. Rejecting asks why --
 * that reason is what the next person reads instead of starting over.
 * Correcting asks for the replacement sentence, and shows the original directly
 * above the field so the two can be compared without leaving.
 */
import { useEffect, useRef, useState } from "react";
import { Button, Failure } from "@/components/primitives";
import { RichText } from "@/components/RichText";
import type { Requirement } from "@/lib/api";
import { cx } from "@/lib/cx";

export type Verdict = "rejected" | "corrected";

/** Everything the copy changes between the two verdicts, in one place. */
const WORDING = {
  rejected: {
    title: "Reject this statement",
    lead: "Say what is wrong with it. This is what the next person reads instead of starting the investigation again.",
    label: "Why is this wrong?",
    placeholder:
      "e.g. The relationship exists but is inactive, so this is not how the number is actually calculated.",
    confirm: "Reject",
    tone: "bad" as const,
  },
  corrected: {
    title: "Correct this statement",
    lead: "Write what it should say instead. Your wording replaces the generated sentence in the document.",
    label: "What should it say instead?",
    placeholder:
      "e.g. Deviations per Site shall count only deviations classified as major.",
    confirm: "Save correction",
    tone: "review" as const,
  },
};

export function DecisionDialog({
  requirement,
  verdict,
  busy,
  problem,
  onCancel,
  onConfirm,
}: {
  requirement: Requirement;
  verdict: Verdict;
  /** True while the write is in flight, so it cannot be submitted twice. */
  busy: boolean;
  /**
   * A write that failed, reported here rather than on the page behind: the
   * dialog stays open on failure so the note is not lost, and an explanation
   * rendered underneath it would be an explanation nobody can see.
   */
  problem?: { status: number; message: string } | null;
  onCancel: () => void;
  onConfirm: (note: string) => void;
}) {
  const [note, setNote] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);
  const wording = WORDING[verdict];
  const enough = note.trim().length > 0;

  useEffect(() => {
    // Focus goes to the field rather than the dialog: the reviewer has already
    // decided by the time this opens, and the only thing left is to type.
    field.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, busy]);

  function submit() {
    if (!enough || busy) return;
    onConfirm(note.trim());
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-ink/40 p-4"
      // Clicking away cancels, matching the intro dialog -- but never while a
      // write is in flight, where it would leave the reviewer unsure whether
      // the decision was recorded.
      onClick={() => !busy && onCancel()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="decision-title"
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-full w-full max-w-lg flex-col overflow-hidden rounded border border-hairline bg-ground shadow-xl animate-(--animate-rise)"
      >
        <header className="border-b border-hairline px-4 py-3">
          <h2 id="decision-title" className="font-serif text-lg font-semibold">
            {wording.title}
          </h2>
          <p className="mt-1 text-xs text-muted">{wording.lead}</p>
        </header>

        <div className="min-h-0 flex-1 overflow-auto">
          {/* The statement stays visible. `window.prompt` hid it, which meant
              correcting a sentence from memory. */}
          <section className="border-b border-hairline bg-surface px-4 py-3">
            <div className="flex items-baseline gap-2">
              <code className="font-mono text-[11px] text-faint">{requirement.id}</code>
              <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
                {verdict === "corrected" ? "current wording" : "the statement"}
              </span>
            </div>
            <p className="mt-1.5 text-sm text-ink">
              <RichText>{requirement.statement}</RichText>
            </p>
            <p className="mt-1.5 text-xs text-muted">
              <RichText>{requirement.rationale}</RichText>
            </p>
          </section>

          <section className="px-4 py-3">
            <label
              htmlFor="decision-note"
              className="block text-xs font-medium text-ink"
            >
              {wording.label}
            </label>
            <textarea
              id="decision-note"
              ref={field}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              onKeyDown={(event) => {
                // Enter alone inserts a newline: these are sentences, and
                // submitting on Enter would truncate half of them mid-thought.
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault();
                  submit();
                }
              }}
              rows={4}
              disabled={busy}
              placeholder={wording.placeholder}
              aria-describedby="decision-help"
              className={cx(
                "mt-1.5 w-full resize-y rounded border border-hairline bg-surface",
                "px-2.5 py-2 text-sm text-ink placeholder:text-faint",
                "disabled:cursor-not-allowed disabled:opacity-60",
              )}
            />
            <p id="decision-help" className="mt-1.5 text-[11px] text-faint">
              Recorded against this statement&rsquo;s fingerprint, so it stops applying by
              itself if the logic beneath it changes. {navigator.platform.includes("Mac") ? "⌘" : "Ctrl"}+Enter to save.
            </p>
            {problem && (
              <div className="mt-3">
                <Failure message={problem.message} status={problem.status} what="that decision" />
              </div>
            )}
          </section>
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-hairline px-4 py-3">
          <Button onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            // Disabled rather than accepting an empty note and failing on the
            // server: a rejection with no reason leaves the next reviewer
            // exactly where they started, which is the thing this queue exists
            // to prevent.
            disabled={!enough || busy}
            tone={wording.tone}
            title={enough ? undefined : "Add a note first"}
          >
            {busy ? "Saving…" : wording.confirm}
          </Button>
        </footer>
      </div>
    </div>
  );
}
