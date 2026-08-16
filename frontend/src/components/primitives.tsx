/**
 * The small pieces every view shares.
 *
 * The rule these encode: a state is never signalled by colour alone. Every
 * verdict and confidence chip carries its word too. Roughly one in twelve men
 * has some colour vision deficiency, audit packs get printed in monochrome, and
 * a projector in a bright room flattens the muted palette this tool uses on
 * purpose. A word costs a few pixels and survives all three.
 */
import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";
import type { Confidence, Verdict } from "@/lib/api";
import { AlertIcon, RetryIcon } from "@/components/icons";
import { present } from "@/lib/failures";
import { cx } from "@/lib/cx";

/**
 * The one button.
 *
 * Eighteen hand-rolled buttons had drifted into eighteen slightly different
 * paddings, hover rules and focus behaviours, and only one of them showed a
 * pointer cursor. That is the kind of inconsistency nobody reports and
 * everybody feels.
 *
 * `min-h-8` on a pointer device, `min-h-11` once the primary input is coarse:
 * 44px is the touch target a finger needs, and 44px everywhere would make a
 * dense tool look like a phone app. The media query is the honest way to have
 * both.
 */
const BUTTON_TONE = {
  quiet: "border-hairline text-muted hover:bg-raised hover:text-ink",
  selected: "border-accent/40 bg-accent-soft text-accent",
  primary:
    "border-accent bg-accent text-ground hover:brightness-110 disabled:hover:brightness-100",
  ok: "border-ok/40 text-ok hover:bg-ok-soft",
  review: "border-review/40 text-review hover:bg-review-soft",
  bad: "border-bad/40 text-bad hover:bg-bad-soft",
} as const;

/**
 * The button's look, without the button.
 *
 * Exported because a download has to be a real `<a download href>` -- that is
 * what lets the browser name the file and stream it to disk -- and an anchor
 * that looked hand-styled next to these controls would read as a different
 * kind of thing. One definition, two elements.
 */
export function controlClasses(
  tone: keyof typeof BUTTON_TONE = "quiet",
  size: "sm" | "icon" = "sm",
  className?: string,
): string {
  return cx(
    "inline-flex items-center justify-center gap-1.5 rounded border",
    "font-mono text-[11px] leading-none whitespace-nowrap",
    // Colour and background only. Never size, never position: a control
    // that moves under the cursor is a control that gets mis-clicked.
    "transition-[color,background-color,border-color,filter]",
    "duration-(--duration-feedback) ease-(--ease-standard)",
    "disabled:cursor-not-allowed disabled:opacity-50",
    size === "icon"
      ? "size-8 p-0 pointer-coarse:size-11"
      : "min-h-8 px-2 py-1 pointer-coarse:min-h-11 pointer-coarse:px-3",
    BUTTON_TONE[tone],
    className,
  );
}

export function Button({
  tone = "quiet",
  size = "sm",
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: keyof typeof BUTTON_TONE;
  size?: "sm" | "icon";
  // Declared rather than left to ride along in `...rest`. React 19 passes a
  // ref to a function component as an ordinary prop, so the spread happens to
  // work -- but the intro's focus trap depends on this landing on the real
  // DOM node, and a dependency that subtle should be written down.
  ref?: Ref<HTMLButtonElement>;
}) {
  return (
    <button className={controlClasses(tone, size, className)} {...rest}>
      {children}
    </button>
  );
}

const TONE = {
  ok: "bg-ok-soft text-ok",
  review: "bg-review-soft text-review",
  bad: "bg-bad-soft text-bad",
  accent: "bg-accent-soft text-accent",
  neutral: "bg-raised text-muted",
} as const;

export type Tone = keyof typeof TONE;

export function Chip({
  tone = "neutral",
  children,
  title,
}: {
  tone?: Tone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5",
        "font-mono text-[11px] leading-4 tracking-wide whitespace-nowrap",
        TONE[tone],
      )}
    >
      {children}
    </span>
  );
}

const VERDICT_TONE: Record<Verdict, Tone> = {
  consistent: "ok",
  review: "review",
  divergent: "bad",
};

/** Deliberately spelled out: "divergent" is the claim, the colour only echoes it. */
export function VerdictChip({ verdict }: { verdict: Verdict }) {
  return <Chip tone={VERDICT_TONE[verdict]}>{verdict}</Chip>;
}

const CONFIDENCE_TONE: Record<Confidence, Tone> = {
  high: "ok",
  medium: "review",
  low: "bad",
};

const CONFIDENCE_MEANING: Record<Confidence, string> = {
  high: "Stated outright by the model — the document can assert it.",
  medium: "Follows from a structural rule applied to a real object.",
  low: "Inferred from structure alone. Needs a person to confirm.",
};

export function ConfidenceChip({ level }: { level: Confidence }) {
  return (
    <Chip tone={CONFIDENCE_TONE[level]} title={CONFIDENCE_MEANING[level]}>
      {level}
    </Chip>
  );
}

/** A hash is only meaningful next to what it was taken over — see EvidenceDrawer. */
export function Fingerprint({ value, full }: { value: string; full?: string }) {
  return (
    <code
      title={full ? `SHA-256 · ${full}` : undefined}
      className="font-mono text-[11px] text-faint tabular"
    >
      {value}
    </code>
  );
}

export function Panel({
  title,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cx(
        "flex min-h-0 flex-col rounded border border-hairline bg-ground",
        className,
      )}
    >
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-hairline px-3.5 py-2">
          <h2 className="font-mono text-[11px] tracking-[0.1em] text-faint uppercase">
            {title}
          </h2>
          {actions}
        </header>
      )}
      <div className="min-h-0 flex-1 overflow-auto">{children}</div>
    </section>
  );
}

