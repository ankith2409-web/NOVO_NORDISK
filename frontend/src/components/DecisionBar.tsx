/**
 * The three verdicts on one requirement, as controls rather than as data.
 *
 * What was here before was three identical outline chips reading `accepted`,
 * `rejected`, `corrected` -- the stored enum values, rendered directly. Three
 * problems came with that, and none of them were cosmetic.
 *
 * The labels were past participles, so they read as the state a thing is in
 * rather than as something you can do: a button saying "accepted" invites the
 * reading "this is accepted", which is the opposite of what clicking it means.
 * They are verbs now.
 *
 * All three carried equal visual weight, so a queue whose whole purpose is
 * "most of these are fine, a few are not" gave no hint which was which.
 * Accepting is the ordinary outcome and is now the filled control; rejecting
 * and correcting are exceptions and sit quieter, in their own group behind a
 * separator.
 *
 * And nothing distinguished them at a glance, in a row where the words differ
 * only in their middle. Each carries its own silhouette now -- tick, cross,
 * pencil -- which is what actually gets read when someone is moving down a
 * list.
 *
 * The keyboard shortcuts are the reason this is a component and not three
 * buttons: a reviewer working a queue of forty statements should not have to
 * reach for the mouse for the common case. A/R/C act on the card the keyboard
 * is already inside, so the binding is scoped to the row rather than to the
 * window -- a global shortcut would fire on whichever card happened to be
 * first, which on a decision log is a genuinely bad failure.
 */
import { useEffect, useRef } from "react";
import { Button } from "@/components/primitives";
import { CheckIcon, CrossIcon, PencilIcon } from "@/components/icons";
import { cx } from "@/lib/cx";

export type Verdict = "accepted" | "rejected" | "corrected";

const ACTIONS = [
  {
    verdict: "accepted" as const,
    label: "Accept",
    key: "a",
    Icon: CheckIcon,
    hint: "The statement is correct as written",
  },
  {
    verdict: "rejected" as const,
    label: "Reject",
    key: "r",
    Icon: CrossIcon,
    hint: "The statement is wrong — say why",
  },
  {
    verdict: "corrected" as const,
    label: "Correct",
    key: "c",
    Icon: PencilIcon,
    hint: "Replace the wording with your own",
  },
];

export function DecisionBar({
  busy,
  stale,
  decided,
  onDecide,
}: {
  /** True while this row's write is in flight. */
  busy: boolean;
  /** A decision exists but no longer covers the current definition. */
  stale: boolean;
  /** A decision stands and still applies. */
  decided: boolean;
  onDecide: (verdict: Verdict) => void;
}) {
  const row = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = row.current;
    if (!node) return;
    const onKey = (event: KeyboardEvent) => {
      // Never while a write is running, and never when the keystroke was meant
      // for a field: a reviewer typing "a" into the correction note must not
      // also file an acceptance.
      if (busy || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, [contenteditable]")) return;

      const action = ACTIONS.find((entry) => entry.key === event.key.toLowerCase());
      if (!action) return;
      event.preventDefault();
      onDecide(action.verdict);
    };
    node.addEventListener("keydown", onKey);
    return () => node.removeEventListener("keydown", onKey);
  }, [busy, onDecide]);

  return (
    <div
      ref={row}
      className="mt-2.5 flex flex-wrap items-center gap-1.5"
      role="group"
      aria-label="Record a decision on this statement"
    >
      {ACTIONS.map(({ verdict, label, key, Icon, hint }, index) => (
        <span key={verdict} className="flex items-center gap-1.5">
          {/* Accepting is the ordinary outcome; the other two assert the
              statement is wrong. The rule sets them apart as a group rather
              than leaving three peers in a row. */}
          {index === 1 && <span aria-hidden className="mx-0.5 h-4 w-px bg-hairline" />}
          <Button
            onClick={() => onDecide(verdict)}
            disabled={busy}
            tone={verdict === "accepted" ? "primary" : "quiet"}
            // Spelled out for a screen reader, which gets no help from the
            // icon and none from a one-word label either.
            aria-label={`${label}: ${hint}`}
            title={`${hint}  (${key.toUpperCase()})`}
            className={cx(
              "gap-1.5",
              verdict === "rejected" && "hover:border-bad/40 hover:text-bad hover:bg-bad-soft",
              verdict === "corrected" &&
                "hover:border-review/40 hover:text-review hover:bg-review-soft",
            )}
          >
            <Icon size={14} />
            {busy && verdict === "accepted" ? "Recording…" : label}
            {/* Shown, not hidden in a tooltip -- a shortcut nobody discovers
                is a shortcut nobody uses. Dropped on coarse pointers, where
                there is no keyboard to press it with. */}
            <kbd className="ml-0.5 hidden font-mono text-[10px] opacity-60 sm:inline pointer-coarse:sm:hidden">
              {key.toUpperCase()}
            </kbd>
          </Button>
        </span>
      ))}

      {stale && (
        <span className="ml-1 font-mono text-[11px] text-bad">
          re-decide: the definition moved
        </span>
      )}
      {decided && !stale && (
        <span className="ml-1 font-mono text-[11px] text-muted">
          recorded — deciding again replaces it
        </span>
      )}
    </div>
  );
}
