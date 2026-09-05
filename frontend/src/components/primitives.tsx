/**
 * The small pieces every view shares.
 *
 * The rule these encode: a state is never signalled by colour alone. Every
 * verdict and confidence chip carries its word too. Roughly one in twelve men
 * has some colour vision deficiency, audit packs get printed in monochrome, and
 * a projector in a bright room flattens the muted palette this tool uses on
 * purpose. A word costs a few pixels and survives all three.
 */
import { useEffect, useRef, useState } from "react";
import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";
import type { Confidence, Verdict } from "@/lib/api";
import { AlertIcon, InfoIcon, RetryIcon } from "@/components/icons";
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
 * The typeface is the thing that changed most recently, and it changed for a
 * reason worth writing down. Every control here used to be set in lowercase
 * 11px mono, which made the header read as a row of terminal switches rather
 * than as a product's chrome. Mono is doing real work in this interface --
 * it marks the things that are *literal*: a measure's name, a column, a
 * fingerprint, a figure whose digits line up. Spending it on "guide" and
 * "dark" as well blunted that distinction and dated the whole surface. So
 * controls are set in the UI sans, sentence case; mono stays for data.
 *
 * `min-h-8` on a pointer device, `min-h-11` once the primary input is coarse:
 * 44px is the touch target a finger needs, and 44px everywhere would make a
 * dense tool look like a phone app. The media query is the honest way to have
 * both.
 */