export function Stat({
  label,
  value,
  tone = "neutral",
  hint,
}: {
  label: string;
  value: number | string;
  tone?: Tone;
  hint?: string;
}) {
  const emphasis =
    tone === "bad" ? "text-bad" : tone === "review" ? "text-review" : "text-ink";
  return (
    <div className="rounded border border-hairline bg-ground px-3.5 py-3" title={hint}>
      <div className={cx("text-2xl leading-none font-semibold tabular", emphasis)}>
        {value}
      </div>
      <div className="mt-1.5 font-mono text-[10px] tracking-[0.09em] text-faint uppercase">
        {label}
      </div>
    </div>
  );
}

/** Failures arrive as values carrying a message meant for a person. Show it. */
/**
 * A failed request, presented as something a person can act on.
 *
 * Three things this does that a red box with a server string does not.
 *
 * It announces itself: `role="alert"` means a screen reader says the failure
 * when it appears, rather than leaving it to be discovered by someone
 * re-reading a page that looks unchanged.
 *
 * It offers a way out. "Error without recovery path" is the failure mode here
 * -- the reader is told the request failed and left with a page they must
 * reload by hand. `onRetry` re-runs the exact request that failed.
 *
 * And it distinguishes broken from refused. A crashed server is `bad`; a
 * mistyped measure name is `review`, because the tool is working correctly and
 * red would be a lie.
 */
export function Failure({
  message,
  hint,
  status = 500,
  what,
  onRetry,
}: {
  message: string;
  hint?: string[];
  status?: number;
  /** What was being loaded, for the fallback title: "Could not load …". */
  what?: string;
  onRetry?: () => void;
}) {
  const shown = present({ status, message, didYouMean: hint, what });
  const suggestions = shown.suggestions ?? [];

  return (
    <div
      role="alert"
      className={cx(
        "m-3 max-w-2xl rounded border px-3.5 py-3",
        "animate-(--animate-rise)",
        shown.tone === "bad"
          ? "border-bad/40 bg-bad-soft"
          : "border-review/40 bg-review-soft",
      )}
    >
      <div className="flex items-start gap-2.5">
        <AlertIcon
          size={15}
          className={cx(
            "mt-px flex-none",
            shown.tone === "bad" ? "text-bad" : "text-review",
          )}
        />
        <div className="min-w-0 flex-1">
          <p
            className={cx(
              "text-sm font-medium",
              shown.tone === "bad" ? "text-bad" : "text-review",
            )}
          >
            {shown.title}
          </p>

          {shown.detail && <p className="mt-1 text-xs text-muted">{shown.detail}</p>}

          {suggestions.length > 0 && (
            <p className="mt-2 text-xs text-muted">
              Did you mean{" "}
              {suggestions.map((name, index) => (
                <span key={name}>
                  {index > 0 && (index === suggestions.length - 1 ? " or " : ", ")}
                  <code className="font-mono text-[11.5px] text-ink">{name}</code>
                </span>
              ))}
              ?
            </p>
          )}

          {/* Selectable, because the point of showing a command is that someone
              runs it. A command they have to retype is one they mistype. */}
          {shown.instruction && (
            <code className="mt-2 block w-fit rounded border border-hairline bg-ground px-2 py-1 font-mono text-[11.5px] text-ink select-all">
              {shown.instruction}
            </code>
          )}

          {onRetry && shown.retryLabel && (
            <Button
              onClick={onRetry}
              tone={shown.tone === "bad" ? "bad" : "review"}
              className="mt-2.5"
            >
              <RetryIcon size={11} className="flex-none" />
              {shown.retryLabel}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Waiting, with the shape of what is coming.
 *
 * A line of text saying "Reading…" tells you nothing about how long or how
 * much, and the page jumps when the real content replaces it. Blocks in
 * roughly the proportion of the eventual rows hold the layout still and make
 * the wait legible.
 *
 * `aria-busy` and a live label carry the same information without sight; the
 * blocks themselves are `aria-hidden`, since a screen reader announcing eight
 * empty boxes is worse than silence.
 */
export function Loading({ what, rows = 3 }: { what: string; rows?: number }) {
  return (
    <div className="flex flex-col gap-2.5 p-4" aria-busy="true">
      <p className="sr-only" role="status">
        Reading {what}
      </p>
      <div aria-hidden="true" className="flex flex-col gap-2.5">
        {Array.from({ length: rows }, (_, index) => (
          <div
            key={index}
            className="animate-(--animate-shimmer) rounded border border-hairline bg-raised"
            style={{
              // Descending widths read as text rather than as a loading bar,
              // and the offset delay makes the group feel like one object
              // instead of several blinking independently.
              height: "3.25rem",
              opacity: 1 - index * 0.15,
              animationDelay: `${index * 90}ms`,
            }}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * Nothing to show, and why.
 *
 * An empty view that says only "no results" leaves the reader unsure whether
 * the tool failed, the filter is too narrow, or there is genuinely nothing
 * there. `hint` carries the difference, and `action` gets them out.
 */
export function Empty({
  children,
  hint,
  action,
}: {
  children: ReactNode;
  hint?: ReactNode;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="flex flex-col items-start gap-2 px-4 py-6">
      <p className="text-sm text-muted">{children}</p>
      {hint && <p className="max-w-prose text-xs text-faint">{hint}</p>}
      {action && (
        <Button onClick={action.onClick} className="mt-0.5">
          {action.label}
        </Button>
      )}
    </div>
  );
}