const BUTTON_TONE = {
  quiet:
    "border-hairline bg-surface text-muted hover:border-edge hover:bg-raised hover:text-ink active:bg-edge/40",
  //: For chrome that sits *on* a surface rather than in a form -- the header,
  //: a panel corner. No border until it is wanted, so a row of them reads as
  //: one group instead of five boxes.
  ghost:
    "border-transparent bg-transparent text-muted hover:bg-raised hover:text-ink active:bg-edge/40",
  selected: "border-accent/45 bg-accent-soft text-accent hover:border-accent/70",
  primary:
    "border-accent bg-accent text-ground shadow-[0_1px_2px_rgb(0_0_0/0.10)] hover:brightness-110 active:brightness-95 disabled:hover:brightness-100",
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
    "inline-flex items-center justify-center gap-1.5 rounded-md border",
    "text-[12px] font-medium leading-none tracking-[-0.005em] whitespace-nowrap",
    "cursor-pointer select-none",
    // Colour and background only. Never size, never position: a control that
    // moves under the cursor is a control that gets mis-clicked.
    "transition-[color,background-color,border-color,filter,box-shadow]",
    "duration-(--duration-feedback) ease-(--ease-standard)",
    // Stated here rather than left to the global outline rule, so a control
    // keeps its ring when it sits on a coloured ground.
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
    "focus-visible:ring-offset-1 focus-visible:ring-offset-ground",
    "disabled:cursor-not-allowed disabled:opacity-50",
    size === "icon"
      ? "size-8 p-0 pointer-coarse:size-11"
      : "min-h-8 px-2.5 py-1 pointer-coarse:min-h-11 pointer-coarse:px-3",
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

/**
 * A button that is only an icon, and therefore must say what it is.
 *
 * `label` is not optional. An icon-only control with no accessible name is
 * unusable with a screen reader and unguessable with a mouse, and making the
 * name a required argument is the only way to be sure one exists.
 */
export function IconButton({
  label,
  tone = "ghost",
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  tone?: keyof typeof BUTTON_TONE;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={controlClasses(tone, "icon", className)}
      {...rest}
    >
      {children}
    </button>
  );
}

/**
 * One choice from a few, shown as a row rather than as a dropdown.
 *
 * Every place this replaces had hand-rolled the same thing slightly
 * differently -- Visual/Tabular at the foot of a chart panel, the period
 * switch on the trend, the theme. A segmented control is right where the
 * options are few, mutually exclusive and worth seeing without a click, which
 * is exactly when a `<select>` is wrong: a dropdown hides the alternatives
 * behind an interaction and costs a second one to change your mind.
 *
 * `role="radiogroup"` rather than `tablist`, because these select a value;
 * they do not switch between panels of content.
 */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
  size = "sm",
  className,
}: {
  options: readonly { value: T; label: string; title?: string }[];
  value: T;
  onChange: (value: T) => void;
  /** What is being chosen. Required for the same reason `IconButton` requires one. */
  label: string;
  size?: "sm" | "xs";
  className?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cx(
        "inline-flex items-center gap-0.5 rounded-md border border-hairline bg-surface p-0.5",
        className,
      )}
    >
      {options.map((option) => {
        const on = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={on}
            title={option.title}
            onClick={() => onChange(option.value)}
            className={cx(
              "cursor-pointer rounded-[5px] font-medium whitespace-nowrap",
              "transition-[color,background-color] duration-(--duration-feedback)",
              "ease-(--ease-standard) focus-visible:outline-none",
              "focus-visible:ring-2 focus-visible:ring-accent/60",
              size === "xs"
                ? "px-2 py-[3px] text-[10.5px]"
                : "px-2.5 py-1 text-[11.5px] pointer-coarse:min-h-9",
              on
                ? "bg-accent text-ground shadow-[0_1px_2px_rgb(0_0_0/0.10)]"
                : "text-muted hover:bg-raised hover:text-ink",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * The affordance that lets a paragraph become a sentence.
 *
 * Several places here carry three or four lines explaining how a figure was
 * produced. That explanation is load-bearing -- it is most of what makes a
 * number checkable, and deleting it would be deleting the product's argument.
 * But printed in full beside the figure it competes with the figure, and a
 * reader who has read it once reads past it forever after.
 *
 * So it moves behind an (i) and stays one keystroke away. Deliberately *not*
 * a tooltip: this is prose a reader may want to sit with, and a bubble that
 * vanishes when the pointer drifts is not somewhere you can read a paragraph.
 * It opens on click, closes on Escape, on a click outside, and on a second
 * click of the button.
 */
export function Info({
  label,
  children,
  align = "left",
}: {
  /** What the note is about, e.g. "how these figures were produced". */
  label: string;
  children: ReactNode;
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const host = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const onDown = (event: MouseEvent) => {
      if (!host.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  return (
    <span ref={host} className="relative inline-flex align-middle">
      <button
        type="button"
        onClick={() => setOpen((was) => !was)}
        aria-expanded={open}
        aria-label={open ? `Hide ${label}` : `About ${label}`}
        title={`About ${label}`}
        className={cx(
          "inline-flex size-[18px] cursor-pointer items-center justify-center rounded-full",
          "transition-colors duration-(--duration-feedback) ease-(--ease-standard)",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
          open ? "bg-accent-soft text-accent" : "text-faint hover:bg-raised hover:text-muted",
        )}
      >
        <InfoIcon size={13} />
      </button>
      {open && (
        <span
          role="note"
          className={cx(
            "absolute top-[22px] z-30 w-80 max-w-[min(20rem,calc(100vw-2rem))]",
            "max-h-[60vh] overflow-y-auto rounded-md border border-edge bg-surface",
            "p-3 text-[12px] leading-relaxed font-normal text-muted normal-case",
            "shadow-[0_8px_24px_rgb(0_0_0/0.14)]",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          {children}
        </span>
      )}
    </span>
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
  retrying = false,
}: {
  message: string;
  hint?: string[];
  status?: number;
  /** What was being loaded, for the fallback title: "Could not load …". */
  what?: string;
  onRetry?: () => void;
  /** True while a retry is in flight, so the button can say so. */
  retrying?: boolean;
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
            // Disabled while in flight so a second click cannot queue a second
            // attempt, and labelled so the wait is visibly the button's doing
            // rather than nothing having happened.
            <Button
              onClick={onRetry}
              disabled={retrying}
              aria-busy={retrying}
              tone={shown.tone === "bad" ? "bad" : "review"}
              className="mt-2.5"
            >
              <RetryIcon
                size={11}
                className={cx(
                  "flex-none",
                  retrying && "motion-safe:animate-spin",
                )}
              />
              {retrying ? "Trying…" : shown.retryLabel}
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
